#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

if [[ ! -f ".env" ]]; then
  echo "ERROR: .env file not found at ${ROOT_DIR}/.env"
  echo "Create it first (example: cp .env.example .env)."
  exit 1
fi

# Export all variables from .env for this process.
set -a
source ".env"
set +a

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"
RELOAD_FLAG="${API_RELOAD:-true}"

if [[ "${RELOAD_FLAG}" == "true" ]]; then
  exec uv run uvicorn src.main:app --host "${HOST}" --port "${PORT}" --reload
else
  exec uv run uvicorn src.main:app --host "${HOST}" --port "${PORT}"
fi

