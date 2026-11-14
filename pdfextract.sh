#!/bin/bash
# With no arguments, extract all investment statements; optionally extract one PDF.
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/.venv/bin/python" "$script_dir/scripts/extract_statements.py" "$@"
