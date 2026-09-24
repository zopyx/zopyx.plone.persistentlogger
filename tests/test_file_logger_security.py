import json
from pathlib import Path

import pytest

from zopyx.plone.persistentlogger.file_logger import get_logger


@pytest.mark.parametrize(
    "prefix",
    ["../escape", "nested/name", "/tmp/absolute", ".", "..", r"..\escape"],
)
def test_rejects_prefixes_that_escape_log_root(tmp_path: Path, prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        get_logger(prefix=prefix, log_stdout=False, log_root=tmp_path)


def test_allows_a_safe_basename_below_log_root(tmp_path: Path) -> None:
    logger = get_logger(prefix="application", log_stdout=False, log_root=tmp_path)
    try:
        logger.info("ordinary message")
    finally:
        logger.remove()

    log_path = tmp_path / "application.log"
    assert log_path.is_file()
    assert log_path.parent == tmp_path
    assert "ordinary message" in log_path.read_text()


def test_redacts_common_secrets_from_text_and_json_sinks(tmp_path: Path) -> None:
    logger = get_logger(
        prefix="application",
        log_stdout=False,
        log_as_json=True,
        log_root=tmp_path,
    )
    try:
        logger.info(
            "password=supersecret api_key=key-123 Authorization: Bearer authvalue987"
        )
    finally:
        logger.remove()

    text = (tmp_path / "application.log").read_text()
    json_records = [
        json.loads(line)
        for line in (tmp_path / "application.json").read_text().splitlines()
    ]
    json_messages = [record["record"]["message"] for record in json_records]

    for secret in ("supersecret", "key-123", "authvalue987"):
        assert secret not in text
        assert all(secret not in message for message in json_messages)
    assert "[REDACTED]" in text
    assert any("[REDACTED]" in message for message in json_messages)
