# -*- coding: utf-8 -*-
"""Tiny persistent startup trace for native-crash diagnosis on Pyto/iOS."""
import os
import time
import threading

_PROJECT_ROOT = None
_LOCK = threading.Lock()


def configure(project_root):
    global _PROJECT_ROOT
    try:
        _PROJECT_ROOT = os.path.abspath(str(project_root))
    except Exception:
        _PROJECT_ROOT = None


def _path():
    root = _PROJECT_ROOT
    if not root:
        try:
            root = os.environ.get("PYTO_RPG_PROJECT_ROOT")
        except Exception:
            root = None
    if not root:
        try:
            root = os.getcwd()
        except Exception:
            return None
    return os.path.join(root, "startup_trace.log")


def reset():
    p = _path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("Pyto RPG startup trace\n")
            f.flush()
    except Exception:
        pass


def trace(stage, detail=""):
    p = _path()
    if not p:
        return
    line = "%.3f | %s" % (time.time(), str(stage))
    if detail:
        line += " | " + str(detail)
    line += "\n"
    try:
        with _LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception:
        pass
