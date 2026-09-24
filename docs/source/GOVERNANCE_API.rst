Governance API and roles
=========================

The package provides ordinary Plone browser views; it does not require
``plone.restapi``.  Routes are object-scoped unless stated otherwise.

Read API
--------

``@@persistent-log-api``
    ``GET`` only.  Requires ``View audit log``.  Optional exact,
    case-insensitive selectors are ``actor``, ``target``, and ``event_id``.
    ``offset`` and ``limit`` must fit within the 1,000-record object quota.
    The response includes ``object_uid``, ``total``, rows, and a ``legal_hold``
    flag.  Access is journaled with actor, object, operation, and result count;
    selector values are not copied into the journal.

``@@persistent-log-api-export``
    ``GET`` only.  Requires ``Export audit log``.  Returns the governance
    journal as bounded JSON and uses the 10,000-record journal quota.  This is
    a journal export, not an external immutability claim.

``@@persistent-log-integrity``
    ``GET`` only and Manager-only through Plone's ``Manage portal`` permission.
    Returns the local integrity health report.  See
    :doc:`INTEGRITY_OPERATIONS`.

Hold API
--------

``@@persistent-log-api-hold``
    ``POST`` only, requires ``Manage audit legal holds``, and checks Plone's
    authenticator.  ``action=create`` accepts a reason of at least ten
    characters and optional comma-separated ``event_ids``.  Without IDs the
    hold covers all events for the object.  ``action=release`` accepts a
    canonical UUID ``hold_id`` and a reason of at least ten characters.  Both
    transitions are written to the governance journal.

    Repeated request parameters and malformed numeric, selector, event-ID, or
    hold-ID values are rejected.  Error responses are generic JSON and do not
    include backend exception text.

A hold blocks retention deletion if any selected event is held, even if the
hold was created after the deletion preview.  Hold storage is object-local;
the package does not provide a site-wide hold index.

Permissions and default roles
-----------------------------

The default GenericSetup role map grants:

* ``View audit log`` to Manager and the read-only ``Auditor`` role;
* ``Export audit log`` to Manager;
* ``Manage audit retention`` to Manager;
* ``Manage audit legal holds`` to Manager; and
* ``Create audit demo data`` to Manager.

The ``Auditor`` role is not created as a global Plone role by this package.  A
deployment that uses it must create/configure that role.  Existing sites must
reapply the role map or grant equivalent permissions explicitly after an
upgrade.  Settings and integrity routes require ``Manage portal``.

Quotas and explicit scope
-------------------------

The safe core uses hard limits rather than silently truncating results:

* object API rows: 1,000 records;
* explicit multi-object/data-subject site budget: 10,000 matching records;
* governance journal export: 10,000 records; and
* event export: 100,000 entries and at most 1,000,000,000 bytes.

``search_data_subject`` and ``export_journal`` accept an explicit iterable of
objects and de-duplicate it by object UID.  They do not traverse a site or
invent a catalog authorization policy.  A deployment needing whole-site access
must supply an authorization-aware object iterable and enforce its own network
rate limits.

Request context and privacy
---------------------------

The event schema does not persist request IDs, IP addresses, or user agents.
API access journaling deliberately stores only operation metadata and a result
count.  Deployments needing network-forensic context must implement redaction,
access control, and retention at the reverse proxy or adapter boundary.

All browser mutations are POST-only and CSRF-protected.  GET requests for the
read APIs and exports are not mutation operations.  External immutable/WORM
anchoring, a global subject index, configurable quotas, and distributed rate
limiting are outside this package.
