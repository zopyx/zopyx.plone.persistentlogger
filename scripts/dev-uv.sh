#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' "error: uv is required; install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

if [[ -e instance && ! -f instance/etc/zope.ini ]]; then
    printf '%s\n' "error: instance exists but instance/etc/zope.ini is missing" >&2
    exit 1
fi

if [[ ! -f instance/etc/zope.ini ]]; then
    password="${PLONE_INITIAL_PASSWORD:-admin-admin}"
    cat > instance.yaml <<EOF
# Local development only. Do not use this password in production.
default_context:
  initial_user_name: "admin"
  initial_user_password: "${password}"
  wsgi_listen: "127.0.0.1:8080"
  debug_mode: true
  verbose_security: false
  db_storage: "direct"
  environment:
    zope_i18n_compile_mo_files: true
EOF

    uvx --from cookiecutter cookiecutter \
        -f --no-input --config-file instance.yaml \
        gh:plone/cookiecutter-zope-instance
fi

printf '%s\n' "Starting Plone in foreground mode on http://127.0.0.1:8080"
printf '%s\n' "Press Ctrl-C to stop Plone."
exec uv run --python 3.14 runwsgi -v instance/etc/zope.ini
