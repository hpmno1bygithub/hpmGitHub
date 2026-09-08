# -*- coding: utf-8 -*-
"""Shared editor/game traversal rules for authored overlay tiles (FIX96).

No inference from a filename or pixel colours: an explicit role is saved in
assets/custom_map_assets.json. Existing solid/passable/background data remains
valid. These small dictionaries never hold image data or per-pixel UI objects.
"""
TILE_MAP_ROLES = (
    ('solid', '碰撞：實體', '完整實體：可以站立，不能穿過。'),
    ('platform', '單向平台', '可站立；側面／由下往上可穿越，不提供攀爬。'),
    ('climb_platform', '平台＋攀爬', '可站立，也可上下攀爬穿越。站在上方往下推可接下方藤蔓／梯子；放手會停住。'),
    ('ladder', '攀爬：藤／梯', '可上下攀爬，不提供頂面站立支撐。'),
    ('passable', '碰撞：可穿越', '只有圖像，可穿越；不提供站立或攀爬。'),
    ('background', '背景：可穿越', '背景圖像，不提供站立或攀爬。'),
)
ROLE_IDS = frozenset(row[0] for row in TILE_MAP_ROLES)
SUPPORT_ROLES = frozenset(('solid', 'platform', 'climb_platform'))
PLATFORM_ROLES = frozenset(('platform', 'climb_platform'))
LADDER_ROLES = frozenset(('ladder', 'climb_platform'))


def role_from_meta(meta):
    """Read old and new metadata without treating an unknown role as solid."""
    if not isinstance(meta, dict):
        return 'passable'
    role = str(meta.get('role', '')).strip().lower()
    if role in ROLE_IDS:
        return role
    if meta.get('one_way_platform'):
        return 'climb_platform' if meta.get('ladder') else 'platform'
    if meta.get('ladder'):
        return 'ladder'
    if meta.get('collision') == 'solid' or meta.get('solid'):
        return 'solid'
    return 'passable'


def collision_fields(role):
    role = str(role).strip().lower()
    if role not in ROLE_IDS:
        raise ValueError('未知圖塊碰撞設定：' + role)
    return {
        'role': role,
        'collision': 'solid' if role == 'solid' else ('platform' if role in PLATFORM_ROLES else 'passable'),
        'solid': role in SUPPORT_ROLES,
        'one_way_platform': role in PLATFORM_ROLES,
        'ladder': role in LADDER_ROLES,
    }


def role_label(role):
    return next((label for key, label, _ in TILE_MAP_ROLES if key == role), '碰撞：可穿越')


def role_description(role):
    return next((text for key, _, text in TILE_MAP_ROLES if key == role), '')
