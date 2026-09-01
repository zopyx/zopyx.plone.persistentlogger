PYTHON ?= 3.14
UV ?= uv

.PHONY: install dev reset-site dev-reset test coverage lint format-check typecheck audit build package-check docs

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

test:
	$(UV) run --python $(PYTHON) zope-testrunner \
		--path . --package zopyx.plone.persistentlogger

coverage:
	$(UV) run --python $(PYTHON) coverage run -m zope.testrunner \
		--path . --package zopyx.plone.persistentlogger
	$(UV) run --python $(PYTHON) coverage report -m

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
		--ignore GHSA-w6g9-xccc-347h \
		--ignore PYSEC-2026-459

build:
	rm -rf build dist *.egg-info
	$(UV) run --python $(PYTHON) --group release python -m build

package-check: build
	$(UV) run --python $(PYTHON) --group release python -m twine check dist/*

docs:
	$(MAKE) -C docs html
