#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

LIVE_MODE=false
API_URL="http://127.0.0.1:8000"
BUNDLE_PATH="fixtures/demo.zip"
TOKEN=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --live)
      LIVE_MODE=true
      shift
      ;;
    --url)
      API_URL="$2"
      shift 2
      ;;
    --bundle)
      BUNDLE_PATH="$2"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--live] [--url URL] [--bundle BUNDLE] [--token TOKEN]" >&2
      exit 1
      ;;
  esac
done

echo "=== MoneyTrail Live API Smoke Test ==="
echo "Live mode: ${LIVE_MODE}"
echo "API URL: ${API_URL}"
echo "Bundle: ${BUNDLE_PATH}"

# 1. Resolve Token
if [ -z "${TOKEN}" ]; then
  if [ -f .env ]; then
    TOKEN="$(grep '^MONEYTRAIL_OWNER_TOKEN=' .env | cut -d'=' -f2- | tr -d '\"\r ' || true)"
  fi
fi

if [ -z "${TOKEN}" ]; then
  echo "Error: MONEYTRAIL_OWNER_TOKEN not found in .env and not passed via --token." >&2
  exit 1
fi

# 2. Check Provider if in --live mode
if [ "${LIVE_MODE}" = true ]; then
  OLLAMA_URL="http://127.0.0.1:11434"
  if [ -f .env ]; then
    OLLAMA_URL="$(grep '^MONEYTRAIL_OLLAMA_URL=' .env | cut -d'=' -f2- | tr -d '\"\r ' || echo 'http://127.0.0.1:11434')"
  fi
  REQUIRED_MODEL="qwen3:8b"
  if [ -f .env ]; then
    REQUIRED_MODEL="$(grep '^MONEYTRAIL_MODEL=' .env | cut -d'=' -f2- | tr -d '\"\r ' || echo 'qwen3:8b')"
  fi

  echo "Verifying live LLM provider at ${OLLAMA_URL} for model '${REQUIRED_MODEL}'..."
  if ! uv run python -c "
import urllib.request, json, sys
url = '${OLLAMA_URL}/api/tags'
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        data = json.loads(resp.read().decode())
        models = [m['name'] for m in data.get('models', [])]
        if not any('${REQUIRED_MODEL}' in m for m in models):
            print('Missing model', file=sys.stderr)
            sys.exit(2)
except Exception as e:
    print(f'Cannot reach Ollama: {e}', file=sys.stderr)
    sys.exit(1)
" >/dev/null 2>&1; then
    echo "Error: --live specified but Ollama provider is unavailable at ${OLLAMA_URL} with model '${REQUIRED_MODEL}'." >&2
    echo "Live mode strictly fails when provider is unavailable and never selects a test double." >&2
    exit 1
  fi
  echo "Live provider verified."
fi

# 3. Check / Start Server
BG_SERVER_PID=""
cleanup() {
  if [ -n "${BG_SERVER_PID}" ]; then
    echo "Stopping background API server (PID: ${BG_SERVER_PID})..."
    kill "${BG_SERVER_PID}" 2>/dev/null || true
    wait "${BG_SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

SERVER_READY=false
if curl -sf "${API_URL}/healthz" >/dev/null 2>&1; then
  echo "Existing API server detected and healthy at ${API_URL}."
  SERVER_READY=true
else
  echo "Starting background API server for smoke test..."
  uv run --env-file .env uvicorn moneytrail.api:app --host 127.0.0.1 --port 8000 > /tmp/moneytrail_smoke_server.log 2>&1 &
  BG_SERVER_PID=$!

  for i in {1..30}; do
    if curl -sf "${API_URL}/healthz" >/dev/null 2>&1; then
      SERVER_READY=true
      echo "API server started successfully (PID: ${BG_SERVER_PID})."
      break
    fi
    sleep 1
  done
fi

if [ "${SERVER_READY}" != true ]; then
  echo "Error: Could not reach API server at ${API_URL}." >&2
  if [ -f /tmp/moneytrail_smoke_server.log ]; then
    cat /tmp/moneytrail_smoke_server.log >&2
  fi
  exit 1
fi

# 4. Upload Case Bundle
if [ ! -f "${BUNDLE_PATH}" ]; then
  echo "Error: Bundle file not found at ${BUNDLE_PATH}." >&2
  exit 1
fi

echo "Uploading bundle '${BUNDLE_PATH}' to ${API_URL}/api/v1/cases/import..."
IMPORT_RESP=$(curl -s -X POST "${API_URL}/api/v1/cases/import" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "bundle=@${BUNDLE_PATH}")

echo "Import response received. Parsing..."
CASE_ID=$(uv run python -c "
import json, sys
data = json.loads('''${IMPORT_RESP}''')
if 'error' in data:
    print('Import Error:', data['error'], file=sys.stderr)
    sys.exit(1)
cid = data.get('case_id')
assert cid, 'Missing case_id in import result'
counts = data.get('record_counts', {})
print(f'Imported case_id={cid}, record_counts={counts}', file=sys.stderr)
print(cid)
")

echo "Case imported successfully. Case ID: ${CASE_ID}"

# 5. Ask Original Question
QUESTION="Why does the recorded gross sales amount differ from the received payments?"
INVESTIGATION_MODE="agent"
if [ "${LIVE_MODE}" = false ]; then
  INVESTIGATION_MODE="fixed"
fi

echo "Starting investigation run (mode: ${INVESTIGATION_MODE})..."
echo "Question: '${QUESTION}'"

RUN_INIT_RESP=$(curl -s -X POST "${API_URL}/api/v1/investigations" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"case_id\": \"${CASE_ID}\", \"question\": \"${QUESTION}\", \"mode\": \"${INVESTIGATION_MODE}\"}")

RUN_ID=$(uv run python -c "
import json, sys
data = json.loads('''${RUN_INIT_RESP}''')
if 'error' in data:
    print('Run Init Error:', data['error'], file=sys.stderr)
    sys.exit(1)
run_id = data.get('run_id')
assert run_id, f'Missing run_id in response: {data}'
print(run_id)
")

echo "Investigation started with Run ID: ${RUN_ID}"

# 6. Poll for completion via /api/v1/runs/{run_id}
echo "Polling for run completion (timeout: 240s)..."
START_TIME=$(date +%s)
TIMEOUT_SECONDS=240
STATE="running"
RESULT_JSON=""

while [ "${STATE}" = "running" ] || [ "${STATE}" = "pending" ]; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START_TIME))
  if [ ${ELAPSED} -ge ${TIMEOUT_SECONDS} ]; then
    echo "Error: Investigation timed out after ${TIMEOUT_SECONDS}s." >&2
    exit 1
  fi

  sleep 1
  POLL_RESP=$(curl -s -X GET "${API_URL}/api/v1/runs/${RUN_ID}" \
    -H "Authorization: Bearer ${TOKEN}")

  STATE=$(uv run python -c "
import json
data = json.loads('''${POLL_RESP}''')
print(data.get('state'))
")

  if [ "${STATE}" = "completed" ]; then
    RESULT_JSON="${POLL_RESP}"
    echo "Investigation completed in ${ELAPSED}s."
    break
  elif [ "${STATE}" = "failed" ] || [ "${STATE}" = "cancelled" ]; then
    echo "Investigation terminated with state: ${STATE}." >&2
    echo "${POLL_RESP}" >&2
    exit 1
  fi
done

# 7. Verify result integrity, facts, and citations
echo "Verifying result schema, deterministic facts, and grounding..."
VERIFY_OUTPUT=$(uv run python -c "
import json, sys, re
data = json.loads('''${RESULT_JSON}''')
res = data.get('result', {})
assert res, 'Missing result object in completed run'

# Outcome check
outcome = res.get('outcome')
assert outcome in ('explained', 'partial', 'insufficient'), f'Invalid outcome: {outcome}'
print(f'Outcome: {outcome}')

# Reconciliation check
recon = res.get('reconciliation', {})
assert recon, 'Missing reconciliation in result'
gross = recon.get('gross_paise')
received = recon.get('received_allocations_paise') if recon.get('received_allocations_paise') is not None else recon.get('received_paise')
refunds = recon.get('completed_refunds_paise') if recon.get('completed_refunds_paise') is not None else recon.get('refunds_paise')
fees = recon.get('assessed_fees_paise') if recon.get('assessed_fees_paise') is not None else recon.get('fees_paise')
pending = recon.get('documented_pending_paise') if recon.get('documented_pending_paise') is not None else recon.get('evidenced_pending_paise')
diff = recon.get('unexplained_difference_paise') if recon.get('unexplained_difference_paise') is not None else recon.get('reconciliation_diff_paise')
print(f'Reconciliation: gross={gross} received={received} refunds={refunds} fees={fees} pending={pending} diff={diff}')
assert gross == 24000000, f'Expected gross 24000000, got {gross}'
assert received == 18600000, f'Expected received 18600000, got {received}'
assert refunds == 1800000, f'Expected refunds 1800000, got {refunds}'
assert fees == 600000, f'Expected fees 600000, got {fees}'
assert pending == 3000000, f'Expected pending 3000000, got {pending}'
assert diff == 0, f'Expected diff 0, got {diff}'

# Facts verification
facts = res.get('selected_facts', [])
assert len(facts) > 0, 'selected_facts is empty'
money_facts = {f.get('fact_id'): f.get('value_paise') for f in facts if f.get('kind') == 'money'}
print(f'Money Facts: {money_facts}')

# Grounding check: verify claims do not have ungrounded currency amounts outside tokens
claims = res.get('claims', [])
assert len(claims) > 0, 'claims list is empty'
currency_pattern = re.compile(r'(?:INR|Rs\.?|₹|\$)\s*[0-9]+(?:\.[0-9]+)?')
for c in claims:
    tmpl = c.get('text_template', '')
    matches = currency_pattern.findall(tmpl)
    assert not matches, f'Ungrounded currency literal found in claim template: {matches}'
print(f'Grounding Verified: {len(claims)} claims checked, zero raw currency literals.')

# Citations check
citations = res.get('citations', [])
assert len(citations) > 0, 'citations list is empty'
sample_cit = citations[0]
print(f'Sample Citation: source_id={sample_cit.get(\"source_id\")}, locator={sample_cit.get(\"locator_id\")}')

# Output source_id and locator_id for segment retrieval
print(f'TARGET_SOURCE={sample_cit.get(\"source_id\")}')
print(f'TARGET_LOCATOR={sample_cit.get(\"locator_id\")}')
")

echo "${VERIFY_OUTPUT}"

TARGET_SOURCE=$(echo "${VERIFY_OUTPUT}" | grep '^TARGET_SOURCE=' | cut -d'=' -f2-)
TARGET_LOCATOR=$(echo "${VERIFY_OUTPUT}" | grep '^TARGET_LOCATOR=' | cut -d'=' -f2-)

# 8. Resolve source segment through API
echo "Resolving cited source segment through API: /api/v1/cases/${CASE_ID}/sources/${TARGET_SOURCE}/segments?locator_id=${TARGET_LOCATOR}..."
SOURCE_RESP=$(curl -s -X GET "${API_URL}/api/v1/cases/${CASE_ID}/sources/${TARGET_SOURCE}/segments?locator_id=${TARGET_LOCATOR}" \
  -H "Authorization: Bearer ${TOKEN}")

uv run python -c "
import json, sys
data = json.loads('''${SOURCE_RESP}''')
if 'error' in data:
    print('Source Read Error:', data['error'], file=sys.stderr)
    sys.exit(1)
locator = data.get('locator_id')
text = data.get('text', '')
media_type = data.get('media_type', '')
print(f'Source Resolved: locator={locator}, media_type={media_type}, length={len(text)} chars')
assert text, 'Empty segment text'
"

echo "=== Smoke Test PASSED Successfully ==="
