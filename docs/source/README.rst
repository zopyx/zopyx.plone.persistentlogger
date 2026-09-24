zopyx.plone.persistentlogger
============================

Persistent, object-local logging for Plone. The package adds a typed-friendly
Python API and browser views for recording an application history on any
persistent Plone object. Records are stored either in a ZODB annotation --
object-specific histories without a single site-wide log file -- or in an
external relational database, selected per site in a dedicated control panel.

.. note::

   The package targets Plone 6.2+ and Python 3.14. It provides governed,
   object-scoped retention and multi-format export, but it is not a complete
   compliance, tamper-proof, or externally immutable audit system. See
   `Security and immutability boundaries`_ before relying on the integrity
   metadata operationally.

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
* an object-scoped governance journal (there is no separate site-level journal
  in the current implementation);
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

``entries`` is returned in deterministic repository order. New code should keep
details JSON-compatible and should not put secrets, passwords, tokens, cookies,
or complete request payloads into a log entry. The legacy
``logger.clear()`` compatibility method is deliberately disabled and raises
``RuntimeError``; deletion must use the manager-authorized retention workflow.

Severity values
~~~~~~~~~~~~~~~

The modernization uses these severity values:

* ``debug``
* ``info``
* ``warning``
* ``error``
* ``critical``

``LogEvent`` accepts these values (plus the compatibility aliases ``warn``,
``err``, ``fatal``, ``crit``, and ``information``, which it normalizes). New
writes reject other severities. Legacy records are normalized conservatively
by migration and should be reviewed if they contain values outside this
vocabulary.

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

The log view and its data endpoints use the dedicated ``View audit log``
permission. The default profile grants that permission only to the Plone
``Manager`` role. Export, retention, and both storage control-panel routes use
``Manage portal`` (normally granted to Managers); the package does not define
separate export, retention, legal-hold, or integrity-verification permissions.
The demo route uses ``Modify portal content`` and is registered only on the
demo browser layer.

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
    SQLAlchemy. The backend commits its database transactions independently of
    the surrounding ZODB transaction. Consequently, a database-backed record
    can remain after a Plone transaction aborts. The ``details`` payload and
    governance payload must be JSON serializable. PostgreSQL is the supported
    production target and the database exercised by the integration suite in a
    container; other SQLAlchemy dialects are not a supported deployment
    matrix.

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
``StorageConfigurationError`` when the backend is first constructed, not with
an obscure import error.

PostgreSQL prerequisites
~~~~~~~~~~~~~~~~~~~~~~~~

For a supported RDBMS deployment, provide PostgreSQL and a database/schema
that the configured application user can reach and write. The package does
not create the PostgreSQL database, roles, grants, TLS policy, connection
pool limits, or backup policy. Install the ``rdbms`` extra and use a
``postgresql+psycopg://`` URL. The first repository use creates the package's
tables with ``SQLModel.metadata.create_all()``; this is additive setup, not a
general PostgreSQL migration system.

The RDBMS integration tests use a disposable ``postgres:17-alpine``
testcontainers instance. A working Docker-compatible container runtime and
permission to pull/start that image are prerequisites for ``make test`` and
``make test-rdbms``. ``make test`` sets
``ZOPYX_PERSISTENTLOGGER_REQUIRE_POSTGRES=1`` and therefore fails rather than
silently omitting those tests when the runtime is unavailable. Use
``make test-no-postgres`` only when intentionally running without the
PostgreSQL half of the suite; that result does not verify the RDBMS backend.

RDBMS configuration
~~~~~~~~~~~~~~~~~~~

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
backend name, a missing database URL, a malformed URL, and (for a URL entered
in the form) an unreachable database are refused with a status message and
leave the stored configuration untouched. The settings are cached per site
and invalidated whenever a registry record changes. An environment fallback is
resolved when a repository is constructed; it is not written into the
registry.

Because Plone stores a non-empty database URL in the configuration registry,
its credentials are part of the site's registry. Protect registry exports and
backups accordingly. To keep the URL outside the registry, leave the field
empty and supply ``ZOPYX_PERSISTENTLOGGER_DATABASE_URL`` in the process
environment; the deployment must then protect that environment and verify the
effective URL separately.

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
    Redacted, JSON-compatible detail payload; the legacy alias is retained for
    older callers.

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

Governance records carry ``event_id``, ``created_at``, ``actor``, ``action``,
``reason``, the caller supplied payload keys, ``previous_digest`` and
``integrity_digest``.

Migration and upgrade behavior
------------------------------

The GenericSetup profile is version 3. Installing the profile does not scan
and rewrite existing annotations. An administrator must apply the registered
upgrade from profile version 2 to 3. That step visits the site and objects
returned by the unrestricted catalog search, so it is not a substitute for a
separate inventory of inaccessible, uncatalogued, or otherwise unreachable
objects. Re-run verification after the upgrade and investigate any object
that could not be visited.

Before an upgrade, take a tested backup of the relevant storage (see `Backup
and restore boundaries`_). Record event counts and representative UUIDs, and
keep the pre-upgrade backup until post-upgrade verification succeeds. The
upgrade normalizes legacy aliases, preserves valid identifiers and timestamps,
rebuilds the event chain, quarantines records that cannot be normalized when a
quarantine mapping is available, and is designed to be idempotent. It does
not provide an automatic rollback. Restore the pre-upgrade backup if rollback
is required, and do not mix restored ZODB data with an unrelated RDBMS state.

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

The relational backend uses six SQLModel tables with the
``persistentlogger_`` prefix: four data tables plus a per-object chain-head
table and a schema-version marker. The schema is created on first use
(``SQLModel.metadata.create_all()``), which keeps deployment to setting the
database URL. The tables hold records of all objects and sites sharing one
database; ``object_uid`` scopes a record to the object that owns the log.

``persistentlogger_events`` - the audit events:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``event_id``                ``varchar(36)``      composite primary key, UUID
                                                   of the entry
``object_uid``              ``varchar(1024)``    composite primary key; owning
                                                   object
``created_at``              ``timestamptz``      indexed; UTC event timestamp
``actor``                   ``varchar(255)``     user name
``event_type``              ``varchar(100)``     ``create``, ``edit``, …
``severity``                ``varchar(32)``      severity value
``target``                  ``varchar(2048)``    subject of the event
``comment``                 ``text``             human-readable message
``info_url``                ``varchar(2048)``    nullable, optional link
``details``                 ``json``             nullable; structured payload
``schema_version``          ``integer``          version of the event schema
``sequence``                ``integer``          nullable insertion sequence
``previous_digest``         ``varchar(64)``      chain link to the predecessor
``integrity_digest``        ``varchar(64)``      SHA-256 digest of the record
==========================  ===================  ==============================

``persistentlogger_governance`` - the object-scoped governance journal:

==========================  ===================  ==============================
Column                      Type                 Notes
==========================  ===================  ==============================
``event_id``                ``varchar(36)``      composite primary key
``object_uid``              ``varchar(1024)``    composite primary key; owning
                                                 object
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
``operation_id``            ``varchar(36)``      composite primary key
``object_uid``              ``varchar(1024)``    composite primary key; owning
                                                 object
``cutoff``                  ``timestamptz``      age cutoff of the preview
``event_ids``               ``json``             identifiers of the selected
                                                 records
``selection_digest``        ``varchar(64)``      digest binding the preview to
                                                 its selection
==========================  ===================  ==============================

``persistentlogger_chain_heads`` stores the current event and governance
chain heads per ``object_uid``. ``persistentlogger_schema_version`` stores the
``audit`` schema marker used by the additive startup check. They are internal
coordination tables and must be included in PostgreSQL backups and restores.

Operational notes
~~~~~~~~~~~~~~~~~

* Event and governance appends use ``Session.add()`` and reject a duplicate
  identifier for the same object. Policy and preview rows use ``Session.merge``
  for their explicitly keyed upsert behavior.
* Relational audit records are independent of the surrounding content
  transaction. Retention is the supported deletion workflow; database
  lifecycle cleanup is otherwise an operator responsibility.
* ``details`` and ``payload`` values must be JSON serializable. Values that
  are not are rejected at write time instead of being silently coerced.
* Bring your own backup, retention and access control for the audit database;
  the package neither creates roles nor grants privileges.
* Table creation happens on first use. The backend records schema marker
  version 2 and currently performs one additive compatibility change (adding
  the nullable ``sequence`` column when it is absent); it does not drop data
  or provide a general migration/rollback framework. Future incompatible
  schema changes require a release-specific migration procedure. A database
  marked with a newer schema version is rejected rather than downgraded.

Retention and deletion
-----------------------

Version 1 governance defaults are:

* retention policy disabled after installation;
* default retention age: 365 days;
* manually started only;
* object-scoped;
* at most 100 entries per operation;
* oldest eligible entries deleted first;
* the ZODB repository can keep deletion and its journal in the surrounding
  transaction; the RDBMS repository performs separate database transactions
  for the deletion and the subsequent governance write; and
* a reason of at least 10 characters required.

The workflow is preview, confirmation, and deletion. A governance record in
the same object's repository records the request, actor, reason, selection,
counts, result, and a survivor-chain digest. It is not a separate site-level
journal. The preview is stored server-side and the normal preview constructor
records a one-hour ``expires_at`` value. Expiry is enforced when validation is
given the current time; the current browser service does not pass an explicit
clock to that validation, so preview expiry must not be treated as a complete
browser-side security boundary. A preview is consumed once and is bound to the
object, selected UUIDs, and cutoff. The cutoff is strictly
``created_at < now_utc - timedelta(days=older_than_days)``.

The current service deletes first and then records governance evidence. If the
journal write fails, it raises ``RetentionExecutionError`` with an
indeterminate governance state; do not report the operation as fully
evidenced without checking the repository. In the RDBMS backend the event
deletion and governance write are separate database transactions. In ZODB,
the repository has a combined helper for one surrounding transaction, but the
browser service does not use that helper automatically.

Legal holds are not implemented. There is no hold field, hold API, hold
permission, or retention exclusion, so the retention workflow must not be
used as if it enforces a legal hold. Operators must disable or otherwise
withhold deletion through their own process when a hold applies. Automatic
schedulers are also not part of this release.

Integrity verification
----------------------

Each event and governance record contains a canonical SHA-256 digest and a
link to the previous digest in that object's chain. The storage module exposes
verification helpers, but the package does not provide a browser verification
page, scheduled verifier, or external trust anchor. An operator or test can
verify the current records programmatically::

    from zopyx.plone.persistentlogger.storage import (
        get_repository,
        verify_event_chain,
        verify_governance_chain,
    )

    repository = get_repository(context)
    events_ok = verify_event_chain(
        repository.events(), repository.event_chain_anchor()
    )
    governance_ok = verify_governance_chain(repository.journal())

Treat a false result as an integrity incident. Run verification after
migration, restore, retention deletion, and any database maintenance. A
successful result means that the supplied records form a self-consistent
chain and that their stored digests match; it does not prove that records were
never changed. An administrator with write access to the backend can alter
records and recompute both the records and their chain. Retention deliberately
relinks surviving events, so verification covers the current surviving chain,
not a cryptographic proof of deleted history.

Backup and restore boundaries
-----------------------------

The package does not create backups, schedule them, encrypt them, test them,
or provide a restore command. Back up the selected storage using the
platform's supported procedure, and restore it with the application stopped
or otherwise quiesced:

* For ``zodb``, include the Plone ZODB filestorage containing the object's
  annotations and the site's registry. A ZODB restore restores log records
  with the corresponding database state; it does not restore an RDBMS.
* For ``rdbms``, back up and restore the PostgreSQL database containing all
  ``persistentlogger_*`` tables, including the schema marker and chain-head
  rows. PostgreSQL roles, grants, TLS material, credentials, and the
  ``ZOPYX_PERSISTENTLOGGER_DATABASE_URL`` environment are outside the package
  and must be restored/configured separately.

Because RDBMS transactions are independent of ZODB transactions, there is no
package-provided cross-store point-in-time backup. Restoring only one store
can produce a log state that does not correspond to the Plone content state.
After restore, verify connection/configuration, event and governance counts,
representative identifiers, and both hash chains before re-enabling writes.
Backups and database access controls are also the operator's responsibility;
the package does not make a backup immutable or compliant.

Exports
-------

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
returns a clear error rather than silently splitting an export. The current
browser export view does not record export actions in the governance journal;
do not treat an export as an independently journaled governance event.

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

Security and immutability boundaries
-------------------------------------

The package provides access checks for its browser routes, POST and CSRF
checks for retention mutations, bounded previews, redaction of configured
sensitive-key patterns in JSON-like details, and SHA-256 chain metadata. The
legacy clear method is disabled; it is not a deletion bypass. These controls
do not make the package compliant, tamper-proof, or externally immutable.

In particular, the package does not provide digital signatures, a trusted
timestamp, an external hash anchor, WORM/object-lock storage, a separate
immutable journal, database roles/grants, or an independent audit of
administrative database access. The hash and its predecessor are stored in the
same backend as the records. Anyone who can write that backend can change
records and recompute the chain. Deploy an independently controlled signer,
append-only/WORM sink, and operational access policy if those guarantees are
required; document and verify that external deployment separately. Never
describe the package alone as tamper-proof, immutable, or legally compliant.

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
