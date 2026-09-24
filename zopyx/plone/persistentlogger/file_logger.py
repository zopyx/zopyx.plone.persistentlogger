"""
Loguru based logging
"""

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from App.config import getConfiguration
from loguru import logger as _global_logger

if TYPE_CHECKING:
    from loguru import Record

config = getConfiguration()
client_home = Path(config.clienthome)
log_home = client_home.parent / "log"
log_home.mkdir(parents=True, exist_ok=True)

LOG_ROOT = log_home
DEFAULT_FMT = "{time:YYYYMMDDTHH:mm:ss} {level:8s} {message}"
DEFAULT_LEVEL = "INFO"

_SECRET_PATTERN = re.compile(
    r"""
    (?P<prefix>
        (?<![A-Za-z0-9_])(?:password|passwd|pwd|passphrase|secret|private[ _-]?key|
        api[ _-]?key|access[ _-]?(?:key|token)|refresh[ _-]?token|
        auth(?:entication)?[ _-]?token|client[ _-]?secret|token)(?![A-Za-z0-9_])
        [ \t]*["']?[ \t]*[:=][ \t]*
    )
    (?:
        (?P<quote>["'])(?P<quoted>.*?)(?P=quote)
        |(?P<unquoted>[^ \t,;&}]+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_AUTHORIZATION_PATTERN = re.compile(
    r"""
    (?P<prefix>(?<![A-Za-z0-9_])(?:authorization|proxy-authorization)
        (?![A-Za-z0-9_])[ \t]*["']?[ \t]*[:=][ \t]*
    )
    (?:
        (?P<quote>["'])(?P<scheme>(?:bearer|basic|token)[ \t]+)?
        (?P<quoted_secret>.*?)(?P=quote)
        |
        (?P<unquoted_scheme>(?:bearer|basic|token)[ \t]+)?
        (?P<unquoted_secret>[^ \t,;&}]+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _redact_match(match: re.Match[str]) -> str:
    quote = match.groupdict().get("quote")
    return f"{match.group('prefix')}{quote or ''}[REDACTED]{quote or ''}"


def _redact_authorization(match: re.Match[str]) -> str:
    groups = match.groupdict()
    quote = groups.get("quote") or ""
    scheme = groups.get("scheme") or groups.get("unquoted_scheme") or ""
    return f"{match.group('prefix')}{quote}{scheme}[REDACTED]{quote}"


def _redact_secrets(record: "Record") -> bool:
    """Redact common key/value secrets while retaining the log record shape."""
    message = record.get("message")
    if isinstance(message, str):
        message = _AUTHORIZATION_PATTERN.sub(_redact_authorization, message)
        record["message"] = _SECRET_PATTERN.sub(_redact_match, message)
    return True


def _safe_log_path(log_root: Path, prefix: str, suffix: str) -> Path:
    if (
        not isinstance(prefix, str)
        or not prefix
        or prefix in {".", ".."}
        or "/" in prefix
        or chr(92) in prefix
        or chr(0) in prefix
    ):
        raise ValueError("prefix must be a safe basename")

    root = log_root.resolve()
    log_path = log_root / f"{prefix}{suffix}"
    resolved_log_path = log_path.resolve()
    try:
        resolved_log_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("prefix must be confined below LOG_ROOT") from exc
    return log_path


def new_logger():
    """Create a new loguru.Logger instance isolated from the global logger.

    loguru's public API deliberately exposes only one logger, so a fresh
    ``Logger`` with its own ``Core`` requires the private constructor.
    ``bind()`` is used as a fallback: it derives a new instance but shares
    the global core, which means sinks added later would be visible
    globally. The private path keeps sinks instance-local; if a loguru
    upgrade changes the private API, the fallback keeps this package
    functional.
    """
    try:
        from loguru._logger import Core as _Core
        from loguru._logger import Logger as _Logger
    except (ImportError, AttributeError):
        return _global_logger.bind()
    return _Logger(_Core(), None, 0, False, False, False, False, True, [], {})


def get_logger(
    prefix=None,
    log_stdout=True,
    rotation="1 week",
    retention="3 months",
    log_as_json=False,
    fmt=DEFAULT_FMT,
    log_root=LOG_ROOT,
    level=DEFAULT_LEVEL,
):
    LOG = new_logger()
    log_name = None
    json_log_name = None
    if prefix:
        log_name = _safe_log_path(Path(log_root), prefix, ".log")
        if log_as_json:
            json_log_name = _safe_log_path(Path(log_root), prefix, ".json")

    if log_stdout:
        LOG.add(sys.stdout, format=fmt, level=level, filter=_redact_secrets)
    if log_name is not None:
        LOG.add(
            log_name,
            rotation=rotation,
            format=fmt,
            retention=retention,
            level=level,
            filter=_redact_secrets,
        )
        LOG.info(f"Logfile added: {log_name}")
    if json_log_name is not None:
        LOG.add(
            json_log_name,
            rotation=rotation,
            serialize=True,
            format=fmt,
            retention=retention,
            level=level,
            filter=_redact_secrets,
        )
        LOG.info(f"Logfile added: {json_log_name}")
    return LOG
