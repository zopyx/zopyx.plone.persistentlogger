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
* preserve and migrate legacy annotation records;
* compute chained SHA-256 integrity digests;
* find entries by UUID;
* preview and execute object-scoped retention deletion;
* record policy and deletion actions in a separate governance journal;
* export logs as JSON, CSV, XLSX, or ODS;
* expose a searchable and sortable browser table; and
* provide manager-protected browser views for export and retention operations.

Modernization status:

* Python 3.14 and Plone 6.2+ packaging with ``uv``;
* type checking with Astral ``ty`` and formatting/linting with ``ruff``;
* branch coverage enforced at 98% or higher;
* object-scoped retention policies and explicitly confirmed deletion;
* a separate, permanent site-level governance journal;
* hash-chain integrity metadata for log and governance events;
* site-wide audit logging of content creation and metadata edits
  (control panel, per content type, metadata diff); and
* GitHub Actions CI plus manually triggered PyPI/TestPyPI Trusted Publishing.

Requirements
------------

* Python 3.14
* Plone 6.2 or a compatible later Plone release
* ``plone.api``
* ``loguru``

Installation for Plone with uv
------------------------------

This package is a Plone add-on. ``uv`` manages the Python environment and
installs the package; it does not replace Plone site creation or instance
configuration. Buildout is not required.

Existing uv-managed Plone project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

From the root of an existing Plone application project::

    uv add "Products.CMFPlone>=6.2,<7"
    uv add "zopyx.plone.persistentlogger>=0.5.2"
    uv sync

For a reproducible deployment, commit the resulting ``pyproject.toml`` and
``uv.lock`` and install only from the lockfile on the deployment host::

    uv sync --locked --no-dev

If the application uses development and test dependencies, use::

    uv sync --locked --all-groups

The package registers itself through the ``z3c.autoinclude.plugin`` entry
point. After the Plone site has been created, verify that the add-on is
available and apply the GenericSetup profile if the application does not
install it automatically::

    uv run python -c "import zopyx.plone.persistentlogger"
    zopyx.plone.persistentlogger:default

The last line is the GenericSetup profile identifier, not a shell command. It
can be applied through Plone's Add-ons control panel or the application's
profile-installation code.

New uv project
~~~~~~~~~~~~~~

A minimal new project can be initialized with::

    mkdir my-plone-site
    cd my-plone-site
    uv init --python 3.14
    uv add "Products.CMFPlone>=6.2,<7"
    uv add zopyx.plone.persistentlogger
    uv sync

The Plone application still needs its normal WSGI/instance configuration and a
site creation step. Keep those application-specific files in the host project;
do not put site data or secrets into this add-on repository.

Local development of this repository
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone the repository and run the supplied setup script::

    git clone https://github.com/zopyx/zopyx.plone.persistentlogger.git
    cd zopyx.plone.persistentlogger
    ./scripts/setup-uv.sh

The script performs these steps:

#. verifies that ``uv`` is installed;
#. installs or selects Python 3.14;
#. creates ``.venv`` with Python 3.14;
#. installs the locked Plone 6.2+ test environment and development tools;
#. imports both ``Products.CMFPlone`` and this package as a smoke test; and
#. prints the command used to run the Plone test layer.

The equivalent commands, useful in CI or when customizing the environment,
are::

    uv python install 3.14
    uv venv --python 3.14 .venv
    uv sync --locked --all-groups
    uv run zope-testrunner --path . --package zopyx.plone.persistentlogger

The repository also provides a local foreground instance workflow::

    make dev

This creates a local Zope instance on first use and starts it at
``http://127.0.0.1:8080``. Use ``Ctrl-C`` to stop it. To deliberately remove and
recreate the local ``/Plone`` site with the add-on profile installed, stop the
server first and run::

    make reset-site
    make dev

The reset target is destructive and applies only to the local development
instance. ``make dev-reset`` combines both commands. The local development
password can be overridden with ``PLONE_INITIAL_PASSWORD``; it is never a
production credential.

Use the repository Makefile for the complete quality suite::

    make install
    make test
    make coverage
    make lint
    make format-check
    make typecheck
    make audit
    make package-check

No ``bin/buildout``, ``bootstrap.py``, or legacy buildout configuration is
required for the uv workflow. The old buildout files remain only as historical
migration material and are not used by CI.

Package data and verification
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The wheel includes the package's ZCML, Page Templates, GenericSetup profiles,
and browser resources. Validate a locally built distribution with::

    make package-check

This cleans stale output, builds both an sdist and a wheel with ``uv`` and
``python -m build``, and runs ``twine check``. The GitHub Actions build repeats
the check on Ubuntu with Python 3.14.

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

``@@persistent-log-export``
    Export des objektbezogenen Logs. Das gewünschte Format wird mit
    ``format=json``, ``format=csv``, ``format=xlsx`` oder ``format=ods``
    gewählt.

``@@persistent-log-retention-preview``
    Erzeugt eine serverseitige Preview der ältesten löschbaren Einträge.

``@@persistent-log-retention-delete``
    Führt eine bestätigte, CSRF-geschützte Löschung per POST aus.

``@@logger-demo``
    Entwicklungs-/Demo-Datengenerator, verfügbar über den Demo-Browser-Layer.

Die frühere GET-basierte ``@@persistent-log-clear``-Route wurde entfernt. Das
Löschen erfolgt ausschließlich über Preview, Bestätigung, Begründung und die
begrenzte Retention-Operation.

The existing log table uses ``Modify portal content``. The new export and
retention administration routes use ``Manage portal`` and are restricted to
Plone Managers.

Audit logging
~~~~~~~~~~~~~

Site-wide audit logging records content creation and metadata changes as
``create`` and ``edit`` entries in the object-local persistent log. Enable it
through the ``Audit logging`` control panel (Site Setup) per Plone site:

* ``enabled`` turns audit logging on for the site; and
* ``content_types`` restricts auditing to selected content types
  (empty means all types).

On creation the entry contains the full metadata snapshot. On modification a
diff with per-field ``old``/``new`` values is stored in the entry ``details``;
the metadata fields covered are title, description, subject, language,
effective and expiration dates, creators, id, portal type, and UID. Objects
created before audit logging was enabled receive a baseline snapshot on their
first modification without an audit entry.

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
