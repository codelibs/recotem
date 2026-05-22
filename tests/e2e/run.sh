#!/usr/bin/env bash
# Recotem end-to-end test script.
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
    # The tutorial CSV uses numeric user_ids ("1", "2", ...) — match the
    # documented curl example in docs/getting-started.md.
    PREDICT_USER_ID="1"
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
    # Synthetic data above generates user IDs of the form "u0".."u99".
    PREDICT_USER_ID="u0"
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
while ! curl -sf "http://127.0.0.1:${SERVE_PORT}/v1/health" > /dev/null 2>&1; do
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
echo "[e2e] Checking /v1/health..."
HEALTH=$(curl -sf "http://127.0.0.1:${SERVE_PORT}/v1/health")
echo "[e2e] /v1/health response: ${HEALTH}"

STATUS=$(echo "${HEALTH}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status','unknown'))")
if [ "${STATUS}" != "ok" ]; then
    echo "[e2e] ERROR: /health status is '${STATUS}', expected 'ok'"
    exit 1
fi

# ---------------------------------------------------------------------------
# 7. /v1/recipes/{name}:recommend call
# ---------------------------------------------------------------------------
echo "[e2e] Calling /v1/recipes/${RECIPE_NAME}:recommend..."
PREDICT=$(curl -sf \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"user_id\": \"${PREDICT_USER_ID}\", \"limit\": 5}" \
    "http://127.0.0.1:${SERVE_PORT}/v1/recipes/${RECIPE_NAME}:recommend")
echo "[e2e] /v1/recipes/:recommend response: ${PREDICT}"

# Validate JSON shape: must have items, recipe, model_version, request_id
python3 - <<PYEOF
import sys, json
data = json.loads('''${PREDICT}''')
assert "items" in data, f"Missing 'items' key: {data}"
assert isinstance(data["items"], list), "items must be a list"
assert "recipe" in data, f"Missing 'recipe' key: {data}"
assert data["recipe"] == "${RECIPE_NAME}", f"Wrong recipe name: {data['recipe']}"
assert "request_id" in data, f"Missing 'request_id' key: {data}"
assert "model_version" in data, f"Missing 'model_version' key: {data}"
print("[e2e] JSON shape validation: PASSED")
PYEOF

# ---- 8. GET /v1/recipes ----
echo "[e2e] Calling GET /v1/recipes..."
RECIPES_LIST=$(curl -sf "http://127.0.0.1:${SERVE_PORT}/v1/recipes")
echo "[e2e] GET /v1/recipes response: ${RECIPES_LIST}"

python3 - <<PYEOF
import sys, json
data = json.loads('''${RECIPES_LIST}''')
assert "recipes" in data, f"Missing 'recipes' key: {data}"
assert isinstance(data["recipes"], list), "'recipes' must be a list"
names = [r["name"] for r in data["recipes"]]
assert "${RECIPE_NAME}" in names, f"Recipe '${RECIPE_NAME}' not found in list: {names}"
print("[e2e] GET /v1/recipes validation: PASSED")
PYEOF

# ---- 9. GET /v1/recipes/{name} ----
echo "[e2e] Calling GET /v1/recipes/${RECIPE_NAME}..."
RECIPE_DETAIL=$(curl -sf "http://127.0.0.1:${SERVE_PORT}/v1/recipes/${RECIPE_NAME}")
echo "[e2e] GET /v1/recipes/${RECIPE_NAME} response: ${RECIPE_DETAIL}"

python3 - <<PYEOF
import sys, json
data = json.loads('''${RECIPE_DETAIL}''')
for key in ("name", "model_version", "loaded_at", "kind", "supported_verbs"):
    assert key in data, f"Missing '{key}' key: {data}"
assert data["name"] == "${RECIPE_NAME}", f"Wrong name: {data['name']}"
assert isinstance(data["supported_verbs"], list), "'supported_verbs' must be a list"
assert len(data["supported_verbs"]) > 0, "'supported_verbs' must be non-empty"
print("[e2e] GET /v1/recipes/{name} validation: PASSED")
PYEOF

# ---- 10. Parse seed item_id from prior :recommend response ----
SEED_ITEM_ID=$(python3 - <<PYEOF
import sys, json
data = json.loads('''${PREDICT}''')
items = data.get("items", [])
if items:
    print(items[0]["item_id"])
else:
    # Fallback: item IDs in synthetic data are "i0".."i49"
    print("i0")
PYEOF
)
echo "[e2e] Using seed item_id='${SEED_ITEM_ID}' for :recommend-related"

# ---- 11. POST /v1/recipes/{name}:recommend-related ----
echo "[e2e] Calling /v1/recipes/${RECIPE_NAME}:recommend-related..."
RELATED=$(curl -sf \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"seed_items\": [\"${SEED_ITEM_ID}\"], \"limit\": 5}" \
    "http://127.0.0.1:${SERVE_PORT}/v1/recipes/${RECIPE_NAME}:recommend-related")
echo "[e2e] :recommend-related response: ${RELATED}"

python3 - <<PYEOF
import sys, json
data = json.loads('''${RELATED}''')
assert "items" in data, f"Missing 'items' key: {data}"
assert isinstance(data["items"], list), "'items' must be a list"
assert len(data["items"]) >= 1, "Expected at least one related item"
assert "recipe" in data, f"Missing 'recipe' key: {data}"
assert "model_version" in data, f"Missing 'model_version' key: {data}"
assert "request_id" in data, f"Missing 'request_id' key: {data}"
print("[e2e] :recommend-related validation: PASSED")
PYEOF

# ---- 12. POST /v1/recipes/{name}:batch-recommend ----
echo "[e2e] Calling /v1/recipes/${RECIPE_NAME}:batch-recommend..."
# Send two requests: one known user and one unknown user
BATCH=$(curl -sf \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"requests\": [{\"user_id\": \"${PREDICT_USER_ID}\", \"limit\": 3}, {\"user_id\": \"__definitely_unknown_user__\", \"limit\": 3}]}" \
    "http://127.0.0.1:${SERVE_PORT}/v1/recipes/${RECIPE_NAME}:batch-recommend")
echo "[e2e] :batch-recommend response: ${BATCH}"

python3 - <<PYEOF
import sys, json
data = json.loads('''${BATCH}''')
assert "results" in data, f"Missing 'results' key: {data}"
assert isinstance(data["results"], list), "'results' must be a list"
assert len(data["results"]) == 2, f"Expected 2 results, got {len(data['results'])}"
assert "recipe" in data, f"Missing 'recipe' key: {data}"
assert "model_version" in data, f"Missing 'model_version' key: {data}"
assert "request_id" in data, f"Missing 'request_id' key: {data}"

# First request (known user) should succeed
r0 = data["results"][0]
assert r0["index"] == 0, f"Expected index 0, got {r0['index']}"
assert r0["status"] == "ok", f"Expected status 'ok' for known user, got {r0['status']}"
assert isinstance(r0["items"], list), "items for known user must be a list"

# Second request (unknown user) should have error status
r1 = data["results"][1]
assert r1["index"] == 1, f"Expected index 1, got {r1['index']}"
assert r1["status"] == "error", f"Expected status 'error' for unknown user, got {r1['status']}"
assert r1["error"] is not None, "error field must be present for unknown user"
assert r1["error"]["code"] == "UNKNOWN_USER", f"Expected UNKNOWN_USER code, got {r1['error']['code']}"
print("[e2e] :batch-recommend validation: PASSED")
PYEOF

# ---- 13. X-Request-ID echo ----
echo "[e2e] Testing X-Request-ID echo via :recommend..."
TRACED=$(curl -sf \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-Request-ID: e2e-trace-001" \
    -d "{\"user_id\": \"${PREDICT_USER_ID}\", \"limit\": 3}" \
    "http://127.0.0.1:${SERVE_PORT}/v1/recipes/${RECIPE_NAME}:recommend")
echo "[e2e] X-Request-ID echo response: ${TRACED}"

python3 - <<PYEOF
import sys, json
data = json.loads('''${TRACED}''')
assert "request_id" in data, f"Missing 'request_id' key: {data}"
assert data["request_id"] == "e2e-trace-001", (
    f"Expected request_id 'e2e-trace-001', got {data['request_id']!r}"
)
print("[e2e] X-Request-ID echo validation: PASSED")
PYEOF

echo "[e2e] All checks passed!"
exit 0
