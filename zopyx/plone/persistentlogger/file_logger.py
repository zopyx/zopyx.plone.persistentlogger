"""
Loguru based logging
"""

import sys

from loguru._logger import Core as _Core
from loguru._logger import Logger as _Logger

def new_logger():
    return _Logger(_Core(), None, 0, False, False, False, False, True, None, {})


DEFAULT_FMT = "{time:YYYYMMDDTHH:mm:ss} {level:8s} {message}"

def get_logger(
        prefix=None,
        log_stdout=True,
        rotation="1 week",
        log_as_json=False,
        fmt=DEFAULT_FMT,
        ):

    LOG = new_logger()
    if prefix:
        LOG.add(prefix+ ".log", rotation=rotation, format=fmt)
    if log_as_json:
        LOG.add(prefix+ ".json", rotation=rotation, serialize=True, format=fmt)
    if log_stdout:
        LOG.add(sys.stdout, format=fmt)

    return LOG

LOG = get_logger("coi-reviewer-notifications", log_as_json=True, log_stdout=False)
LOG.info("foo")

