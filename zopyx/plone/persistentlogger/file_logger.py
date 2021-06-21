"""
Loguru based logging
"""

import sys
from pathlib import Path

from loguru._logger import Core as _Core
from loguru._logger import Logger as _Logger

from App.config import getConfiguration

config = getConfiguration()
client_home = Path(config.clienthome)
log_home = client_home.parent / 'log'
log_home.mkdir(parents=True, exist_ok=True)

LOG_ROOT = log_home
DEFAULT_FMT = "{time:YYYYMMDDTHH:mm:ss} {level:8s} {message}"
DEFAULT_LEVEL = "INFO"


def new_logger():
    """ Create a new loguru.Logger instance """
    return _Logger(_Core(), None, 0, False, False, False, False, True, None, {})


def get_logger(
        prefix=None,
        log_stdout=True,
        rotation="1 week",
        retention="3 months",
        log_as_json=False,
        fmt=DEFAULT_FMT,
        log_root=LOG_ROOT,
        level=DEFAULT_LEVEL
        ):

    LOG = new_logger()
    if log_stdout:
        LOG.add(sys.stdout, format=fmt, level=level)
    if prefix:
        log_name = log_root / (prefix + ".log")
        LOG.add(log_name, rotation=rotation, format=fmt, retention=retention, level=level)
        LOG.info(f"Logfile added: {log_name}")
    if log_as_json:
        log_name = log_root / (prefix + ".json")
        LOG.add(log_name, rotation=rotation, serialize=True, format=fmt, retention=retention, level=level)
        LOG.info(f"Logfile added: {log_name}")
    return LOG
