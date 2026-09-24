"""Check that built distributions contain all shipped package resources."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

REQUIRED_FILES = {
    "zopyx/plone/persistentlogger/configure.zcml",
    "zopyx/plone/persistentlogger/browser/configure.zcml",
    "zopyx/plone/persistentlogger/browser/logger.pt",
    "zopyx/plone/persistentlogger/browser/retention.pt",
    "zopyx/plone/persistentlogger/browser/resources/persistent-log.js",
    "zopyx/plone/persistentlogger/browser/resources/local.js",
    "zopyx/plone/persistentlogger/browser/resources/styles.css",
    "zopyx/plone/persistentlogger/browser/resources/aggrid/ag-grid-community.min.js",
    "zopyx/plone/persistentlogger/browser/resources/aggrid/ag-grid.min.css",
    "zopyx/plone/persistentlogger/browser/resources/aggrid/ag-theme-quartz-no-font.min.css",
    "zopyx/plone/persistentlogger/migrations/v1.py",
    "zopyx/plone/persistentlogger/profiles/default/metadata.xml",
    "zopyx/plone/persistentlogger/profiles/default/upgrades/to_2/registry.xml",
    "zopyx/plone/persistentlogger/profiles/default/upgrades/to_3/registry.xml",
    "zopyx/plone/persistentlogger/profiles/demo/metadata.xml",
    "zopyx/plone/persistentlogger/profiles/uninstall/registry.xml",
}
REQUIRED_DEPENDENCIES = {
    "btrees",
    "datetime",
    "loguru",
    "persistent",
    "plone-api",
    "plone-app-registry",
    "plone-protect",
    "plone-registry",
    "plone-z3cform",
    "products-cmfcore",
    "transaction",
    "zope2",
    "z3c-form",
    "zope-annotation",
    "zope-component",
    "zope-i18nmessageid",
    "zope-interface",
    "zope-lifecycleevent",
    "zope-schema",
}


def normalize_name(name: str) -> str:
    """Return the canonical comparison form for a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def archive_members(path: Path) -> set[str]:
    """Return archive members without the sdist's top-level directory."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    with tarfile.open(path, "r:gz") as archive:
        members = {member.name for member in archive.getmembers()}
    return {name.split("/", 1)[1] for name in members if "/" in name}


def wheel_metadata(path: Path) -> str:
    """Read the wheel metadata file."""
    with zipfile.ZipFile(path) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        return archive.read(metadata_name).decode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path in (args.wheel, args.sdist):
        if not path.is_file():
            raise SystemExit(f"missing distribution: {path}")
        missing = REQUIRED_FILES - archive_members(path)
        if missing:
            raise SystemExit(f"{path.name} is missing: {', '.join(sorted(missing))}")

    metadata = Parser().parsestr(wheel_metadata(args.wheel))
    actual = {
        normalize_name(value.split(" ", 1)[0])
        for value in metadata.get_all("Requires-Dist", [])
    }
    missing_dependencies = REQUIRED_DEPENDENCIES - actual
    if missing_dependencies:
        raise SystemExit(
            "wheel metadata is missing runtime dependencies: "
            + ", ".join(sorted(missing_dependencies))
        )
    print(
        f"package contents OK: {args.wheel.name}, {args.sdist.name}; "
        f"{len(REQUIRED_FILES)} resources and "
        f"{len(REQUIRED_DEPENDENCIES)} runtime dependencies"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
