Architecture and extension points
===================================

The package separates the application-facing repository contract from the
backend implementation template.

Ownership boundaries
--------------------

* ``models.py`` owns validated domain values such as ``LogEvent``,
  ``RetentionPolicy``, and deletion previews.  Domain values do not choose a
  storage backend.
* ``storage/contracts.py`` owns ``LogRepository``, the public structural
  contract consumed by services and browser code.  It describes event,
  retention, and governance operations without exposing persistence hooks.
* ``storage/base.py`` owns shared integrity, query, and retention algorithms.
  ``BaseLogStorage`` is an implementation template, not the dependency that
  application services should require.
* ``storage/zodb.py`` and ``storage/rdbms.py`` own persistence primitives for
  their respective backends.  They implement the shared template and must
  preserve the ``LogRepository`` contract.
* ``storage/factory.py`` is the only backend selector.  ``get_repository``
  returns the public ``LogRepository`` contract, so callers do not need to
  branch on ZODB versus RDBMS.
* ``api.py``, ``retention.py``, adapters, and browser views own orchestration
  and compatibility behavior.  They should use ``get_repository`` and the
  public contract rather than annotation keys, SQL tables, or protected
  backend methods.

Extending storage
-----------------

A new backend must provide a ``BaseLogStorage`` implementation (or otherwise
implement every method in ``LogRepository``) and be registered in the factory.
Keep backend-specific conversion and transactions inside that backend.  Add
contract tests for behavior shared by all backends; add backend-specific tests
only for physical storage and transaction semantics.

``BaseLogStorage`` remains publicly importable for compatibility with existing
integrations and test doubles.  New application code should annotate injected
repositories as ``LogRepository``.  This makes a fake or an alternative
backend a valid dependency without inheriting implementation details.

Compatibility
-------------

The legacy ``repository.AnnotationRepository`` import and the
``BaseLogStorage`` export remain available.  The protocol is additive: it
changes annotations and documentation, not runtime repository construction or
record shapes.
