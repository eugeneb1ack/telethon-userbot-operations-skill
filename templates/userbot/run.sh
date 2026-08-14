#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[error] Не найден интерпретатор venv: $PYTHON_BIN" >&2
  echo "Создайте окружение: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/main.py" "$@"
