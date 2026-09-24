"""Tests for the application-facing storage contract."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from ..models import DeletionPreview, RetentionPolicy
from ..retention import RetentionService
from ..storage import AnnotationRepository, LogRepository


class StorageContractTests(unittest.TestCase):
    def test_annotation_backend_implements_public_contract(self):
        repository = AnnotationRepository(SimpleNamespace(__name__="context"))
        self.assertIsInstance(repository, LogRepository)

    def test_contract_hides_backend_persistence_hooks(self):
        self.assertNotIn("_load_events", LogRepository.__dict__)
        self.assertNotIn("_store_event", LogRepository.__dict__)
        self.assertIn("append", LogRepository.__dict__)

    def test_retention_service_depends_on_public_contract(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        preview = DeletionPreview(uuid4(), "context", now, (), "selection")
        repository = MagicMock(spec=LogRepository)
        repository.preview_delete.return_value = preview

        result = RetentionService(object(), repository).preview(
            RetentionPolicy(enabled=True), now
        )

        self.assertIs(result, preview)
        repository.preview_delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
