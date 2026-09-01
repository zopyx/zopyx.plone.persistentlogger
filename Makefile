PYTHON ?= 3.14
UV ?= uv

.PHONY: install test coverage lint format-check typecheck audit build package-check docs

install:
	$(UV) sync --python $(PYTHON)

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
	$(UV) audit --locked

build:
	rm -rf build dist *.egg-info
	$(UV) run --python $(PYTHON) --group release python -m build

package-check: build
	$(UV) run --python $(PYTHON) --group release python -m twine check dist/*

docs:
	$(MAKE) -C docs html
