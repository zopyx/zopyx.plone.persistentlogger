Governance Logging Modernization Plan
=====================================

Status
------

This document describes the planned modernization of
``zopyx.plone.persistentlogger`` into a governance logging solution for
Plone 6.2 and later. It is an implementation plan, not a statement that all
features are already available.

The detailed task plan is also kept at
``.hermes/plans/2026-08-31_193346-governance-logging-modernization.md``.
The project documentation is the operationally relevant summary; the Hermes
plan contains the task-level implementation breakdown.

Agreed requirements
-------------------

The following decisions are binding for the first implementation scope:

* target platform: Plone 6.2+ with Python 3.11--3.13;
* annotation storage remains the initial backend, for approximately 100,000
  events per site;
* retention is site-configurable, initially disabled, with a default age of
  365 days;
* manual deletion is limited to 100 entries per operation, selects the oldest
  eligible entries first, and processes the selected entries in one
  transaction;
* deletion is available only manually in version 1; the old ``log_clear``
  operation is removed;
* all governance actions are restricted to the Plone ``Manager`` role, with
  no four-eyes approval requirement;
* migration runs automatically on first object access and persists
  transactionally;
* legal holds are not part of version 1;
* the governance journal is a separate Plone object in the site root and is
  retained permanently;
* the hash chain covers both normal log events and governance-journal events;
* exports and retention/deletion are object-scoped, not site-wide;
* severity is limited to ``debug``, ``info``, ``warning``, ``error``, and
  ``critical``;
* deletion requires a reason of at least 10 characters;
* exports are limited to 100,000 entries and 1,000 MB; exceeding either limit
  produces a clear error rather than automatic splitting; and
* details are JSON objects in JSON exports and canonical JSON text in CSV,
  XLSX, and ODS exports.

Default field limits are 4,000 characters for comments, 100 characters for
Event types, 255 characters for usernames, 2,048 characters for paths/URLs,
and 64 KiB for structured details.

Goals
-----

The modernization has six primary goals:

* support current Python and Plone 6.2+ installations;
* provide a typed and maintainable Python implementation;
* preserve and migrate existing annotation-based log data;
* provide governed JSON, CSV, XLSX, and ODS exports;
* provide retention policies, legal holds, and controlled deletion; and
* provide security, auditability, reproducible builds, and meaningful test
  coverage.

The existing public adapter entry point, ``IPersistentLogger(context)``, will
remain available during the transition. New functionality will use typed
models and services rather than exposing persistent implementation details.

Current baseline
----------------

The current package is a small legacy Plone add-on:

* packaging is based on ``setup.py`` and old buildout configurations;
* metadata claims Plone 4.3 through 6.0 and Python 3.7 through 3.11, while
  the documentation still mentions Python 2.7;
* tests use the legacy ``bin/test`` runner;
* persistent records are untyped dictionaries in an annotation ``OOBTree``;
* records use a datetime key, which is unsafe for same-timestamp writes;
* the browser layer has JSON listing and destructive clear functionality, but
  no format-neutral export service;
* ``demo`` and ``log_clear`` disable CSRF protection;
* destructive clearing is exposed as a browser URL rather than an explicit
  confirmed POST operation; and
* the test suite covers only basic append, list, lookup, and clear behavior.

These facts define the starting point. The modernization must not silently
remove or rewrite existing records.

Target architecture
-------------------

The implementation will be split into the following logical layers::

    browser/API
        -> validation and authorization
    governance services
        -> retention, legal holds, deletion, audit journal
    export services
        -> JSON, CSV, XLSX, ODS
    typed domain models
        -> LogEvent, policies, requests, results
    repository interface
        -> annotation backend initially; additional backends later

The annotation repository remains the first backend for compatibility. A
repository boundary is required because annotation storage may not be
appropriate for high-volume sites. A later relational or event-store backend
must be replaceable without changing callers or export formats.

Planned package structure
-------------------------

The following modules are planned. Modules should be introduced when they have
production callers and tests; unnecessary abstractions must be avoided::

    zopyx/plone/persistentlogger/
        api.py
        interfaces.py
        models.py
        exceptions.py
        repository.py
        logger.py
        serialization.py
        retention.py
        exports/base.py
        exports/json.py
        exports/csv.py
        exports/xlsx.py
        exports/ods.py
        browser/logger.py
        browser/retention.py
        browser/schemas.py
        controlpanel/settings.py
        migrations/v1.py
        jobs.py

Modern Python and Plone foundation
----------------------------------

The first implementation phase will:

#. create ``pyproject.toml`` and a reproducible lock/constraint strategy;
#. define the supported Python and Plone 6.2+ matrix;
#. retain setuptools if Plone constraints require it, but centralize metadata
   in ``pyproject.toml``;
#. replace the legacy ``bin/test`` Makefile assumptions with explicit
   ``uv``-based test, lint, type-check, coverage, and build targets;
#. add CI for the supported Python matrix and Plone constraints; and
#. verify sdist, wheel, clean installation, GenericSetup installation, and
   package data inclusion.

The old Plone 4/5 buildout files should be removed or clearly marked as
unsupported after the Plone 6 migration is validated. The main package must
not continue to advertise unsupported compatibility.

Typed domain and storage model
------------------------------

A typed ``LogEvent`` model will define the stable event schema. It will include
at least:

* UUID event identifier;
* timezone-aware UTC creation timestamp;
* actor and target identifiers;
* event type/category and validated severity;
* comment and optional information URL;
* structured, JSON-compatible details;
* schema version and retention metadata;
* legal-hold state; and
* integrity metadata where enabled.

The public API must reject naive datetimes, unbounded strings, invalid
severity/category values, and details containing credentials or other
sensitive material. Arbitrary Python objects must not be introduced into new
records or exports.

The repository will provide typed operations for append, query, lookup,
count, deletion preview, deletion by validated UUIDs, and policy metadata.
Legacy dictionaries will be normalized by an idempotent migration. Existing
UUIDs and timestamps must be preserved. Legacy ``details_raw`` values that
cannot be represented safely will be quarantined or represented as a safe
legacy marker; they must never be blindly serialized or exported.

The current adapter methods will remain as compatibility shims:

* ``log`` delegates to the new repository/service;
* ``entries`` returns normalized events;
* ``entry_by_uuid`` uses the repository;
* ``get_last_user`` and ``get_last_date`` remain available; and
* ``clear`` is deprecated and delegates to the governed deletion path.

Security model
--------------

Dedicated permissions will be defined for:

* viewing governance logs;
* exporting logs;
* managing retention policies;
* previewing deletion;
* executing deletion;
* managing legal holds; and
* generating demo/test data.

No mutation may be reachable through GET. All browser-session mutations must
be POST-only, permission-protected, and protected by the supported Plone 6
CSRF mechanism. The existing use of ``IDisableCSRFProtection`` must be removed
from production paths.

Browser responses will escape comments and details by default. URLs will be
validated against permitted schemes, and untrusted values will not be
rendered using TAL ``structure``. JSON embedded into HTML will use one safe
serializer that prevents ``</script>`` termination and equivalent injection.

Redaction will be centralized for passwords, tokens, cookies, authorization
headers, API keys, and configured sensitive fields. Administrative logs will
contain operation metadata, not complete payloads or credentials.

Retention, legal holds, and deletion
------------------------------------

Retention is a policy-driven service, not a direct database loop. A policy
will define at least:

* enabled state;
* age in days;
* event categories/severities to which it applies;
* batch limit and safety maximum; and
* dry-run behavior.

All time calculations use UTC. The cutoff boundary must be explicit, for
example ``created_at < now_utc - timedelta(days=age_days)``.

Two explicit deletion modes are planned:

* delete at most ``N`` eligible entries older than ``D`` days; or
* delete all eligible entries older than ``D`` days, subject to a configured
  safety maximum.

The operation sequence is:

#. validate the request and authorization;
#. calculate a server-side preview;
#. show eligible, held, and excluded counts;
#. issue a short-lived operation ID and selection digest;
#. require explicit confirmation, reason, and matching preview;
#. re-check record existence and legal holds at execution time;
#. delete in bounded transactions; and
#. write durable governance evidence with exact result counts.

A legal hold always overrides normal retention. Holds must be scoped and
include actor, reason, creation/removal timestamps, and an auditable change
record. Race conditions between hold creation and deletion must be tested.

The deletion result must distinguish requested, eligible, held, missing,
deleted, and failed records. The system must not report success merely because
a prior lookup succeeded; final deletion state must be verified.

Administrative governance journal
---------------------------------

Policy changes, export requests/results, legal-hold changes, migration
operations, deletion previews, deletion executions, and failures will be
recorded in a durable governance journal. Each entry will include an
operation ID, actor, UTC timestamp, reason, selection criteria, requested and
result counts, and outcome.

The journal must survive deletion of the event records it describes. It must
be stored separately from the selected records or delivered to an external
immutable sink. A successful deletion must never be claimed if its governance
evidence cannot be written.

The design will reserve fields for canonical event digests and a hash chain
(``previous_digest``, algorithm, digest). Digital signatures or WORM storage
may be a subsequent phase, but any first release must document whether its
persistence is tamper-evident or merely access-controlled.

Export subsystem
----------------

All formats will use one normalized, stable set of columns/keys::

    event_id, created_at, actor, event_type, severity, target,
    comment, info_url, details, legal_hold, schema_version,
    integrity_digest

Exports will define ordering, filtering, maximum rows, encoding, UTC date
format, filename sanitization, and content-disposition behavior. Export
operations themselves are journaled without logging sensitive event payloads.

JSON
~~~~

JSON will use a versioned envelope containing export metadata, applied filters,
generation time, and records. Datetimes, UUIDs, legacy values, and nested
structures use a safe serializer. Large JSON exports should be streamed or
bounded rather than loaded without limit into memory.

CSV
~~~

CSV will use the stdlib ``csv`` module with explicit dialect, stable headers,
UTF-8 encoding, correct quoting/newlines, and spreadsheet formula-injection
protection for values beginning with ``=``, ``+``, ``-``, or ``@``.

XLSX
~~~~

Excel output means XLSX. ``openpyxl`` will be optional and write-only mode
will be used where appropriate. Workbooks will have stable headers, explicit
date formats, a frozen header row, and a metadata sheet. Macros and formulas
will not be generated. Output row and byte limits are mandatory.

ODS
~~~

OpenDocument output means ODS unless additional ODF types are explicitly
requested. ``odfpy`` or a verified maintained alternative will be optional.
The generated package must use explicit text/date cell types and include
metadata. Tests must inspect the resulting ZIP/XML structure.

Browser and UI modernization
----------------------------

The current log table can be retained initially, but destructive operations
will become explicit forms with:

* POST method and CSRF token;
* permission checks;
* visible scope and count;
* legal-hold warning;
* required reason; and
* confirmation of the exact preview/selection.

Read-only browsing, export actions, retention policy management, and deletion
execution will be separate UI concerns. Vendored DataTables and jQuery assets
will be inventoried and reduced only after rendered-page tests prove that no
required behavior is lost.

Testing and quality gates
-------------------------

The test suite will use the real Plone 6 fixture for integration behavior and
pure unit tests for domain, serialization, retention, and exporter logic.
Required coverage includes:

* legacy migration and rollback;
* duplicate/same-timestamp writes;
* timezone and retention boundaries;
* empty, Unicode, malformed, and large exports;
* CSV formula injection and quoting;
* valid XLSX/ODS package structure;
* permissions, CSRF, wrong HTTP method, and anonymous access;
* legal holds and hold/deletion races;
* preview expiry and changed selections;
* concurrent append/delete behavior;
* transaction failures and journal failures; and
* safe browser rendering, URL validation, and ``</script>`` regression.

The initial quality gate is no regression from the measured baseline. The
planned target is at least 95% branch coverage for new core modules and at
least 90% overall, with no exclusions for deletion, migration, security, or
error paths merely to improve the percentage.

Planned commands are::

    uv run pytest --cov=zopyx.plone.persistentlogger --cov-branch \
        --cov-report=term-missing
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check zopyx
    uv build

Delivery sequence
-----------------

#. Foundation: baseline, Plone 6.2 fixture, packaging, CI, and coverage.
#. Domain: typed models, repository boundary, compatibility API, migration.
#. Security: permissions, CSRF, redaction, safe templates.
#. Governance: journal, legal holds, integrity metadata.
#. Retention: preview, confirmation, bounded deletion, failure handling.
#. Exports: JSON/CSV first, then optional XLSX/ODS.
#. UI: browser workflow and asset modernization.
#. Release: concurrency tests, documentation, clean installation, upgrade and
   rollback verification.

Open decisions
--------------

Before implementing destructive or compliance-sensitive features, the
following decisions require explicit approval:

#. What event categories and fields are mandatory?
#. Which users/roles may view, export, configure, hold, preview, and delete?
#. Does retention use event creation time or another lifecycle timestamp?
#. Is deletion by oldest ``N``, all older than ``D``, or both?
#. What legal-hold scopes are required?
#. Is a separate site-level journal acceptable, or is an external immutable
   sink required?
#. Is hash chaining sufficient initially, or are signatures/WORM exports a
   release requirement?
#. What event volume and export-size limits must be supported?
#. Is ODS the intended OpenDocument format?
#. Must any Plone 5 installation remain supported during the transition?

Release acceptance
------------------

The work is complete only when:

* a clean checkout builds and installs reproducibly;
* supported Plone/Python versions are documented and tested;
* existing records remain readable and migration is idempotent;
* all mutations require permission, POST, and CSRF protection;
* retention and deletion respect legal holds and leave durable evidence;
* all four export formats have correct content types and validity tests;
* safe rendering and redaction are verified;
* the canonical test, type, lint, coverage, and build gates pass; and
* upgrade, rollback, backup, retention, deletion, and export limitations are
  documented for administrators.
