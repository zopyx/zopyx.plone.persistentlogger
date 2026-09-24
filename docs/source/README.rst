zopyx.plone.persistentlogger
============================

Persistent, object-local logging for Plone. The package adds a typed-friendly
Python API and browser views for recording an application history on any
persistent Plone object. Records are stored either in a ZODB annotation --
object-specific histories without a single site-wide log file -- or in an
external relational database, selected per site in a dedicated control panel.

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
* record policy and deletion actions in an object-scoped governance journal;
* export logs as JSON, CSV, XLSX, or ODS;
* expose a searchable and sortable browser table;
* provide manager-protected browser views for export and retention operations;
* provide an HTML retention management page (policy, preview, confirmed
  deletion); and
* record content creation and metadata edits as site-wide audit logging;
* store records either in ZODB annotations or in a relational database
  (PostgreSQL target); and
* select the storage backend and the database URL in the *Audit log storage*
  control panel.

Modernization status:

* Python 3.14 and Plone 6.2+ packaging with ``uv``;
* type checking with Astral ``ty`` and formatting/linting with ``ruff``;
* branch coverage enforced at 99% or higher;
* object-scoped retention policies and explicitly confirmed deletion;
* a persistent governance journal scoped to the logged object (there is no
  separate site-root journal in the current implementation);
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
* ``SQLModel``/``SQLAlchemy`` and a database driver (optional, only for the
  RDBMS backend)

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

The demo workflow creates (or reuses) the local ``/Plone`` site, installs the
Barceloneta theme and fills the Plone root object with a batch of demo audit
entries::

    make demo

``make demo`` installs ``plone.app.theming:default`` plus
``plonetheme.barceloneta:default``, activates the theme through
``plone.app.theming`` (which stores the theme name, rules and prefix in the
registry and switches theming on), installs the add-on profile plus the demo
browser layer, and writes 250 entries (``DEMO_ENTRIES`` changes that number,
``DEMO_SEED`` makes a run reproducible). Because the demo is served from
``http://127.0.0.1:8080``, the target also removes ``127.0.0.1`` and
``localhost`` from *Unthemed host names* (``IThemeSettings.hostnameBlacklist``,
the default ships both): as long as the host is listed there, the theme is off
for that request no matter what the registry says. The target refuses to run
while ``make dev`` is running, because the server holds the ``Data.fs`` lock:
stop the server, run ``make demo``, start the server again and open
``http://127.0.0.1:8080/Plone/@@persistent-log``. Running it again appends
another batch instead of duplicating entries. Local development only.

Use the repository Makefile for the complete quality suite::

    make install
    make test
    make lint
    make format-check
    make typecheck
    make audit
    make package-check

``make test`` runs the full suite against both storage backends -- ZODB and
the RDBMS backend verified against a PostgreSQL test container -- and reports
branch coverage; it fails when the container runtime is missing or the
coverage requirement is not met. Use ``make test-no-postgres`` on machines
without Docker, or ``make test-rdbms`` to run only the database-backed tests.

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

The legacy ``logger.clear()`` compatibility method is deliberately disabled
and raises ``RuntimeError``. The storage repositories do not expose a public
clear or wipe operation either. Use the manager-authorized retention service
with a stored preview, a reason, and a governance record for deletion.

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

Entries grid
------------

The entries view uses agGrid (Community edition, vendored under
``browser/resources/aggrid``) with the *infinite row model*: the browser sends
``startRow``/``endRow``, the ``sortModel`` and the ``filterModel`` to
``@@persistent-log-data`` and renders exactly one page, while counting,
filtering, sorting and paging happen on the server. Both storage backends
implement the same query model -- the ZODB backend evaluates it in Python, the
RDBMS backend translates it into SQL.

The queryable columns are defined once in
``zopyx.plone.persistentlogger.storage.query.COLUMNS``. The grid renders its
column definitions from that list and the server validates every incoming
filter against it:

================== ====== ======================================================
Column             Kind   Notes
================== ====== ======================================================
``created_at``     date   entry timestamp, the default sort order (newest first)
``severity``       text   debug, info, warning, error, critical
``actor``          text   user that triggered the entry
``event_type``     text   application, governance, workflow, ...
``target``         text   affected object or subsystem
``comment``        text   the message itself
``info_url``       text   optional link, may be empty
``details``        json   JSON payload, compared as text
``schema_version`` number hidden by default
================== ====== ======================================================

Supported filter operators are the agGrid standard ones: text (``contains``,
``notContains``, ``equals``, ``notEqual``, ``startsWith``, ``endsWith``),
number and date (``equals``, ``notEqual``, ``lessThan``, ``lessThanOrEqual``,
``greaterThan``, ``greaterThanOrEqual``, ``inRange``), ``blank``/``notBlank``
for every kind, the set filter (``values``) for enumerations, and agGrid's
combined filters (``operator: AND|OR`` with ``condition1``/``condition2`` and
the ``multi`` filter type). Text comparisons are case insensitive. Date bounds
given as plain dates cover the whole day -- a lower bound starts at midnight,
an upper bound ends at 23:59:59.999999. Filters on different columns are
combined with ``AND``. The quick filter (the search box above the grid) searches
comment, actor, event type, target and severity.

Records that are equal on every sort instruction keep their chronological
order; that is the tie break both backends use, so the ZODB and the PostgreSQL
implementation return identical pages. Unsupported payloads are rejected with
HTTP 400 and an ``error`` message instead of being silently ignored.

Browser views
-------------

For an object at ``http://host/path/to/object`` the current views are:

``@@persistent-log``
    agGrid based entries view (see `Entries grid`_). Paging, sorting and
    filtering are executed on the server, so only one page of entries is sent
    to the browser.

``@@persistent-log-data``
    JSON data source of the entries grid. Accepts agGrid's ``startRow``,
    ``endRow``, ``sortModel`` and ``filterModel`` plus the optional ``quick``
    filter and answers with ``rows``, ``startRow``, ``endRow``, ``lastRow`` and
    ``total``. Invalid payloads are answered with HTTP 400 and an ``error``
    message.

``@@logger-entries``
    Deprecated, bounded JSON compatibility endpoint. Use
    ``@@persistent-log-data`` for paged results; requests over its limit are
    rejected.

``@@persistent-log-export``
    Export des objektbezogenen Logs. Das gewünschte Format wird mit
    ``format=json``, ``format=csv``, ``format=xlsx`` oder ``format=ods``
    gewählt.

``@@persistent-log-retention-preview``
    Creates a server-side preview through a CSRF-protected POST request;
    malformed inputs receive structured HTTP 400 responses.

``@@persistent-log-retention-delete``
    Führt eine bestätigte, CSRF-geschützte Löschung per POST aus.

``@@persistent-log-retention``
    HTML management page for the retention workflow: edit the retention
    policy, generate a deletion preview, and confirm deletion with a
    reason. Manager-only.

``@@audit-logging-settings``
    Control panel view for the site-wide audit logging settings.

``@@audit-storage-settings``
    Control panel view for the *Audit log storage* settings: the storage
    backend (``zodb`` or ``rdbms``) and the optional database URL.
    Manager-only; see `Storage layer`_.

``@@logger-demo``
    Entwicklungs-/Demo-Datengenerator, verfügbar über den Demo-Browser-Layer.

Die frühere GET-basierte ``@@persistent-log-clear``-Route wurde entfernt. Das
Löschen erfolgt ausschließlich über Preview, Bestätigung, Begründung und die
begrenzte Retention-Operation.

The existing log view uses the dedicated ``View audit log`` permission. The
new export and retention administration routes use ``Manage portal`` and are
restricted to Plone Managers.

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

Storage layer
-------------

The audit log is written through a small storage layer with one contract and
two interchangeable backends. Everything the audit log *means* lives in
``zopyx.plone.persistentlogger.storage.base``: the integrity hash chain,
retention previews, deletion bookkeeping and the governance journal are
implemented once as template methods on ``BaseLogStorage`` over a handful of
persistence primitives. The backends in ``storage/zodb.py`` and
``storage/rdbms.py`` only decide *where* a record is kept, which is what allows
the same contract test suite to run unchanged against both of them.

Nothing outside the storage layer knows which backend is active. The public
API (``api.py``), the logger adapter (``logger.py``), the retention service
(``retention.py``), the audit subscribers (``audit.py``) and the browser views
all obtain their repository through ``storage.get_repository(context)``.

Backends
~~~~~~~~

``zodb``
    The default and historical backend. Records live in the annotations of
    the logged object, so they travel with the object, are versioned by the
    ZODB and participate in the surrounding transaction. Deleting the object
    deletes its audit records; an aborted transaction aborts its audit
    records as well.

``rdbms``
    Records live in a relational database. The persistence layer is modelled
    with SQLModel -- SQLAlchemy models with Pydantic validation -- on top of
    SQLAlchemy, so the tables are declared once as Python models and any
    SQLAlchemy dialect works. The backend commits every audit record
    immediately, so audit evidence survives an abort or a later deletion of
    the content it describes. The ``details`` payload and the governance
    payload must be JSON serializable. PostgreSQL is the supported production
    target and the database the test suite exercises in a container.

Switching the backend does not migrate existing records. Both backends are
queried and written independently; a site that switches from ``zodb`` to
``rdbms`` starts with an empty relational log and keeps the old records in the
annotations of the objects.

Installation of the RDBMS backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The relational backend is an optional dependency group, so a ZODB-only
installation does not pull a database driver::

    uv add "zopyx.plone.persistentlogger[rdbms]"

or, into an existing environment::

    uv pip install "zopyx.plone.persistentlogger[rdbms]"

The group contains ``sqlmodel>=0.0.22`` (which pulls in SQLAlchemy) and
``psycopg[binary]>=3.2``. Selecting
``rdbms`` without the extra installed fails with an explanatory
``StorageConfigurationError`` on the first log write, not with an obscure
import error.

Configuration
~~~~~~~~~~~~~

Site Setup → *Audit log storage* (``@@audit-storage-settings``, restricted to
the ``Manage portal`` permission) selects the backend:

``Storage backend``
    ``zodb`` (default) or ``rdbms``.

``Database URL``
    SQLAlchemy connection URL of the audit database, for example
    ``postgresql+psycopg://user:password@host:5432/database``. Required when
    ``rdbms`` is selected and ignored by the ``zodb`` backend. When the field
    is left empty, the environment variable
    ``ZOPYX_PERSISTENTLOGGER_DATABASE_URL`` is used instead, which keeps
    container and CI deployments configurable without a site administrator.

The control panel validates a configuration before saving it: an unsupported
backend name, a missing database URL, a malformed URL, and an unreachable
database are refused with a status message and leave the stored configuration
untouched. The settings are cached per site for the duration of a request and
invalidated whenever a registry record changes.

Because Plone stores the database URL in the configuration registry, the
credentials of the audit database are part of the site's registry. Grant
access to the registry accordingly, or leave the control panel field empty and
supply the URL through ``ZOPYX_PERSISTENTLOGGER_DATABASE_URL``.

Canonical record shape
~~~~~~~~~~~~~~~~~~~~~~

Event records are plain dictionaries carrying both the current schema keys and
the legacy keys, so every existing consumer (templates, exporters, the legacy
adapter) keeps working with either backend:

``event_id``
    Entry identifier (UUID, also exposed as the legacy ``uuid`` key).

``created_at``
    UTC timestamp (also exposed as the legacy ``date`` key).

``actor``
    User name associated with the event (also ``username``).

``event_type``
    ``create``, ``edit``, ``application``, or a caller supplied type.

``severity``
    ``debug``, ``info``, ``warning``, ``error`` or ``critical``
    (also ``level``).

``target``
    Free-form subject of the event.

``comment``
    Human-readable message.

``info_url``
    Optional relative or absolute information link.

``details`` / ``details_raw``
    Formatted and original detail values used by older callers.

``schema_version``
    Version of the stored event schema.

``previous_digest``
    Integrity digest of the preceding record of the same object.

``integrity_digest``
    SHA-256 digest over the record content and ``previous_digest``.

Records of one object are displayed in ``(created_at, event_id)`` order. The
integrity chain itself is append-only: each new record links to the persisted
head, regardless of its caller-supplied timestamp. An explicit retention
operation may relink the surviving interval; it records the new first-survivor
digest in the governance journal as the chain anchor. Normal appends never
rewrite existing records.

Governance records are scoped to the same object as the event repository. They
carry ``event_id``, ``created_at``, ``actor``, ``action``, ``reason``, the caller
supplied payload keys, ``previous_digest`` and ``integrity_digest``. The
current implementation does not create a separate site-level governance
object or a cross-object journal chain.

The modernization introduces this versioned event schema on top of the legacy
annotation records. Legacy data is migrated automatically on first object
access and persisted transactionally. Migration preserves UUIDs and event
timestamps and is idempotent. Arbitrary legacy Python objects are not blindly
written to new exports.

Annotation layout (ZODB backend)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each key is a separate entry in ``zope.annotation.interfaces.IAnnotations`` of
the object that owns the log:

===================================================  =======================
Annotation key                                        Content
===================================================  =======================
``…connector.log``                                    ``OOBTree`` of event
                                                      records, keyed by the
                                                      entry identifier
``…connector.governance``                             ``PersistentMapping``
                                                      of governance journal
                                                      records
``…connector.retention``                              ``PersistentMapping``
                                                      with the retention
                                                      policy of the object
``…connector.previews``                               ``PersistentMapping``
                                                      of stored deletion
                                                      previews
===================================================  =======================

``…`` abbreviates the shared prefix
``zopyx.plone.persistentlogger.``; the constants ``LOG_KEY``,
``JOURNAL_KEY``, ``POLICY_KEY`` and ``PREVIEW_KEY`` in
``storage/zodb.py`` hold the full names. The keys are part of the on-disk
format: renaming them invalidates existing installations.

Database layout (RDBMS backend)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The relational backend uses four SQLModel tables with the
``persistentlogger_`` prefix. The schema is created on first use
(``SQLModel.metadata.create_all()``), which keeps deployment to setting the
database URL. The tables hold records of all
objects of all sites sharing one database; ``object_uid`` scopes a record to
the object that owns the log.

``persistentlogger_events`` - the audit events:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``event_id``                ``varchar(36)``      primary key, UUID of the entry
``object_uid``              ``varchar(1024)``    indexed; owning object
``created_at``              ``timestamptz``      indexed; UTC event timestamp
``actor``                   ``varchar(255)``     user name
``event_type``              ``varchar(100)``     ``create``, ``edit``, …
``severity``                ``varchar(32)``      severity value
``target``                  ``varchar(2048)``    subject of the event
``comment``                 ``text``             human-readable message
``info_url``                ``varchar(2048)``    nullable, optional link
``details``                 ``json``             nullable; structured payload
``schema_version``          ``integer``          version of the event schema
``previous_digest``         ``varchar(64)``      chain link to the predecessor
``integrity_digest``        ``varchar(64)``      SHA-256 digest of the record
==========================  ===================  ==============================

``persistentlogger_governance`` - the object-scoped governance journal:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``event_id``                ``varchar(36)``      primary key
``object_uid``              ``varchar(1024)``    indexed; owning object
``created_at``              ``timestamptz``      UTC timestamp
``actor``                   ``varchar(255)``     user name
``action``                  ``varchar(100)``     ``retention_policy_changed``,
                                                 ``retention_delete``, …
``reason``                  ``text``             documented reason
``payload``                 ``json``             nullable; action counts and
                                                 identifiers
``previous_digest``         ``varchar(64)``      chain link to the predecessor
``integrity_digest``        ``varchar(64)``      SHA-256 digest of the record
==========================  ===================  ==============================

``persistentlogger_policies`` - one row per object with a retention policy:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``object_uid``              ``varchar(1024)``    primary key; owning object
``enabled``                 ``boolean``          retention enabled flag
``older_than_days``         ``integer``          age threshold in days
``max_entries``             ``integer``          per-operation limit
==========================  ===================  ==============================

``persistentlogger_previews`` - stored deletion previews, so a deletion can be
confirmed against the exact preview that was shown:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``operation_id``            ``varchar(36)``      primary key
``object_uid``              ``varchar(1024)``    indexed; owning object
``cutoff``                  ``timestamptz``      age cutoff of the preview
``event_ids``               ``json``             identifiers of the selected
                                                 records
``selection_digest``        ``varchar(64)``      digest binding the preview to
                                                 its selection
==========================  ===================  ==============================

Operational notes
~~~~~~~~~~~~~~~~~

* RDBMS event and governance appends use ``Session.add()``. A repeated event
  identifier for the same object is rejected; event history is not upserted.
  Retention policy rows and stored preview rows use ``Session.merge()`` and
  therefore have explicit upsert semantics for their keys.
* Deleting a content object does not delete its rows; relational audit records
  are deliberately independent of the content storage. Retention is the
  supported way to remove audit records.
* ``details`` and ``payload`` values must be JSON serializable. Values that
  are not are rejected at write time instead of being silently coerced.
* Bring your own backup, retention and access control for the audit database;
  the package neither creates roles nor grants privileges.
* Table creation happens on first use and never drops or alters existing
  tables. Schema changes of a future release would require a migration step.

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

The workflow is preview, confirmation, and deletion. The repository for the
logged object records the request, actor, reason, selection, counts, and result
in its object-scoped governance journal. The journal is not part of the
object-local deletion selection, but it is not a separate site-level object or
an externally immutable archive: deleting a ZODB content object also removes
its annotations, while RDBMS rows remain as object-scoped records. The event
and preview deletion is committed before the governance write; if journaling
fails, the retention service raises ``RetentionExecutionError`` and reports an
indeterminate governance state. Legal holds and automatic schedulers are not
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
    make lint
    make format-check
    make typecheck
    make audit
    make package-check

The CI environment uses Python 3.14 and runs the same Plone test layer. The
current suite includes unit and integration tests and runs the ZODB and the
RDBMS backend through one shared contract test suite; the RDBMS tests start a
PostgreSQL container, so ``make test`` requires a container runtime.
Coverage is measured with branch coverage and has a 99% minimum threshold. Plone may emit warnings from
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

Release readiness and security boundaries
------------------------------------------

This repository revision is not a compliance or tamper-proof audit release.
SHA-256 chain metadata detects changes when the complete stored chain is
available, but it is not a digital signature, external anchor, WORM store, or
access-control policy. Before production release, the outstanding chain,
retention/governance transaction, migration, and operational-readiness gaps
must be resolved and verified by the full quality and integration gates.

The current legacy browser implementation predates the governance workflow.
Treat the following as modernization work and review before production use:

* the public clear/wipe primitives are disabled; deletion must remain limited
  to the governed retention workflow;
* every mutation must be POST-only and CSRF-protected;
* permissions must be separated by operation;
* comments, details, and URLs must be safely escaped and validated;
* sensitive values must be redacted before persistence or export;
* audit evidence must survive deletion of the selected events; and
* hash-chain integrity must not be described as digital signatures or WORM
  storage unless those controls are separately deployed; and
* a passing local no-PostgreSQL run is not evidence that the PostgreSQL
  backend passed. Release verification must run the required PostgreSQL
  integration suite and the documented build, lint, type, audit, and package
  checks.

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
