#!/usr/bin/env bash
# Validate everything under helm/, examples/k8s/ and compose.yaml.
#
# Nothing used to check these trees, and two HIGH defects shipped through the
# gap in a single release cycle:
#
#   * examples/k8s/ referenced `serviceAccountName: recotem` with no
#     ServiceAccount manifest in the directory — every pod would fail to
#     schedule;
#   * the Helm CronJob rendered bash-only syntax (`read -ra`, `<<<`, `${a[@]}`,
#     `${v// /}`) into a container whose command is `/bin/sh`, which is dash in
#     the python:3.12-slim image — the job died with a syntax error.
#
# Neither is a schema violation, so schema validation alone would have missed
# both.  This script therefore runs five distinct checks:
#
#   1. helm lint over a set of values permutations
#   2. helm template + kubeconform -strict on every rendered resource
#   3. kubeconform -strict over examples/k8s/
#   4. every rendered `/bin/sh -c` script parsed by a real POSIX shell (dash)
#   5. docker compose config -q
#
# Requirements: helm, kubeconform, yq, dash (or any strict POSIX sh), docker.
# Usage: bash .github/scripts/validate-manifests.sh

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="${REPO_ROOT}/helm/recotem"
K8S_EXAMPLES="${REPO_ROOT}/examples/k8s"
COMPOSE_FILE="${REPO_ROOT}/compose.yaml"

# Pinned so a schema-registry drift cannot silently change the verdict.
KUBE_VERSION="${KUBECONFORM_KUBERNETES_VERSION:-1.31.0}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

FAILURES=0
note_failure() {
    echo "::error::$1"
    FAILURES=$((FAILURES + 1))
}

require() {
    command -v "$1" > /dev/null 2>&1 || {
        echo "::error::required tool '$1' not found on PATH"
        exit 127
    }
}
require helm
require kubeconform
require yq

# The Helm CronJob's command is `/bin/sh -c`, and /bin/sh is dash in both the
# python:3.12-slim runtime image and on Debian/Ubuntu hosts.  bash in POSIX
# mode still accepts `<<<` and arrays, so checking with `sh` on a system where
# sh IS bash would pass a script that dies in the container.  Insist on dash.
if command -v dash > /dev/null 2>&1; then
    POSIX_SH="$(command -v dash)"
else
    echo "::error::dash not found — a bash-provided /bin/sh would not catch" \
         "bashisms.  Install dash (apt-get install -y dash / brew install dash)."
    exit 127
fi
echo "POSIX shell for command checks: ${POSIX_SH}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Every `serviceAccountName` in a manifest set must resolve to a ServiceAccount
# defined in that same set, or to the implicit `default` account.  Neither
# `kubeconform` nor `kubectl apply --dry-run` catches a dangling reference:
# the Deployment is admitted and only its Pods fail, at runtime.
check_service_accounts() {
    local label="$1" json_file="$2"
    local output
    if ! output="$(python3 - "${label}" "${json_file}" 2>&1 <<'PYEOF'
import json
import sys

label, path = sys.argv[1], sys.argv[2]
docs = [d for d in json.load(open(path, encoding="utf-8")) if isinstance(d, dict)]

defined = {
    (d.get("metadata") or {}).get("name")
    for d in docs
    if d.get("kind") == "ServiceAccount"
}
defined.add("default")


def referenced(node, out):
    if isinstance(node, dict):
        value = node.get("serviceAccountName")
        if isinstance(value, str):
            out.add(value)
        for child in node.values():
            referenced(child, out)
    elif isinstance(node, list):
        for child in node:
            referenced(child, out)


refs: set[str] = set()
referenced(docs, refs)

missing = sorted(r for r in refs if r not in defined)
if missing:
    raise SystemExit(
        f"{label}: serviceAccountName {missing} referenced but no matching "
        f"ServiceAccount is defined here (defined: {sorted(defined)}). "
        "Pods using it will fail to schedule."
    )
print(f"{label}: serviceAccountName references OK ({sorted(refs) or 'none'})")
PYEOF
    )"; then
        note_failure "${output}"
        return 1
    fi
    echo "${output}"
}

# Extract every `/bin/sh -c <script>` from a manifest set and parse each one
# with a real POSIX shell.  `-n` reads and parses without executing.
check_sh_commands() {
    local label="$1" json_file="$2" outdir="${WORK}/sh-$3"
    mkdir -p "${outdir}"
    python3 - "${json_file}" "${outdir}" <<'PYEOF'
import json
import pathlib
import sys

path, outdir = sys.argv[1], pathlib.Path(sys.argv[2])
docs = json.load(open(path, encoding="utf-8"))
count = 0


def walk(node, trail):
    global count
    if isinstance(node, dict):
        command = node.get("command")
        if (
            isinstance(command, list)
            and len(command) >= 3
            and isinstance(command[0], str)
            and command[0].rsplit("/", 1)[-1] == "sh"
            and command[1] == "-c"
            and isinstance(command[2], str)
        ):
            count += 1
            # Prefer the container name; fall back to the document path so a
            # failure in e.g. a lifecycle preStop hook is still locatable.
            name = node.get("name") or "-".join(trail[-3:]) or f"cmd{count}"
            (outdir / f"{count:02d}-{name}.sh").write_text(command[2], encoding="utf-8")
        for key, child in node.items():
            walk(child, trail + [str(key)])
    elif isinstance(node, list):
        for index, child in enumerate(node):
            walk(child, trail + [str(index)])


walk(docs, [])
PYEOF
    local scripts
    scripts=$(find "${outdir}" -name '*.sh' -type f | sort)
    if [ -z "${scripts}" ]; then
        echo "${label}: no /bin/sh -c commands rendered"
        return 0
    fi
    local script rc=0
    while IFS= read -r script; do
        if "${POSIX_SH}" -n "${script}" 2> "${script}.err"; then
            echo "${label}: $(basename "${script}") parses under $(basename "${POSIX_SH}")"
        else
            note_failure "${label}: $(basename "${script}") is not valid POSIX sh — the container runs it under /bin/sh (dash) and will die with a syntax error"
            sed 's/^/    /' "${script}.err"
            echo "    --- rendered script ---"
            sed 's/^/    /' "${script}"
            rc=1
        fi
    done <<< "${scripts}"
    return "${rc}"
}

# ---------------------------------------------------------------------------
# 1 + 2 + 4. Helm: lint, render, schema-validate, shell-check
# ---------------------------------------------------------------------------
VALUES_DIR="${WORK}/values"
mkdir -p "${VALUES_DIR}"

# Defaults (empty override).
: > "${VALUES_DIR}/00-defaults.yaml"

cat > "${VALUES_DIR}/01-train-list.yaml" <<'YAML'
train:
  enabled: true
  failOnBusy: true
  recipeFiles:
    - news.yaml
    - products.yaml
YAML

# Legacy comma-separated string form.  values.yaml still documents it as
# accepted-but-deprecated, so it must still render a script /bin/sh can run.
cat > "${VALUES_DIR}/02-train-legacy-string.yaml" <<'YAML'
train:
  enabled: true
  recipeFiles: "news.yaml,products.yaml"
YAML

# No recipeFiles: the CronJob globs the recipes directory instead.
cat > "${VALUES_DIR}/03-train-glob.yaml" <<'YAML'
train:
  enabled: true
  failOnBusy: true
  recipeFiles: []
YAML

cat > "${VALUES_DIR}/04-full-serve.yaml" <<'YAML'
replicaCount: 3
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: recotem.example.com
      paths:
        - path: /
          pathType: Prefix
hpa:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
pdb:
  enabled: true
  minAvailable: 2
networkPolicy:
  enabled: true
  ingressFromPodSelector:
    app.kubernetes.io/name: ingress-nginx
  kubeletCIDRs:
    - "10.0.0.0/8"
artifacts:
  enabled: true
YAML

cat > "${VALUES_DIR}/05-pvc-recipes.yaml" <<'YAML'
recipes:
  source: pvc
  pvc:
    claimName: recotem-recipes
artifacts:
  enabled: true
train:
  enabled: true
  recipeFiles:
    - news.yaml
YAML

cat > "${VALUES_DIR}/06-objectstore-recipes.yaml" <<'YAML'
recipes:
  source: objectStore
  objectStore:
    # Minimal spec, as documented in values.yaml: no `name`, no `volumeMounts`.
    initContainer:
      image: amazon/aws-cli:latest
      command: ["sh", "-c", "aws s3 sync s3://example-bucket/recipes /recipes"]
train:
  enabled: true
YAML

cat > "${VALUES_DIR}/07-managed-configmap.yaml" <<'YAML'
serviceAccount:
  create: false
  name: ""
recipes:
  source: configMap
  configMap:
    managed: true
    name: recotem-recipes
    data:
      news.yaml: |
        name: news_articles
        source:
          type: csv
          path: /artifacts/interactions.csv
        schema:
          user_column: user_id
          item_column: item_id
        output:
          path: /artifacts/news_articles.recotem
YAML

# The realistic objectStore spec: the sync container is named, and it mounts a
# scratch dir of its own alongside the chart's /recipes mount.  The templates
# used to emit `name:` and `volumeMounts:` a SECOND time under this spec; Go's
# YAML decoder is last-key-wins, so the operator's mounts were silently
# discarded.  kubeconform is the check that catches it ("key already set in
# map") — but only once a permutation supplies these keys, which none did.
# Mounts must name a volume the chart declares (recipes / artifacts / tmp /
# workspace); the chart has no extraVolumes hook.
cat > "${VALUES_DIR}/08-objectstore-operator-spec.yaml" <<'YAML'
recipes:
  source: objectStore
  objectStore:
    initContainer:
      name: s3-sync
      image: amazon/aws-cli:latest
      command: ["sh", "-c", "aws s3 sync s3://example-bucket/recipes /recipes"]
      volumeMounts:
        - name: tmp
          mountPath: /tmp
train:
  enabled: true
YAML

echo
echo "=== helm chart: lint + template + kubeconform + sh -n ==="
for values in "${VALUES_DIR}"/*.yaml; do
    name="$(basename "${values}" .yaml)"
    echo
    echo "--- permutation: ${name} ---"

    if ! helm lint "${CHART}" --values "${values}" --strict; then
        note_failure "helm lint failed for permutation '${name}'"
        continue
    fi

    rendered="${WORK}/${name}.yaml"
    if ! helm template recotem "${CHART}" \
            --values "${values}" \
            --namespace recotem > "${rendered}"; then
        note_failure "helm template failed for permutation '${name}'"
        continue
    fi

    if ! kubeconform \
            -strict \
            -summary \
            -kubernetes-version "${KUBE_VERSION}" \
            "${rendered}"; then
        note_failure "kubeconform -strict failed for permutation '${name}'"
    fi

    yq eval-all --output-format=json '[.]' "${rendered}" > "${WORK}/${name}.json"
    check_service_accounts "helm/${name}" "${WORK}/${name}.json" || true
    check_sh_commands "helm/${name}" "${WORK}/${name}.json" "${name}" || true
done

# ---------------------------------------------------------------------------
# 3. examples/k8s/
# ---------------------------------------------------------------------------
echo
echo "=== examples/k8s: kubeconform + reference checks ==="
mapfile -t EXAMPLE_FILES < <(find "${K8S_EXAMPLES}" -maxdepth 1 -name '*.yaml' | sort)
if [ "${#EXAMPLE_FILES[@]}" -eq 0 ]; then
    note_failure "no YAML manifests found under ${K8S_EXAMPLES}"
else
    printf '%s\n' "${EXAMPLE_FILES[@]}"
    if ! kubeconform \
            -strict \
            -summary \
            -kubernetes-version "${KUBE_VERSION}" \
            "${EXAMPLE_FILES[@]}"; then
        note_failure "kubeconform -strict failed for examples/k8s/"
    fi

    # examples/k8s/ is applied as a unit (`kubectl apply -f examples/k8s/`), so
    # cross-file references must resolve within the directory.
    yq eval-all --output-format=json '[.]' "${EXAMPLE_FILES[@]}" \
        > "${WORK}/k8s-examples.json"
    check_service_accounts "examples/k8s" "${WORK}/k8s-examples.json" || true
    check_sh_commands "examples/k8s" "${WORK}/k8s-examples.json" "k8s-examples" || true
fi

# ---------------------------------------------------------------------------
# 5. compose.yaml
# ---------------------------------------------------------------------------
echo
echo "=== compose.yaml: docker compose config ==="
# The tutorial file interpolates ${RECOTEM_SIGNING_KEYS} / ${RECOTEM_API_KEYS};
# supply placeholders so the run is quiet and the check is about structure.
if RECOTEM_SIGNING_KEYS="ci:placeholder" RECOTEM_API_KEYS="ci:placeholder" \
        docker compose -f "${COMPOSE_FILE}" config -q; then
    echo "compose.yaml: valid"
else
    note_failure "docker compose config rejected ${COMPOSE_FILE}"
fi

# ---------------------------------------------------------------------------
echo
if [ "${FAILURES}" -ne 0 ]; then
    echo "FAILED: ${FAILURES} manifest check(s) failed."
    exit 1
fi
echo "OK: all manifest checks passed."
