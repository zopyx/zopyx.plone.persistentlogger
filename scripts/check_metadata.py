"""Validate project metadata against the locked Python matrix."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def normalize_name(name: str) -> str:
    """Return the canonical comparison form for a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_root(lock: dict[str, Any], project_name: str) -> dict[str, Any]:
    """Find the editable project entry in ``uv.lock``."""
    normalized = normalize_name(project_name)
    for package in lock["package"]:
        if normalize_name(package["name"]) == normalized:
            return package
    raise SystemExit(f"{project_name!r} is missing from uv.lock")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-version", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project_file = tomllib.load(stream)
    with (ROOT / "uv.lock").open("rb") as stream:
        lock_file = tomllib.load(stream)

    project = project_file["project"]
    expected_lock_python = f"=={args.python_version}.*"
    if project["requires-python"] != f">={args.python_version},<3.15":
        raise SystemExit(
            "pyproject.toml requires-python does not match the CI Python matrix"
        )
    if lock_file["requires-python"] != expected_lock_python:
        raise SystemExit("uv.lock requires-python does not match the CI Python matrix")

    root = package_root(lock_file, project["name"])
    if root["version"] != project["version"]:
        raise SystemExit("project version differs between pyproject.toml and uv.lock")

    declared = {
        normalize_name(re.split(r"[<>=!~ ]", item, maxsplit=1)[0])
        for item in project["dependencies"]
    }
    locked = {normalize_name(item["name"]) for item in root["dependencies"]}
    if declared != locked:
        missing = ", ".join(sorted(declared - locked))
        extra = ", ".join(sorted(locked - declared))
        raise SystemExit(
            f"locked runtime dependencies differ (missing={missing}; extra={extra})"
        )

    print(
        f"metadata OK: {project['name']} {project['version']} "
        f"for Python {args.python_version} ({len(declared)} runtime dependencies)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
