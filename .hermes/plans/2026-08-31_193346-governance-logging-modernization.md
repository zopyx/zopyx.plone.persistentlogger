# Governance Logging Modernization Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Modernize `zopyx.plone.persistentlogger` into a typed, Plone 6.2+ governance logging add-on with controlled retention/deletion, audited exports (JSON, CSV, XLSX, and ODS), a secure browser/API surface, migrations, and enforceable test coverage.

**Architecture:** Preserve the existing adapter entry point (`IPersistentLogger(context)`) for compatibility, but move the implementation behind typed domain models, a storage repository, retention service, export service, and browser views. Keep object-local annotations as the first storage backend so existing installations remain readable; add a versioned schema and an explicit migration path before considering a separate high-volume backend. Treat deletion as a privileged, explicit, reviewable operation with a durable deletion record in a separate site-level governance journal.

**Tech Stack:** Python 3.14, Plone 6.2+, Zope interfaces/adapters, GenericSetup, `pytest`/`plone.app.testing`, `ruff`, `ty` (or mypy if Plone stubs prevent ty adoption), `coverage`, stdlib `json`/`csv`, `openpyxl` for XLSX, `odfpy` for ODS, setuptools initially with a planned `pyproject.toml` migration.

## Agreed Version 1 Requirements

- Plone 6.2+ and Python 3.14 only.
- Annotation storage remains the initial backend; target capacity is approximately 100,000 events per site.
- Retention is site-configurable, initially disabled, with a default age of 365 days.
- Manual deletion is limited to 100 entries per operation, selects the oldest eligible entries first, and processes them in one transaction.
- Retention/deletion is object-scoped and manual only in version 1.
- The existing `log_clear` operation is removed; only the governed deletion workflow remains.
- All governance actions are restricted to the Plone `Manager` role; no four-eyes approval is required.
- Existing records migrate automatically on first object access and persist transactionally.
- No legal holds are included in version 1.
- The governance journal is a separate Plone object in the site root and is retained permanently.
- A hash chain covers both normal events and governance-journal events.
- Severity is limited to `debug`, `info`, `warning`, `error`, and `critical`.
- Deletion requires a reason of at least 10 characters.
- Exports are limited to 100,000 entries or 1,000 MB; exceeding a limit produces a clear error rather than automatic splitting.
- Details are JSON objects in JSON exports and canonical JSON text in CSV, XLSX, and ODS exports.
- Default field limits are 4,000 characters for comments, 100 for event types, 255 for usernames, 2,048 for paths/URLs, and 64 KiB for structured details.

---

## 1. Current Repository Baseline

The repository is a small legacy Plone add-on, not yet a modern Plone 6.2 package:

| Area | Current state | Consequence |
|---|---|---|
| Packaging | `setup.py`, `setup.cfg`, `requirements.txt`; no `pyproject.toml` | Replace obsolete build/test conventions and make dependency groups reproducible |
| Compatibility metadata | Python 3.7–3.11 and Plone 4.3–6.0 classifiers; docs also claim Python 2.7 | Remove unsupported claims and define the supported Plone/Python matrix |
| Test execution | `Makefile` calls `bin/test`; legacy buildout files include Plone 4/5 | Add a supported Plone 6 test fixture and modern CI; retain legacy files only during transition |
| Persistent data | `PersistentLoggerAdapter` stores dicts in an annotation `OOBTree` keyed by UTC datetime | Preserve old data, fix key collisions and schema ambiguity, add versioned records and migration |
| Mutations | `demo()` and `log_clear()` disable CSRF protection; clear is a GET browser action | Replace with explicit POST-only, permission-protected, CSRF-checked operations |
| Browser/API | HTML view plus `entries_json`; no format-neutral export service | Add streaming/export views with explicit content types, filename rules, limits, and audit events |
| Retention | Loguru has file rotation/retention, but persistent object logs have no policy | Design retention for persistent governance records, dry runs, and deletion evidence |
| Tests | `tests/test_logger.py` covers only basic log/clear/read behavior | Build unit, integration, security, export, migration, concurrency, and coverage gates |
| Frontend | Vendored DataTables/jQuery assets and inline JavaScript in `browser/logger.pt` | Keep UI narrowly scoped initially; modernize unsafe/destructive interactions and asset loading |

The implementation must not overwrite or silently discard unrelated working-tree changes. Before starting implementation, re-run `git status --short --branch` and capture a baseline with the canonical test/build commands.

## 2. Governance Requirements to Make Explicit Before Coding

These are design inputs, not implementation guesses. Resolve them in a short product/security decision record under `docs/decisions/` before the domain model is finalized:

1. **Record scope:** Which events are logged, which fields are mandatory, and which fields may contain personal data or secrets? Define a redaction policy; `details_raw` currently permits arbitrary pickled Python data and must not remain an unrestricted governance export surface.
2. **Authority model:** Define separate permissions for viewing, exporting, configuring retention, executing deletion, and reading the governance journal. All are initially restricted to the Plone `Manager` role. Do not reuse `Modify portal content` for every action.
3. **Retention semantics:** Age is measured from event creation, in UTC, with the explicit boundary `< cutoff`.
4. **Deletion meaning:** Delete the oldest eligible entries first, with `N <= 100` and `D` site-configurable from the 365-day default. Version 1 is object-scoped and manual-only.
5. **Legal hold:** Legal holds are explicitly out of scope for version 1.
6. **Audit of deletion:** Define where the deletion request, actor, reason, selection, counts, and result are stored so the evidence survives deleting the selected event rows. Prefer a site-level governance journal or external sink; never claim an entry was deleted if the evidence write failed.
7. **Tamper evidence:** Decide whether the first release requires hash chaining/signatures/WORM export, or only access-controlled persistence plus audit trails. For a governance product, hash-chain metadata should be designed now even if signing is phased later.
8. **Scale:** Establish expected events per object/site, largest export, concurrent writers, and maximum browser response time. This determines whether annotations are sufficient or whether a relational/event-store backend is required.
9. **Formats:** Treat Excel as XLSX and ODF as ODS unless requirements explicitly include other OpenDocument types. Define whether exported `details` is a string, structured JSON, or both.
10. **Compatibility:** Confirm whether Plone 5 installations must be supported during migration. The target plan assumes Plone 6.2+ is the release boundary and old Plone support is removed from the main package.

## 3. Target Package Layout

Create the following modules gradually; do not introduce empty abstraction layers without a caller and tests:

```text
zopyx/plone/persistentlogger/
    api.py                 # stable public functions/facade
    interfaces.py          # public Zope interfaces and typed protocols
    models.py              # immutable typed event/request/result models
    exceptions.py          # validation, authorization, retention, export errors
    repository.py           # storage protocol and annotation implementation
    logger.py               # compatibility adapter delegating to repository
    serialization.py        # safe JSON/export row normalization and redaction
    retention.py            # policy evaluation, dry-run, deletion orchestration
    exports/
        __init__.py
        base.py             # exporter protocol and registry
        json.py
        csv.py
        xlsx.py             # optional dependency
        ods.py              # optional dependency
    browser/
        logger.py           # read-only listing and export views
        retention.py        # policy and deletion views
        permissions.py      # authorization helpers/constants
        schemas.py          # request parsing/validation
        logger.pt
        retention.pt
    controlpanel/
        __init__.py
        settings.py         # registry-backed site policy
        form.pt
    migrations/
        __init__.py
        v1.py               # legacy annotation normalization
    jobs.py                 # optional maintenance entry point, if scheduled cleanup is approved
    configure.zcml
    browser/configure.zcml
    profiles/default/
        metadata.xml
        rolemap.xml
        permissions.xml
        controlpanel.xml (if used)
        registry/*.xml (if used)
```

Exact final paths may be adjusted after the Plone 6 test fixture confirms the preferred control-panel and registry conventions, but the separation of storage, domain, policy, export, and browser layers is required.

# Phase 0 — Foundation and Baseline

### Task 0.1: Capture repository and runtime baseline

**Files:** Create `.hermes/baselines/` only if the project policy allows committed baselines; otherwise save local artifacts outside Git.

Run:

```bash
git status --short --branch
python3 --version
uv --version
make test
make build
```

Record exact failures as baseline facts. Do not mark modernization complete while the supported canonical suite or build is failing.

### Task 0.2: Replace legacy buildout assumptions with a Plone 6 development definition

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock` or the repository-approved lockfile
- Modify: `setup.py` only as needed during transition
- Modify: `Makefile`
- Create/modify: `constraints/Plone-6.2.txt` or the project’s chosen Plone constraints file

Define package metadata, `requires-python`, runtime dependencies, optional export dependencies, development dependencies, and package data. Use a supported Plone 6.2 constraint set rather than unconstrained latest packages. Make targets explicit:

```make
install:
	uv sync --group test --group lint

test:
	uv run pytest -q

coverage:
	uv run pytest --cov=zopyx.plone.persistentlogger --cov-branch --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check zopyx

build:
	uv build
```

If Plone’s current packaging constraints make a full setuptools-to-hatchling migration unsafe, retain setuptools as the backend but still centralize metadata in `pyproject.toml` and document the constraint.

### Task 0.3: Add CI quality gates

**Files:** Create `.github/workflows/ci.yml`; modify dependency configuration as needed.

Matrix the supported Python versions and the Plone 6.2 constraint set. Run unit/integration tests, branch coverage, Ruff, type checking, package build, and a clean-wheel install smoke test. Add dependency vulnerability checking as an advisory job initially; make it blocking after false positives and policy are documented.

### Task 0.4: Establish coverage configuration and test markers

**Files:** Modify `pyproject.toml`; create/update `pytest.ini` only if needed; modify `zopyx/plone/persistentlogger/tests/base.py`.

Add coverage source/branch settings, test markers (`unit`, `integration`, `security`, `export`, `migration`), warnings policy, deterministic timezone/clock fixture, and isolation for annotations. Set an initial ratchet (for example, no regression from baseline), then raise it to at least 95% for new core modules and at least 90% overall. Do not exclude browser, error, migration, or deletion code merely to inflate the number.

# Phase 1 — Domain Model and Safe Storage

### Task 1.1: Define the event schema and invariants

**Files:** Create `models.py`, `exceptions.py`; modify `interfaces.py`.

Define typed models for `LogEvent`, `LogQuery`, `RetentionPolicy`, `DeletionRequest`, `DeletionPreview`, `DeletionResult`, and `ExportRequest`. Use timezone-aware UTC datetimes, a UUID event identifier, stable event type/category, severity enum or validated literal, actor identity, target identity, comment, optional URL, structured details, retention metadata, legal-hold state, and schema version.

Validation requirements:

- Reject naive datetimes at public boundaries or normalize only with an explicit documented rule.
- Validate bounded string lengths and allowed severity/category values.
- Redact or reject secrets, credentials, authorization headers, and uncontrolled HTML in details.
- Keep structured details JSON-compatible; define a safe fallback for legacy non-JSON Python objects.
- Never use `pickle` as the interchange/export format.

### Task 1.2: Define repository interfaces and annotation backend

**Files:** Create `repository.py`; modify `logger.py`.

Create a repository protocol with typed methods for append, query, count, lookup by UUID, preview deletion, delete selected IDs, and read/write policy metadata. Implement an annotation repository that can read current records and legacy dictionaries. Ensure event keys cannot collide when two events share a timestamp; use UUID or a composite key rather than a raw datetime key.

Storage operations must preserve transaction semantics and mark persistent structures changed. Deletion must be deterministic, bounded, and performed by UUID selection generated by the preview step.

### Task 1.3: Preserve the public adapter API

**Files:** Modify `logger.py`; create/modify `api.py`.

Keep `IPersistentLogger(context)`, `.log()`, `.entries`, `.entry_by_uuid()`, `.get_last_user()`, `.get_last_date()`, and `.clear()` as compatibility shims. Deprecate `.clear()` and route it through a privileged deletion service; do not retain an unbounded destructive shortcut. Document deprecations and emit a safe warning where appropriate.

Use `Protocol`/interfaces for new code and complete annotations for every public function and method. Avoid leaking implementation-specific persistent types from the public API.

### Task 1.4: Migrate legacy records safely

**Files:** Create `migrations/v1.py`; create migration tests under `tests/test_migrations.py`.

Implement idempotent read-time or explicit upgrade migration for dictionaries containing `date`, `username`, `level`, `comment`, `info_url`, `details`, `details_raw`, and `uuid`. Define how `details_raw` is converted or quarantined when it is not JSON-compatible. Preserve UUIDs and timestamps. Never rewrite all annotations in one request without a bounded transaction strategy.

Test legacy fixtures, duplicate timestamps, missing fields, malformed values, repeated migration, rollback on failure, and preservation of unknown fields in a safe metadata envelope.

# Phase 2 — Security and Governance Controls

### Task 2.1: Define dedicated permissions and role mapping

**Files:** Modify `browser/configure.zcml`; create `profiles/default/permissions.xml`, `rolemap.xml`, and/or `controlpanel.xml`.

Create separate permissions for:

- view governance log
- export governance log
- manage retention policy
- preview deletion
- execute deletion
- run demo/test data creation

Update action visibility and browser registrations to use these permissions. Add tests for anonymous, authenticated insufficient-role, authorized, and inherited-object contexts.

### Task 2.2: Make all mutations POST-only and CSRF-protected

**Files:** Modify `browser/logger.py`; create `browser/retention.py` and `browser/schemas.py`; modify `browser/configure.zcml` and `logger.pt`.

Remove `IDisableCSRFProtection` from production views. Use `CheckAuthenticator` (or the supported Plone 6 mechanism), require POST for demo/clear/retention/deletion, validate request bodies, and return safe status messages. A GET may display a deletion preview but must never mutate data.

Add tests for wrong HTTP method, missing/invalid CSRF token, insufficient permission, malformed dates/counts, replayed request IDs, and successful authorized requests.

### Task 2.3: Add redaction and sensitive-data controls

**Files:** Create `serialization.py`; modify logging and export paths.

Centralize redaction for keys and values matching passwords, tokens, cookies, authorization headers, API keys, and other configured sensitive fields. Do not log complete payloads or authentication material. Make the redaction policy configurable only by a privileged administrator and test it with nested mappings/lists and Unicode data.

### Task 2.4: Add tamper-evidence metadata

**Files:** Extend `models.py`, `repository.py`, `retention.py`; create migration/schema tests.

Record an integrity digest over a canonical event representation and define chain semantics (`previous_digest`, `digest_algorithm`, `digest`). Apply the same hash-chain rules to normal events and the separate governance journal. Reject or flag integrity mismatches during reads/exports; never silently “repair” evidence.

If signing/WORM storage is not in the first release, document the limitation explicitly and provide an extension point for an external signer or immutable sink.

### Task 2.5: Create a durable governance journal for administrative actions

**Files:** Extend repository/storage or create a site-level journal implementation; add `tests/test_governance_journal.py`.

Record policy changes, export requests/results, deletion previews, deletion executions, failures, and migration actions with safe metadata. The journal entry must include actor, timestamp, operation ID, reason, selection criteria, requested/eligible/deleted/skipped counts, and outcome. Ensure deletion evidence is committed independently or transactionally before claiming success.

# Phase 3 — Retention and Controlled Deletion

### Task 3.1: Implement policy evaluation as a pure service

**Files:** Create `retention.py`; create `tests/test_retention.py`.

Support policy fields such as enabled flag, age in days (default 365), event categories, minimum severity, a maximum of 100 entries per operation, and optional dry-run defaults. Retention and deletion are object-scoped and manual-only in version 1. Use an injected clock. Make boundary behavior explicit:

```python
cutoff = now_utc - timedelta(days=policy.age_days)
eligible = event.created_at < cutoff
```

Do not delete based on a browser-provided list of arbitrary objects; only use validated UUIDs returned from a server-side preview.

### Task 3.2: Implement preview and confirmation workflow

**Files:** Create/modify `browser/retention.py`, `browser/schemas.py`, templates, and ZCML.

Provide a preview containing policy, cutoff, eligible count, sample IDs, held/skipped count, and estimated export size. Generate a short-lived operation ID and selection digest. Require the execution request to reference that preview, include a reason, and confirm the exact count or selection digest. Expired or changed previews must fail closed.

### Task 3.3: Implement bounded deletion with audit evidence

**Files:** Modify `repository.py`, `retention.py`; add integration tests.

Implement deletion in one transaction for the selected entries, with a hard maximum of 100 entries per operation. Select the oldest eligible entries first. Return exact counts for requested, eligible, missing, deleted, and failed records. Handle concurrent writes/deletes without reporting false success. Write the governance journal before/with the deletion transaction and record failures clearly.

The request is `max_entries=N` plus `older_than_days=D`, with `N <= 100`; version 1 does not support an uncapped deletion mode. Require a non-empty reason of at least 10 characters, reject non-positive values and excessive counts, and do not delete based on a browser-provided list of arbitrary objects—only validated UUIDs returned by a server-side preview.

### Task 3.4: Reserve an external maintenance interface for a later release

**Files:** Document only in `docs/source/administration.rst` and the release notes; create `jobs.py` only when scheduled execution is approved.

Version 1 is manual-only. A future callable maintenance function may reuse the same policy, preview, confirmation, batch, and journal services, but no automatic scheduler or second deletion implementation is part of this release.

# Phase 4 — Export Subsystem

### Task 4.1: Define normalized export rows and streaming contract

**Files:** Create `exports/base.py`, `serialization.py`; tests under `tests/exports/`.

Normalize every event into stable columns/keys:

`event_id`, `created_at`, `actor`, `event_type`, `severity`, `target`, `comment`, `info_url`, `details`, `legal_hold`, `schema_version`, `integrity_digest`.

Define ordering, filtering, pagination, maximum rows, encoding (`UTF-8`), newline behavior, timezone (`UTC`), filename sanitization, and content-disposition rules. Exports must honor view/export permissions and must themselves create a governance journal event without including sensitive payloads.

### Task 4.2: Implement JSON export

**Files:** Create `exports/json.py`; add browser route/tests.

Produce valid UTF-8 JSON with a versioned envelope containing export metadata, applied filters, generation time, and records. Use a safe serializer for datetimes, UUIDs, sets/legacy values, and nested structures. Escape/encode safely in responses; do not embed unescaped event data into HTML or JavaScript.

Test empty, large, Unicode, legacy, malformed-details, filtered, held, and permission-denied exports.

### Task 4.3: Implement CSV export

**Files:** Create `exports/csv.py`; add route/tests.

Use `csv.writer` with explicit dialect, UTF-8 output, stable header order, formula-injection protection for spreadsheet cells, and correct quoting/newlines. Define how multiline comments/details are represented. Test commas, quotes, CR/LF, leading `=`, `+`, `-`, `@`, Unicode, and null values.

### Task 4.4: Implement XLSX export as an optional dependency

**Files:** Create `exports/xlsx.py`; update optional dependencies/build configuration; add tests.

Use `openpyxl` in write-only mode where feasible, set explicit number/date formats, freeze the header row, include a metadata sheet with filters and integrity information, and avoid formulas/macros. Enforce row/size limits and report the optional dependency clearly when unavailable.

Validate the generated workbook by reopening it in a test and checking sheet names, headers, row values, dates, and no formulas/macros.

### Task 4.5: Implement ODS export as an optional dependency

**Files:** Create `exports/ods.py`; update optional dependencies; add tests.

Use `odfpy` (or a verified maintained alternative), write an ODS spreadsheet with explicit text/date cell types, metadata, stable headers, and bounded output. Reopen/inspect the generated ZIP/XML package in tests to verify valid ODF structure and correct values.

### Task 4.6: Add export browser/API views

**Files:** Modify `browser/logger.py`, `browser/configure.zcml`, `logger.pt`; add `tests/test_browser_exports.py`.

Register separate, explicit routes such as `@@persistent-log-export-json`, `...-csv`, `...-xlsx`, and `...-ods`, or a single validated format route if Plone conventions support it safely. Require export permission, validate filters, use response content types and download names, and avoid loading unbounded result sets into memory. Add pagination/streaming for JSON/CSV and hard limits for workbook formats.

# Phase 5 — Plone 6 Browser/UI Modernization

### Task 5.1: Replace destructive links with forms and confirmation

**Files:** Modify `browser/logger.pt`, `browser/retention.pt`, `browser/resources/local.js`.

Render explicit POST forms with CSRF fields, confirmation text containing the scope/count, legal-hold warning, and reason input. Keep read-only log browsing separate from administrative actions. Ensure keyboard/accessibility behavior and no state change on page load.

### Task 5.2: Remove unsafe HTML rendering

**Files:** Modify `browser/logger.pt` and serialization/view code.

Replace `tal:content="structure entry/comment"` with escaped output unless a narrowly reviewed rich-text policy is introduced. Escape/validate URLs with an allowlist and prevent `javascript:`/malformed schemes. Encode bootstrap data through one safe serializer and add an explicit `</script>` regression test.

### Task 5.3: Replace or isolate obsolete vendored frontend assets

**Files:** `browser/resources/`, `logger.pt`, package data configuration, documentation.

Inventory DataTables/jQuery versions and licensing. Prefer Plone 6 resource registration and the smallest supported frontend surface. Remove unused assets only after repository-wide references and rendered-page tests confirm they are not needed. Do not combine this cleanup with unrelated UI redesign.

# Phase 6 — Testing and Coverage Completion

### Task 6.1: Expand unit tests for all pure logic

**Files:** Create tests for `models.py`, `serialization.py`, `retention.py`, each exporter, filename/content-type helpers, and permission/schema validation.

Cover normal paths, every validation failure, timezone boundaries, empty/large inputs, Unicode, malformed legacy data, duplicate IDs, digest mismatch, and optional dependency absence. Use property-based tests for serialization and CSV/JSON round trips if Hypothesis is acceptable.

### Task 6.2: Expand Plone integration tests

**Files:** Modify `tests/base.py`; create `test_integration_logger.py`, `test_browser_security.py`, `test_migrations.py`, `test_retention_integration.py`.

Use the real Plone 6 fixture and GenericSetup installation. Verify adapter registration, annotations, transactions, permissions, browser rendering, CSRF, roles, control-panel settings, exports, and migration behavior. Test multiple objects/sites and transaction rollback.

### Task 6.3: Add concurrency and failure tests

**Files:** Create `tests/test_concurrency.py` or the project’s supported ZODB concurrency test module.

Exercise simultaneous append, same-preview deletion, deletion versus new event, legal-hold race, and governance-journal failure. Assertions must verify no false deletion count, no duplicate event IDs, no bypass of holds, and correct transaction outcome.

### Task 6.4: Enforce coverage ratchets and mutation-quality review

Run:

```bash
uv run pytest --cov=zopyx.plone.persistentlogger --cov-branch --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run ty check zopyx
uv build
```

Review the missing-lines report manually. Add tests for error handlers and browser authorization rather than excluding them. If type checking is blocked by third-party Plone stubs, isolate narrowly documented ignores and keep domain/export/retention code strict.

# Phase 7 — Documentation, Migration, and Release

### Task 7.1: Rewrite user and administrator documentation

**Files:** Modify `docs/source/README.rst`, `docs/source/HISTORY.rst`, `docs/source/index.rst`; create `docs/source/administration.rst`, `docs/source/governance-model.rst`, `docs/source/export-formats.rst`, `docs/source/migration.rst`, and `docs/decisions/*.md` or the project’s chosen format.

Document installation, supported matrix, public API compatibility, event schema, redaction, permissions, retention policy, dry-run/preview/deletion workflow, export formats and limits, integrity evidence, backup/restore, and failure modes. Remove obsolete Plone 4/Python 2 claims and stale Travis references.

### Task 7.2: Define upgrade and rollback procedure

**Files:** Create `docs/source/migration.rst`; add migration tests and release notes.

Document backup requirements, read-only verification, annotation migration, rollback strategy, expected operation IDs, and how to validate event counts/digests before and after upgrade. Do not make destructive cleanup part of package installation or profile application.

### Task 7.3: Package and clean-install verification

Build sdist/wheel, install each into a clean supported environment, verify package data (`.zcml`, templates, profiles, resources), install into a Plone 6.2 test site, apply the profile, render the log view, export all formats, and execute a dry-run deletion. Record exact commands and results in the release checklist.

### Task 7.4: Release policy

Use semantic versioning with a major release for removal of old Plone/legacy behavior. Deprecate compatibility APIs for at least one minor release where practical. Publish security and data-retention behavior changes prominently; never describe deletion as “compliance” without documenting the deployment’s immutable evidence and backup limitations.

## 4. Verification and Acceptance Criteria

The modernization is complete only when all criteria below are demonstrated by tool output and tests:

- Package builds from a clean checkout with a reproducible dependency definition.
- Supported matrix is explicitly Plone 6.2+ and current supported Python versions; obsolete claims are removed.
- All new production Python code has complete useful type annotations; `ty`/mypy has no unexplained errors.
- `make test` (or the new canonical equivalent) passes, including Plone integration tests.
- Overall branch coverage reaches the agreed ratchet, with core domain/retention/export/security modules at or above their target.
- Existing annotation records remain readable and migration is idempotent and tested.
- No production mutation is reachable through GET, missing CSRF, or insufficient permission.
- Deletion requires preview/confirmation/reason, is bounded to 100 entries, reports exact results, and leaves durable governance evidence.
- JSON, CSV, XLSX, and ODS outputs have correct MIME types, stable schemas, safe escaping, bounded resource use, and fixture-based validity tests.
- Export actions and administrative changes are themselves recorded without sensitive payload leakage.
- Browser output escapes comments/details and validates URLs; no unsafe `structure` rendering remains for untrusted event data.
- Clean-wheel install and Plone profile installation succeed; templates, ZCML, resources, and GenericSetup files are included.
- Documentation describes operational limits, backup/restore, retention/deletion behavior, export limits, and unresolved governance assumptions.

## 5. Risks and Trade-offs

| Risk | Mitigation |
|---|---|
| Annotation storage does not scale for high-volume governance logs | Measure volume first; implement repository boundary so a relational/event-store backend can be added without changing callers |
| Legacy `details_raw` contains arbitrary pickled objects | Treat it as legacy input only; never export arbitrary objects; migrate/quarantine with explicit tests |
| “Clear” behavior conflicts with auditability | Deprecate it, require privileged POST/reason, and preserve a durable administrative journal |
| Deletion evidence is deleted with the target entries | Write the governance journal outside the target collection or to an immutable external sink before success |
| Workbook generation consumes excessive memory | Stream JSON/CSV, use write-only XLSX where supported, impose explicit row/byte limits, and make format dependencies optional |
| Legal hold behavior | Legal holds are out of scope for version 1; adding them later requires a policy and concurrency design |
| Plone 6 dependency constraints conflict with generic modern tooling | Pin the Plone constraint set, isolate type-check ignores, and keep domain code independent of Plone internals |
| Hash chaining breaks under concurrent writes | Define serialization/order and transaction conflict behavior; test conflict handling before claiming tamper evidence |
| Browser/UI modernization expands scope | First secure and test existing UI; replace assets only when required for Plone 6 compatibility or security |

## 6. Recommended Delivery Sequence

1. Baseline, packaging, Plone 6.2 fixture, CI.
2. Typed models, repository boundary, compatibility adapter, migration.
3. Permissions, CSRF, redaction, safe rendering.
4. Governance journal and integrity metadata.
5. Retention preview and controlled deletion.
6. JSON/CSV exports, then optional XLSX/ODS exports.
7. Browser/UI cleanup and asset modernization.
8. Coverage ratchet, concurrency/failure tests, documentation, clean-install release verification.

Do not implement retention or export features against the current untyped dictionary API first; establish the typed schema and repository seam before adding destructive or compliance-sensitive operations.
