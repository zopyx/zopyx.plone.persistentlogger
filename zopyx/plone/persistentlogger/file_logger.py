"""
Loguru based logging
"""

import sys
from pathlib import Path

from App.config import getConfiguration
from loguru import logger as _global_logger

config = getConfiguration()
client_home = Path(config.clienthome)
log_home = client_home.parent / "log"
log_home.mkdir(parents=True, exist_ok=True)

LOG_ROOT = log_home
DEFAULT_FMT = "{time:YYYYMMDDTHH:mm:ss} {level:8s} {message}"
DEFAULT_LEVEL = "INFO"


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
    return _Logger(_Core(), None, 0, False, False, False, False, True, None, {})


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
    if log_stdout:
        LOG.add(sys.stdout, format=fmt, level=level)
    if prefix:
        log_name = log_root / (prefix + ".log")
        LOG.add(
            log_name, rotation=rotation, format=fmt, retention=retention, level=level
        )
        LOG.info(f"Logfile added: {log_name}")
    if log_as_json and prefix:
        log_name = log_root / (prefix + ".json")
        LOG.add(
            log_name,
            rotation=rotation,
            serialize=True,
            format=fmt,
            retention=retention,
            level=level,
        )
        LOG.info(f"Logfile added: {log_name}")
    return LOG
