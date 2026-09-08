# -*- coding: utf-8 -*-
"""Haptics are intentionally disabled for the Pyto RPG runtime.

V0.7.3.3:
High-frequency UIKit haptic bridge calls can introduce visible frame spikes in
Pyto even though the physical Taptic Engine effect itself is asynchronous.
To keep gameplay input deterministic and low-latency, this module is a strict
no-op and deliberately does not import UIKit or create feedback generators.

All existing callers may continue importing these functions; they return
immediately and never cross the Python <-> Objective-C bridge.
"""

HAPTICS_ENABLED = False


def impact(style=0):
    return None


def light():
    return None


def medium():
    return None


def heavy():
    return None


def selection():
    return None


def for_action(action):
    return None
