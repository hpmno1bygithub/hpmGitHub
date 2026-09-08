# -*- coding: utf-8 -*-
"""Shared fixed-density pixel-art geometry for FIX20.

This module is deliberately UIKit/Metal-free so the editor, runtime renderer,
and tests use the exact same pivots and world-pixel scale.
"""
from config import PIXEL_WORLD_SCALE

PIXEL_MAPPING_FIXED = "fixed_world_pixel"
PIXEL_MAPPING_FIT = "fit_world_box"
FEET_ANCHORED_CATEGORIES = ("creature", "plant", "player", "equipment", "decoration")


def source_anchor_px(category, size):
    """Return the logical source pivot in authored-pixel coordinates."""
    n = float(max(1, int(size)))
    category = str(category or "")
    if category == "tile":
        return (0.0, 0.0)
    if category in FEET_ANCHORED_CATEGORIES:
        return (n * 0.5, n)
    return (n * 0.5, n * 0.5)


def world_anchor_xy(category, left, top, width, height):
    """Return the world point to which the source pivot is attached."""
    category = str(category or "")
    left = float(left); top = float(top)
    width = float(width); height = float(height)
    if category == "tile":
        return (left, top)
    if category in FEET_ANCHORED_CATEGORIES:
        return (left + width * 0.5, top + height)
    return (left + width * 0.5, top + height * 0.5)


def canvas_world_span(size):
    return float(max(1, int(size))) * float(PIXEL_WORLD_SCALE)


def fixed_rect_to_world(category, size, left, top, width, height,
                        x0, y0, x1, y1, flip_x=False):
    """Map one authored pixel rectangle to fixed-density world coordinates."""
    n = float(max(1, int(size)))
    px0 = float(x0); px1 = float(x1)
    py0 = float(y0); py1 = float(y1)
    if flip_x:
        px0, px1 = n - px1, n - px0
    sax, say = source_anchor_px(category, n)
    wax, way = world_anchor_xy(category, left, top, width, height)
    scale = float(PIXEL_WORLD_SCALE)
    return (
        wax + (px0 - sax) * scale,
        way + (py0 - say) * scale,
        wax + (px1 - sax) * scale,
        way + (py1 - say) * scale,
    )


def fit_rect_to_world(size, left, top, width, height, x0, y0, x1, y1, flip_x=False):
    """Map a logical pixel rectangle into an explicit world-space box.

    FIX111 separates *logical source resolution* from *gameplay/world size*.
    A 24x24 boss can therefore fill a 180x86 collision/visual box without
    pretending to be a 96x96 authored-pixel source.  This is a pure nearest
    pixel mapping: source cells remain discrete; only their world-space quad
    size changes.
    """
    n = float(max(1, int(size)))
    px0=float(x0); px1=float(x1); py0=float(y0); py1=float(y1)
    if flip_x:
        px0, px1 = n-px1, n-px0
    l=float(left); t=float(top); w=float(width); h=float(height)
    return (
        l + (px0/n)*w,
        t + (py0/n)*h,
        l + (px1/n)*w,
        t + (py1/n)*h,
    )
