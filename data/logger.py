# -*- coding: utf-8 -*-
import logging
import sys

def setup_logger(name: str = "Core"):
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    return log

logger = setup_logger()
