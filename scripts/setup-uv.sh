#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' "error: uv is required; install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

uv python install 3.14
uv venv --clear --python 3.14 .venv
uv sync --locked --all-groups

uv run python - <<'PY'
import sys

import Products.CMFPlone
import zopyx.plone.persistentlogger

print(f"Python {sys.version.split()[0]}")
print("Plone and zopyx.plone.persistentlogger are importable")
PY

printf '%s\n' "uv environment is ready: $ROOT_DIR/.venv"
printf '%s\n' "Run tests with: uv run zope-testrunner --path . --package zopyx.plone.persistentlogger"
