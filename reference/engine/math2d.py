# -*- coding: utf-8 -*-
import math


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rects_overlap(a, b):
    return not (
        a[2] <= b[0]
        or a[0] >= b[2]
        or a[3] <= b[1]
        or a[1] >= b[3]
    )


def distance(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)
