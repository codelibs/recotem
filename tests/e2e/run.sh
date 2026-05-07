#!/usr/bin/env bash
# Recotem 2.0 end-to-end test script.
#
# Steps:
#   1. Generate a small synthetic CSV of interactions
#   2. Write a recipe pointing at it
#   3. recotem train -> artifact
#   4. recotem serve & (background)
#   5. curl /predict/{name} -> assert JSON shape
#   6. Cleanup
#
# Requirements: recotem installed, RECOTEM_SIGNING_KEYS set.
# Usage: bash tests/e2e/run.sh [--no-movielens]

set -euo pipefail

# --tutorial mode: train against the in-tree HTTPS-source tutorial recipe.
# Gated on RECOTEM_E2E_NETWORK to keep the offline default working.
TUTORIAL_MODE=0
if [[ "${1:-}" == "--tutorial" ]]; then
    if [[ -z "${RECOTEM_E2E_NETWORK:-}" ]]; then
        echo "Skipping --tutorial: RECOTEM_E2E_NETWORK not set"
        exit 0
    fi
    TUTORIAL_MODE=1
    shift
fi

WORKDIR="/tmp/recotem_e2e_$$"
ARTIFACTS_DIR="${WORKDIR}/artifacts"
RECIPE_NAME="e2e_test"
SERVE_PORT="18080"
SERVE_PID=""

cleanup() {
    echo "[e2e] Cleaning up..."
    if [ -n "$SERVE_PID" ]; then
        kill "$SERVE_PID" 2>/dev/null || true
        wait "$SERVE_PID" 2>/dev/null || true
    fi
    rm -rf "$WORKDIR"
    echo "[e2e] Cleanup done."
}
trap cleanup EXIT

mkdir -p "${WORKDIR}" "${ARTIFACTS_DIR}"

# ---------------------------------------------------------------------------
# 1. Generate signing key
# ---------------------------------------------------------------------------
echo "[e2e] Generating signing key..."
SIGNING_KEY_HEX=$(python3 -c "import os; print(os.urandom(32).hex())")
export RECOTEM_SIGNING_KEYS="e2e-key:${SIGNING_KEY_HEX}"

# ---------------------------------------------------------------------------
# 2. Generate synthetic CSV + recipe (default mode) OR use tutorial recipe
# ---------------------------------------------------------------------------
if [[ "${TUTORIAL_MODE}" == "1" ]]; then
    RECIPE="examples/tutorial-purchase-log/recipe.yaml"
    RECIPE_NAME="purchase_log"
    # The tutorial recipe writes to ./artifacts/purchase_log.recotem (CWD-relative).
    mkdir -p artifacts
else
    echo "[e2e] Generating synthetic interaction data..."
    python3 - <<PYEOF
import csv, random, os
random.seed(42)
n_users = 100
n_items = 50
n_rows = 2000
with open("${WORKDIR}/interactions.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["user_id", "item_id"])
    for _ in range(n_rows):
        writer.writerow([f"u{random.randint(0, n_users-1)}",
                         f"i{random.randint(0, n_items-1)}"])
PYEOF

    echo "[e2e] Writing recipe..."
    cat > "${WORKDIR}/recipe.yaml" <<RECIPE
name: ${RECIPE_NAME}
source:
  type: csv
  path: ${WORKDIR}/interactions.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 2
  split:
    scheme: random
    heldout_ratio: 0.1
    seed: 42
output:
  path: ${ARTIFACTS_DIR}/${RECIPE_NAME}.recotem
  versioning: always_overwrite
RECIPE

    RECIPE="${WORKDIR}/recipe.yaml"
fi

# ---------------------------------------------------------------------------
# 4. Train
# ---------------------------------------------------------------------------
echo "[e2e] Running recotem train..."
recotem train "${RECIPE}"
echo "[e2e] Training complete."

# Verify artifact exists
if [[ "${TUTORIAL_MODE}" == "1" ]]; then
    ARTIFACT_PATH="artifacts/${RECIPE_NAME}.recotem"
else
    ARTIFACT_PATH="${ARTIFACTS_DIR}/${RECIPE_NAME}.recotem"
fi
if [ ! -f "${ARTIFACT_PATH}" ]; then
    echo "[e2e] ERROR: artifact not found after training (expected: ${ARTIFACT_PATH})!"
    exit 1
fi

# ---------------------------------------------------------------------------
# 5. Serve
# ---------------------------------------------------------------------------
echo "[e2e] Starting recotem serve..."
mkdir -p "${WORKDIR}/recipes"
if [[ "${TUTORIAL_MODE}" == "1" ]]; then
    cp examples/tutorial-purchase-log/recipe.yaml "${WORKDIR}/recipes/${RECIPE_NAME}.yaml"
else
    cp "${WORKDIR}/recipe.yaml" "${WORKDIR}/recipes/${RECIPE_NAME}.yaml"
fi

export RECOTEM_ENV=test
recotem serve \
    --recipes "${WORKDIR}/recipes" \
    --port "${SERVE_PORT}" \
    --insecure-no-auth &
SERVE_PID=$!

echo "[e2e] Waiting for server to start (pid=${SERVE_PID})..."
MAX_WAIT=30
WAITED=0
while ! curl -sf "http://127.0.0.1:${SERVE_PORT}/health" > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "${WAITED}" -ge "${MAX_WAIT}" ]; then
        echo "[e2e] ERROR: server did not start within ${MAX_WAIT}s"
        exit 1
    fi
done
echo "[e2e] Server is up."

# ---------------------------------------------------------------------------
# 6. Health check
# ---------------------------------------------------------------------------
echo "[e2e] Checking /health..."
HEALTH=$(curl -sf "http://127.0.0.1:${SERVE_PORT}/health")
echo "[e2e] /health response: ${HEALTH}"

STATUS=$(echo "${HEALTH}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status','unknown'))")
if [ "${STATUS}" != "ok" ]; then
    echo "[e2e] ERROR: /health status is '${STATUS}', expected 'ok'"
    exit 1
fi

# ---------------------------------------------------------------------------
# 7. /predict call
# ---------------------------------------------------------------------------
echo "[e2e] Calling /predict/${RECIPE_NAME}..."
PREDICT=$(curl -sf \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"user_id": "u0", "cutoff": 5}' \
    "http://127.0.0.1:${SERVE_PORT}/predict/${RECIPE_NAME}")
echo "[e2e] /predict response: ${PREDICT}"

# Validate JSON shape: must have items, model, request_id
python3 - <<PYEOF
import sys, json
data = json.loads('''${PREDICT}''')
assert "items" in data, f"Missing 'items' key: {data}"
assert isinstance(data["items"], list), "items must be a list"
assert "model" in data, f"Missing 'model' key: {data}"
assert "request_id" in data, f"Missing 'request_id' key: {data}"
model = data["model"]
assert "recipe" in model, f"Missing 'recipe' in model: {model}"
assert model["recipe"] == "${RECIPE_NAME}", f"Wrong recipe name: {model['recipe']}"
print("[e2e] JSON shape validation: PASSED")
PYEOF

echo "[e2e] All checks passed!"
exit 0
