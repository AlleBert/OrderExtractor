#!/bin/sh
set -eu
project="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project/.uv-cache}"
export UV_PROJECT_ENVIRONMENT="$project/.demo-venv"
exec uv run --locked --no-dev python -m order_extractor demo --data-dir "$project/.demo-data" "$@"
