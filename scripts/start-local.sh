#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "=== MoneyTrail Local Environment Startup ==="

# 1. Check required configuration files
if [ ! -f .env ]; then
  echo "Error: Missing runtime configuration file '.env'." >&2
  echo "Generate .env using .env.example as a template." >&2
  exit 1
fi

if [ ! -f .env.setup ]; then
  echo "Error: Missing setup configuration file '.env.setup'." >&2
  echo "Generate .env.setup using .env.example as a template." >&2
  exit 1
fi

# 2. Check Database Service
USE_DOCKER=false
if command -v docker >/dev/null 2>&1; then
  USE_DOCKER=true
fi

echo "Checking PostgreSQL database..."
if [ "${USE_DOCKER}" = true ]; then
  echo "Docker detected. Ensuring 'db' service is running..."
  docker compose --env-file .env.setup up -d db
  echo "Waiting for database readiness..."
  until docker compose --env-file .env.setup exec db pg_isready -U moneytrail_admin >/dev/null 2>&1; do
    sleep 1
  done
else
  # Native host check
  if ! uv run python -c "
import socket
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('127.0.0.1', 5432))
    s.close()
    print('Port 5432 is reachable')
except Exception as e:
    exit(1)
" >/dev/null 2>&1; then
    echo "Error: PostgreSQL is not reachable on 127.0.0.1:5432." >&2
    echo "Please start PostgreSQL or install Docker to run the database container." >&2
    exit 1
  fi
fi

# 3. Bootstrap and Migrate Schema
echo "Running database bootstrap and migrations..."
uv run --env-file .env --env-file .env.setup python -m moneytrail.db bootstrap --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL

# 4. Verify Local LLM Serving (Ollama)
OLLAMA_URL="$(grep '^MONEYTRAIL_OLLAMA_URL=' .env | cut -d'=' -f2- || echo 'http://127.0.0.1:11434')"
REQUIRED_MODEL="$(grep '^MONEYTRAIL_MODEL=' .env | cut -d'=' -f2- || echo 'qwen3:8b')"

echo "Checking Ollama connectivity at ${OLLAMA_URL}..."
if ! uv run python -c "
import urllib.request, json, sys
url = '${OLLAMA_URL}/api/tags'
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        data = json.loads(resp.read().decode())
        models = [m['name'] for m in data.get('models', [])]
        req_model = '${REQUIRED_MODEL}'
        if not any(req_model in m for m in models):
            print(f'Warning: Model {req_model} not found in Ollama models: {models}', file=sys.stderr)
            sys.exit(2)
except Exception as e:
    print(f'Error connecting to Ollama: {e}', file=sys.stderr)
    sys.exit(1)
"; then
  echo "Error: Ollama check failed. Please ensure Ollama is running ('ollama serve') and model '${REQUIRED_MODEL}' is pulled ('ollama pull ${REQUIRED_MODEL}')." >&2
  exit 1
fi
echo "Ollama and model '${REQUIRED_MODEL}' verified."

# 5. Check Frontend Build
if [ ! -f frontend/dist/index.html ]; then
  echo "Building frontend assets..."
  npm --prefix frontend run build
fi

# 6. Start Application
echo "Starting MoneyTrail application on http://127.0.0.1:8000..."
exec uv run --env-file .env uvicorn moneytrail.api:app --host 127.0.0.1 --port 8000
