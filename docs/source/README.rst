zopyx.plone.persistentlogger
============================

Persistent, object-local logging for Plone. The package adds a typed-friendly
Python API and browser views for recording an application history on any
persistent Plone object. The current storage backend is a ZODB annotation,
which makes the logger useful for object-specific histories without creating a
single site-wide log file.

.. note::

   The package is being modernized for Plone 6.2+ and Python 3.14. Retention,
   governed deletion, and multi-format export are part of the modernization
   roadmap. The currently released legacy views are described below; they
   should not yet be treated as a complete compliance or tamper-proof audit
   system.

Features
--------

Current functionality:

* attach a persistent logger to an arbitrary Plone object;
* record comments, severity, user, optional information URL, and details;
* preserve structured ``details_raw`` data for legacy callers;
* find entries by UUID;
* expose a searchable and sortable browser table; and
* return the current entries as JSON through a browser view.

Modernization in progress:

* Python 3.14 and Plone 6.2+ packaging with ``uv``;
* type checking with Astral ``ty`` and formatting/linting with ``ruff``;
* branch coverage enforced at 98% or higher;
* JSON, CSV, XLSX, and ODS export services;
* object-scoped retention policies and explicitly confirmed deletion;
* a separate, permanent site-level governance journal;
* hash-chain integrity metadata for log and governance events; and
* GitHub Actions CI plus manually triggered PyPI/TestPyPI Trusted Publishing.

Requirements
------------

* Python 3.14
* Plone 6.2 or a compatible later Plone release
* ``plone.api``
* ``loguru``

Installation for Plone
----------------------

The package is installed as a normal Python distribution in the Plone
instance environment. For a project using ``uv``::

    uv add zopyx.plone.persistentlogger
    uv sync

For a constrained Plone deployment, use the project constraint file approved
for that deployment and then install the locked environment. The package
registers itself through the ``z3c.autoinclude.plugin`` entry point. A Plone
site can also apply the GenericSetup profile explicitly::

    zopyx.plone.persistentlogger:default

The package data includes ZCML, Page Templates, GenericSetup profiles, and
browser resources. The wheel is validated in CI with ``twine check``.

Python API
----------

Use the adapter with the object that owns the history::

    from zopyx.plone.persistentlogger.logger import IPersistentLogger

    logger = IPersistentLogger(context)
    logger.log("Document approved", level="info")
    logger.log(
        "Approval details",
        level="info",
        info_url="/approval/123",
        details={"workflow": "approved", "revision": 3},
    )

The adapter currently provides::

    logger.entries
    logger.entry_by_uuid(event_uuid)
    logger.get_last_user()
    logger.get_last_date()
    len(logger)

``entries`` is ordered by the underlying annotation storage. Callers that need
stable presentation ordering should sort by the event date or use the browser
view. New code should keep details JSON-compatible and should not put secrets,
passwords, tokens, cookies, or complete request payloads into a log entry.

Severity values
~~~~~~~~~~~~~~~

The modernization uses these severity values:

* ``debug``
* ``info``
* ``warning``
* ``error``
* ``critical``

The legacy implementation historically accepted arbitrary strings. New code
should use the defined values; compatibility validation will be tightened as
the typed event model is introduced.

Browser views
-------------

For an object at ``http://host/path/to/object`` the current views are:

``@@persistent-log``
    HTML table with search, sorting, and expandable details.

``@@logger-entries``
    JSON representation of the entries.

``@@logger-demo``
    Development/demo data generator, available through the demo browser layer.

The legacy package also exposes ``@@persistent-log-clear``. It is being
removed from the modern governance workflow because destructive operations
must use an explicit, permission-protected POST with confirmation and a
reason. Do not expose the legacy clear action in a production governance
installation without reviewing the current security behavior.

All current browser pages use the ``Modify portal content`` permission. The
modernization will introduce separate permissions for reading, exporting,
retention configuration, deletion, and governance-journal access, initially
restricted to the Plone ``Manager`` role.

Data model and storage
----------------------

Entries are stored as annotations on the context object. Existing records may
contain these legacy keys:

``date``
    Creation timestamp.

``username``
    User name associated with the event.

``level``
    Legacy severity value.

``comment``
    Human-readable message.

``info_url``
    Optional relative or absolute information link.

``details`` / ``details_raw``
    Formatted and original detail values used by older callers.

``uuid``
    Entry identifier.

The modernization introduces a versioned event schema and an annotation
repository boundary. Legacy data is migrated automatically on first object
access and persisted transactionally. Migration must preserve UUIDs and event
timestamps and must be idempotent. Arbitrary legacy Python objects must not be
blindly written to new exports.

Retention and deletion roadmap
------------------------------

Version 1 governance defaults are:

* retention policy disabled after installation;
* default retention age: 365 days;
* manually started only;
* object-scoped;
* at most 100 entries per operation;
* oldest eligible entries deleted first;
* all selected entries handled in one transaction; and
* a reason of at least 10 characters required.

The workflow is preview, confirmation, and deletion. A separate site-level
Plone governance object records the request, actor, reason, selection, counts,
and result. That journal is retained permanently and is not part of the
object-local deletion selection. Legal holds and automatic schedulers are not
part of version 1.

Export roadmap
--------------

The normalized export fields are:

``event_id``, ``created_at``, ``actor``, ``event_type``, ``severity``, ``target``,
``comment``, ``info_url``, ``details``, ``schema_version``, and
``integrity_digest``.

The planned formats are:

* JSON: structured ``details`` object and versioned export envelope;
* CSV: canonical JSON text in the details column, UTF-8, safe quoting;
* XLSX: canonical JSON text, explicit date formats, and metadata sheet; and
* ODS: canonical JSON text and explicit OpenDocument cell types.

Exports are limited to 100,000 entries or 1,000 MB. Exceeding either limit
returns a clear error rather than silently splitting an export. Export actions
will be recorded in the governance journal without storing sensitive payloads.

Development workflow
--------------------

Create or update the locked environment::

    uv sync --locked --all-groups

Run the complete local quality suite::

    make test
    make coverage
    make lint
    make format-check
    make typecheck
    make audit
    make package-check

The CI environment uses Python 3.14 and runs the same Plone test layer. The
current suite includes unit and integration tests; coverage is measured with
branch coverage and has a 98% minimum threshold. Plone may emit warnings from
legacy dependencies during fixture setup; warnings are not substituted for
failed assertions.

Building distributions
----------------------

Build and validate both sdist and wheel::

    make package-check

The command removes stale build output, runs ``python -m build`` through
``uv``, and validates the result with ``twine check``. Package data validation
ensures that ZCML, templates, profiles, and browser resources are present.

Continuous integration
----------------------

GitHub Actions are defined in ``.github/workflows/ci.yml`` and run on pushes
to the main branches, pull requests, and manual dispatch. The workflow runs:

* locked dependency installation with ``uv``;
* Plone tests and branch coverage;
* ``ruff check zopyx``;
* ``ruff format --check zopyx``;
* ``ty check zopyx``;
* ``uv audit --locked`` with documented Plone-6.2 advisory exceptions; and
* sdist/wheel build plus ``twine check``.

Publishing is intentionally separate from ordinary CI:

* ``publish-testpypi.yml`` is manually triggered and uses the ``testpypi``
  GitHub Environment;
* ``publish-pypi.yml`` is manually triggered and uses the protected ``pypi``
  GitHub Environment; and
* both workflows use PyPI Trusted Publishing through GitHub OIDC, with no
  long-lived package token or password.

Before publishing, configure the matching Trusted Publisher on the target
index with the exact repository, workflow filename, and environment name.
Production publishing must require environment approval.

Security and operational boundaries
------------------------------------

The current legacy browser implementation predates the governance workflow.
Treat the following as modernization work and review before production use:

* destructive clear behavior must be removed or disabled;
* every mutation must be POST-only and CSRF-protected;
* permissions must be separated by operation;
* comments, details, and URLs must be safely escaped and validated;
* sensitive values must be redacted before persistence or export;
* audit evidence must survive deletion of the selected events; and
* hash-chain integrity must not be described as digital signatures or WORM
  storage unless those controls are separately deployed.

License and project information
-------------------------------

The project is distributed under the GPL-3.0-or-later license.

Repository and issue tracker:

* https://github.com/zopyx/zopyx.plone.persistentlogger
* https://pypi.org/project/zopyx.plone.persistentlogger/

Author:

Andreas Jung / ZOPYX

* Hundskapfklinge 33
* D-72074 Tübingen, Germany
* info@zopyx.com
* https://www.zopyx.com
