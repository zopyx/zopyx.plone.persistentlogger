"""Smoke checks for administrator-facing operational documentation."""

from __future__ import annotations

import unittest
from pathlib import Path

README = Path(__file__).parents[4] / "docs" / "source" / "README.rst"


class DocumentationSmokeTests(unittest.TestCase):
    """Keep the documented storage and governance boundaries discoverable."""

    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def test_operational_topics_are_present(self):
        for heading in (
            "RDBMS configuration",
            "PostgreSQL prerequisites",
            "Migration and upgrade behavior",
            "Retention and deletion",
            "Integrity verification",
            "Backup and restore boundaries",
            "Security and immutability boundaries",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.text)

        for required_detail in (
            "ZOPYX_PERSISTENTLOGGER_DATABASE_URL",
            "ZOPYX_PERSISTENTLOGGER_REQUIRE_POSTGRES=1",
            "legal holds are not implemented",
            "verify_event_chain",
            "does not make a backup immutable or compliant",
        ):
            with self.subTest(required_detail=required_detail):
                self.assertIn(required_detail.casefold(), self.text.casefold())

    def test_known_stale_governance_claims_are_absent(self):
        lowered = self.text.casefold()
        self.assertNotIn("migrated automatically on first object access", lowered)
        self.assertNotIn("permanent site-level governance journal", lowered)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(DocumentationSmokeTests)
