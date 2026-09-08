# -*- coding: utf-8 -*-
"""Pure-Python Metal control overlay used by FIX66.

The three rapidly-changing selectors (tool, magic and action route) no longer
own UIImageView or mutable UIButton artwork.  This module emits a tiny,
bounded list of ordinary Metal quads directly from the authoritative gameplay
state.  It deliberately imports neither PytoUI nor UIKit, so selection changes
cannot enqueue main-thread view mutations.
"""

import math


MAX_SELECTOR_QUADS = 96


def top_control_frames(viewport_w, safe_top, safe_right, ipad=False):
    """Shared safe-area-aware top row, including the right-side backpack.

    BackpackUI is intentionally separate from the two input backends.  Keeping
    this geometry authoritative prevents its button drifting into the close
    control, Dynamic Island, or an iPad system inset.
    """
    w = max(1.0, float(viewport_w))
    y = max(0.0, float(safe_top))
    right_inset = max(0.0, float(safe_right))
    top_h = 38.0
    close_w = 44.0
    utility_w = 38.0
    # Match the compact utility width so the added control stays to the right
    # of the centred iPhone boss bar as well as clear of the safe edge.
    backpack_w = 38.0
    gap = 7.0
    close_x = w - right_inset - close_w
    load_x = close_x - gap - utility_w
    save_x = load_x - gap - utility_w
    new_game_x = save_x - gap - utility_w
    backpack_x = new_game_x - gap - backpack_w
    return {
        "backpack": (backpack_x, y, backpack_w, top_h),
        "new_game": (new_game_x, y, utility_w, top_h),
        "save": (save_x, y, utility_w, top_h),
        "load": (load_x, y, utility_w, top_h),
        "close": (close_x, y, close_w, top_h),
    }


def selector_frames(viewport_w, viewport_h, ipad=False):
    """Return the exact screen-space frames used by touch hit-testing."""
    w = max(1.0, float(viewport_w))
    h = max(1.0, float(viewport_h))
    ipad = bool(ipad)
    b = min(84.0 if ipad else 72.0, h * (0.15 if ipad else 0.18))
    right = w - (28.0 if ipad else 18.0)
    small_w = b * 0.92
    small_h = b * 0.62
    tool_x = right - b * 4.05 - 28.0
    tool_y = h - b * 1.75 - 12.0
    return {
        "magic": (
            right - b * 3.1 - 24.0,
            h - b * 2.38 - 14.0,
            small_w,
            small_h,
        ),
        "tool": (tool_x, tool_y, small_w, small_h),
        "action_mode": (tool_x, tool_y + small_h + 4.0, small_w, small_h),
    }


def _quad(cx, cy, width, height, rgba):
    r, g, b, a = rgba
    return (
        float(cx), float(cy), float(width) * 0.5, float(height) * 0.5,
        float(r), float(g), float(b), float(a),
    )


def _frame(out, x, y, w, h, camera_x, camera_y, fill, border):
    cx = float(camera_x)
    cy = float(camera_y)
    x = float(x)
    y = float(y)
    w = max(1.0, float(w))
    h = max(1.0, float(h))
    # Hard two-pixel edge matches the retained UIKit controls.  Square Metal
    # quads avoid antialiased rounded corners and keep the UI deliberately
    # pixel-authored at every device scale.
    t = 2.0
    out.append(_quad(cx + x + w * 0.5, cy + y + h * 0.5, w, h, fill))
    out.extend((
        _quad(cx + x + w * 0.5, cy + y + t * 0.5, w, t, border),
        _quad(cx + x + w * 0.5, cy + y + h - t * 0.5, w, t, border),
        _quad(cx + x + t * 0.5, cy + y + h * 0.5, t, h, border),
        _quad(cx + x + w - t * 0.5, cy + y + h * 0.5, t, h, border),
    ))


def _normalise_tool(value):
    key = str(value or "").strip().lower()
    if key in ("axe", "斧", "斧頭"):
        return "axe"
    if key in ("grapple", "grappling_hook", "hook", "鉤爪"):
        return "grapple"
    return "pickaxe"


def _normalise_magic(value):
    key = str(value or "").strip().lower()
    aliases = {
        "fire": "fireball", "火球": "fireball",
        "water": "waterball", "水球": "waterball",
        "ice": "iceball", "冰球": "iceball",
        "electric": "electricball", "lightning": "electricball", "電球": "electricball",
    }
    return aliases.get(key, key if key in (
        "fireball", "waterball", "iceball", "electricball"
    ) else "fireball")


def _magic_panel_color(magic_id, alpha=0.90):
    magic_id = _normalise_magic(magic_id)
    if magic_id == "waterball":
        return (0.07, 0.31, 0.58, alpha)
    if magic_id == "iceball":
        return (0.18, 0.48, 0.67, alpha)
    if magic_id == "electricball":
        return (0.49, 0.35, 0.05, alpha)
    return (0.53, 0.10, 0.08, alpha)


def _pixel(out, x, y, dx, dy, w, h, color):
    out.append(_quad(float(x) + float(dx), float(y) + float(dy), w, h, color))


def _tool_icon(out, tool_id, x, y, scale=1.0):
    """Pixel pickaxe/axe made from a bounded number of rectangular cells."""
    s = max(0.55, float(scale))
    dark = (0.08, 0.10, 0.13, 1.0)
    wood = (0.54, 0.29, 0.12, 1.0)
    wood_hi = (0.75, 0.45, 0.18, 1.0)
    steel = (0.67, 0.78, 0.86, 1.0)
    edge = (0.92, 0.98, 1.0, 1.0)
    tool_id = _normalise_tool(tool_id)

    # Stepped diagonal handle; axis-aligned quads retain hard pixel edges.
    for i in range(6):
        _pixel(out, x, y, (-7.5 + i * 2.6) * s, (8.0 - i * 2.7) * s,
               3.2 * s, 4.1 * s, wood if i % 2 else wood_hi)
    if tool_id == "grapple":
        rope = (0.72, 0.50, 0.24, 1.0)
        # Compact rope coil and a three-prong hook.  All pieces are rectangular
        # pixel cells so the silhouette stays crisp in Metal.
        for dx, dy in ((-8, 6), (-5, 8), (-1, 8), (3, 6), (5, 3)):
            _pixel(out, x, y, dx * s, dy * s, 4.0 * s, 3.2 * s, rope)
        _pixel(out, x, y, 6.0 * s, -1.0 * s, 3.2 * s, 8.0 * s, steel)
        _pixel(out, x, y, 6.0 * s, -6.0 * s, 7.0 * s, 3.2 * s, edge)
        _pixel(out, x, y, 1.5 * s, -7.5 * s, 3.2 * s, 7.0 * s, steel)
        _pixel(out, x, y, 10.5 * s, -7.5 * s, 3.2 * s, 7.0 * s, steel)
    elif tool_id == "axe":
        _pixel(out, x, y, 5.8 * s, -7.4 * s, 11.0 * s, 7.0 * s, dark)
        _pixel(out, x, y, 4.8 * s, -8.0 * s, 9.0 * s, 6.0 * s, steel)
        _pixel(out, x, y, 9.0 * s, -7.2 * s, 3.0 * s, 8.5 * s, edge)
    else:
        _pixel(out, x, y, 0.0, -8.0 * s, 20.0 * s, 4.2 * s, dark)
        _pixel(out, x, y, 0.0, -8.7 * s, 18.0 * s, 3.0 * s, steel)
        _pixel(out, x, y, -8.7 * s, -6.5 * s, 3.4 * s, 6.5 * s, edge)
        _pixel(out, x, y, 8.7 * s, -6.5 * s, 3.4 * s, 6.5 * s, edge)


def _magic_icon(out, magic_id, x, y, scale=1.0):
    s = max(0.55, float(scale))
    magic_id = _normalise_magic(magic_id)
    dark = (0.08, 0.10, 0.13, 0.95)
    if magic_id == "waterball":
        main = (0.20, 0.72, 1.0, 1.0)
        hi = (0.78, 0.96, 1.0, 1.0)
        cells = ((0,-8,4,5),(-3,-4,8,6),(-5,1,11,7),(-3,6,8,5))
        for dx,dy,w,h in cells:
            _pixel(out,x,y,dx*s,dy*s,w*s,h*s,main)
        _pixel(out,x,y,-2*s,-2*s,3*s,5*s,hi)
    elif magic_id == "iceball":
        main = (0.52, 0.91, 1.0, 1.0)
        hi = (0.92, 1.0, 1.0, 1.0)
        _pixel(out,x,y,0,0,4*s,22*s,main)
        _pixel(out,x,y,0,0,22*s,4*s,main)
        for dx,dy in ((-7,-7),(7,7),(-7,7),(7,-7)):
            _pixel(out,x,y,dx*s,dy*s,4*s,4*s,main)
        _pixel(out,x,y,0,0,5*s,5*s,hi)
    elif magic_id == "electricball":
        main = (1.0, 0.82, 0.18, 1.0)
        hi = (1.0, 0.98, 0.66, 1.0)
        for dx,dy in ((-4,-8),(-1,-4),(-3,0),(1,4),(-1,8)):
            _pixel(out,x,y,dx*s,dy*s,6*s,5*s,main)
        _pixel(out,x,y,2*s,-8*s,3*s,5*s,hi)
    else:
        main = (1.0, 0.31, 0.08, 1.0)
        mid = (1.0, 0.65, 0.10, 1.0)
        hi = (1.0, 0.93, 0.39, 1.0)
        _pixel(out,x,y,0,4*s,13*s,13*s,dark)
        _pixel(out,x,y,0,3*s,11*s,13*s,main)
        _pixel(out,x,y,-3*s,-4*s,7*s,11*s,main)
        _pixel(out,x,y,3*s,-7*s,6*s,12*s,mid)
        _pixel(out,x,y,0,4*s,5*s,7*s,hi)


def _weapon_icon(out, weapon, x, y, scale=1.0):
    """Compact weapon silhouettes matching the Metal inventory vocabulary."""
    s = max(0.52, float(scale))
    dark=(.09,.13,.18,1.0);steel=(.68,.79,.88,1.0);edge=(.90,.98,1.0,1.0)
    gold=(.94,.63,.17,1.0);cyan=(.20,.86,1.0,1.0);brown=(.47,.26,.12,1.0)
    def q(dx,dy,w,h,c): _pixel(out,x,y,dx*s,dy*s,w*s,h*s,c)
    wid=str(weapon or "sword")
    # FIX88: bounded selector silhouettes. Backpack and drops use the full
    # shared authored icon; these miniatures keep the 96-quad HUD limit.
    if wid == "quake_hammer":
        q(-3,5,5,26,brown);q(-3,4,2,25,gold);q(1,-9,27,13,dark)
        q(1,-9,23,9,steel);q(-9,-9,4,13,gold);q(11,-9,4,13,gold)
        q(0,-9,5,5,cyan);return
    if wid == "pressure_cannon":
        q(-1,-2,23,13,dark);q(-5,-2,13,9,(.14,.39,.65,1.))
        q(-5,-3,9,4,cyan);q(10,-2,17,6,steel);q(18,-2,4,9,cyan)
        q(-8,8,6,11,brown);q(-8,-12,10,5,steel);return
    if wid == "alien_cannon":
        q(-1,-3,26,12,dark);q(-2,-3,23,8,(.47,.22,.73,1.))
        q(3,-3,10,5,(.36,1.,.53,1.));q(14,-3,8,9,steel)
        q(-8,7,7,11,(.36,.20,.52,1.));q(-5,-10,11,4,cyan);return
    if wid == "bee_swarm":
        for dx,dy in ((-9,4),(8,5),(0,-7)):
            q(dx-2,dy-5,9,4,(.72,.93,1.,1.));q(dx,dy,13,7,gold)
            q(dx,dy,3,7,dark);q(dx+6,dy-1,3,3,dark)
        return
    if wid == "master_sword":
        q(1,-3,31,5,(.27,.86,.73,1.));q(3,-4,29,2,edge)
        q(-10,-3,3,13,gold);q(-15,-3,9,4,brown)
        q(-16,3,3,8,(.76,.15,.24,1.));return
    if wid=="flying_drone":
        q(0,0,18,8,dark);q(0,-1,9,7,cyan);q(-12,-6,16,2,steel);q(12,-6,16,2,steel)
        q(-12,-6,3,7,cyan);q(12,-6,3,7,cyan);q(0,5,4,4,gold);return
    if wid=="energy_bow":
        for off in (-10,-6,-2,2,6,10):q(abs(off)*.26-3,off,3,3,cyan)
        q(1,0,2,24,edge);q(7,0,12,2,cyan);return
    if wid=="whip":
        for i in range(8):q(-11+i*3.0,7-math.sin(i*.55)*12,3.2,3.2,brown)
        q(-13,9,5,8,gold);return
    if wid=="laser_gun":
        q(0,-2,24,8,dark);q(5,-2,12,4,cyan);q(-6,6,7,10,brown);q(14,-2,4,4,edge);return
    if wid=="yoyo":
        for i in range(6):q(-8+i*2.5,-8+i*2.2,2.4,2.4,steel)
        q(7,7,15,15,dark);q(7,7,11,11,(.72,.20,.78,1.0));q(7,7,4,4,edge);return
    if wid=="battle_top":
        q(0,-8,5,5,gold);q(0,-3,17,5,edge);q(0,2,23,8,(.85,.28,.12,1.0))
        q(0,7,13,5,steel);q(0,12,3,6,gold);return
    if wid=="rpg_launcher":
        q(0,-3,29,9,dark);q(2,-4,24,5,(.30,.52,.32,1.0));q(15,-4,5,10,edge)
        q(-11,5,7,12,brown);q(-4,5,8,4,gold);return
    if wid=="battle_axe":
        q(-5,3,4,26,brown);q(3,-8,15,9,steel);q(7,-8,5,13,edge);return
    if wid=="war_hammer":
        q(-4,4,4,25,brown);q(2,-8,23,10,steel);q(13,-8,4,12,edge);return
    length=28 if wid in ("greatsword","spear") else (19 if wid=="dagger" else 23)
    thick=4 if wid=="greatsword" else (2.5 if wid=="spear" else 3)
    q(0,-2,length,thick,steel);q(length*.48,-2,4,4,edge);q(-length*.46,-2,7,5,brown)
    if wid!="spear":q(-length*.30,-2,3,11,gold)


def build_selector_overlay(
    viewport_w,
    viewport_h,
    camera_x,
    camera_y,
    tool_id="pickaxe",
    magic_id="fireball",
    action_mode="tool",
    weapon_id="sword",
    ipad=False,
):
    """Build all three panels and icons with a strict fixed upper bound."""
    frames = selector_frames(viewport_w, viewport_h, ipad=ipad)
    out = []
    tool_id = _normalise_tool(tool_id)
    magic_id = _normalise_magic(magic_id)
    action_mode = str(action_mode or "tool").strip().lower()

    panel_border = (0.62, 0.75, 0.82, 0.98)
    tool_fill = (0.38, 0.25, 0.06, 0.91)
    magic_fill = _magic_panel_color(magic_id, 0.91)
    mode_fill = {
        "weapon": (0.28, 0.12, 0.39, 0.92),
        "magic": _magic_panel_color(magic_id, 0.92),
        "tool": (0.09, 0.14, 0.19, 0.92),
    }.get(action_mode, (0.09, 0.14, 0.19, 0.92))

    for key, fill in (("tool", tool_fill), ("magic", magic_fill), ("action_mode", mode_fill)):
        x, y, w, h = frames[key]
        _frame(out, x, y, w, h, camera_x, camera_y, fill, panel_border)

    def center(key):
        x, y, w, h = frames[key]
        return float(camera_x) + x + w * 0.5, float(camera_y) + y + h * 0.5

    tx, ty = center("tool")
    mx, my = center("magic")
    ax, ay = center("action_mode")
    # Scale follows the smaller button dimension and is intentionally capped.
    icon_scale = max(0.72, min(1.18, min(frames["tool"][2], frames["tool"][3]) / 40.0))
    _tool_icon(out, tool_id, tx, ty, icon_scale)
    _magic_icon(out, magic_id, mx, my, icon_scale)
    if action_mode == "weapon":
        _weapon_icon(out, weapon_id, ax, ay, icon_scale)
    elif action_mode == "magic":
        _magic_icon(out, magic_id, ax, ay, icon_scale)
    else:
        _tool_icon(out, tool_id, ax, ay, icon_scale)

    return tuple(out[:MAX_SELECTOR_QUADS])
