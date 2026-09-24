PYTHON ?= 3.14
UV ?= uv
PACKAGE := zopyx.plone.persistentlogger

# The canonical test run must never silently drop the RDBMS half of the
# suite, so a missing container runtime is a failure there.
REQUIRE_POSTGRES := ZOPYX_PERSISTENTLOGGER_REQUIRE_POSTGRES=1
TESTRUNNER := $(UV) run --python $(PYTHON) zope-testrunner --path . --package $(PACKAGE)

.PHONY: install dev reset-site dev-reset demo test test-no-postgres test-rdbms lint format-check typecheck audit build package-check docs

install:
	$(UV) sync --python $(PYTHON)

dev:
	./scripts/dev-uv.sh

reset-site:
	@if pgrep -f '[r]unwsgi.*instance/etc/zope.ini' >/dev/null; then \
		printf '%s\n' 'error: stop make dev before resetting the Plone site' >&2; \
		exit 1; \
	fi
	@rm -f instance/var/filestorage/Data.fs.lock
	$(UV) run --python $(PYTHON) zconsole run instance/etc/zope.conf \
		scripts/reset-plone-site.py

dev-reset: reset-site dev

# Local demo: create (or reuse) the demo Plone site and fill the Plone root
# object with a batch of demo audit entries. Uses the configured storage
# backend, so it also demonstrates the RDBMS backend once that is selected.
# Stop `make dev` first; run `make dev` afterwards to browse the result.
demo:
	@if pgrep -f '[r]unwsgi.*instance/etc/zope.ini' >/dev/null; then \
		printf '%s\n' 'error: stop make dev before building the demo site' >&2; \
		exit 1; \
	fi
	@if [ ! -f instance/etc/zope.conf ]; then \
		printf '%s\n' 'error: no local instance; run make dev once to create it' >&2; \
		exit 1; \
	fi
	@rm -f instance/var/filestorage/Data.fs.lock
	$(UV) run --python $(PYTHON) zconsole run instance/etc/zope.conf \
		scripts/demo-site.py
	@printf '%s\n' 'Demo entries created; start the server with make dev, then open'
	@printf '%s\n' '  http://127.0.0.1:8080/Plone/@@persistent-log'

# The full suite against both storage backends -- ZODB and the RDBMS backend
# verified against a PostgreSQL test container -- under branch coverage. Fails
# when the coverage requirement or the container requirement is not met.
test:
	$(REQUIRE_POSTGRES) $(UV) run --python $(PYTHON) coverage run --branch \
		-m zope.testrunner --path . --package $(PACKAGE)
	$(UV) run --python $(PYTHON) coverage report -m

# Everything except the PostgreSQL backed tests (skipped when no container
# runtime is available). For machines without Docker.
test-no-postgres:
	$(TESTRUNNER)

# Only the tests that need PostgreSQL.
test-rdbms:
	$(REQUIRE_POSTGRES) $(TESTRUNNER) \
		-t test_storage_rdbms -t test_rdbms_integration

lint:
	$(UV) run --python $(PYTHON) ruff check zopyx

format-check:
	$(UV) run --python $(PYTHON) ruff format --check zopyx

typecheck:
	$(UV) run --python $(PYTHON) ty check zopyx

audit:
	# These Plone advisories describe the Plone 4/5 issue; Plone 6.2 is
	# locked to plone-app-contenttypes 5.0.1 and has no indexed fixed version.
	$(UV) audit --locked \
		--ignore GHSA-w6g9-xccc-347h

build:
	rm -rf build dist *.egg-info
	$(UV) run --python $(PYTHON) --group release python -m build

package-check: build
	$(UV) run --python $(PYTHON) --group release python -m twine check dist/*

docs:
	$(UV) run --python $(PYTHON) --group docs sphinx-build \
		-b html -d docs/build/doctrees docs/source docs/build/html
