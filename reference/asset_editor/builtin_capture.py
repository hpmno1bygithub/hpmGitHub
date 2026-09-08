# -*- coding: utf-8 -*-
"""Lightweight capture of the game's current built-in editable art.

This module intentionally does NOT import the Metal renderer/UIKit.  It mirrors
its normal-state rectangle geometry so AssetEditor can pull the actual default
look without booting Metal or constructing a game scene on the UI thread.
"""
import math

from world.tile_registry import TILES, AIR, ICE, GRASS_DIRT, LAYERED_SOLID_TILES
from config import PIXEL_WORLD_SCALE
from engine.pixel_art import source_anchor_px
from asset_editor.creature_pixel_art import creature_default_grid
try:
    from systems.biome_system import CREATURE_ARCHETYPES
except Exception:
    CREATURE_ARCHETYPES = {}

TRANSPARENT = "#00000000"

CREATURE_COLORS = {
    "slime":"#63C56E", "boar":"#9B6845", "wolf":"#777B82", "deer":"#A8794E",
    "gull":"#E6EAEE", "shore_crab":"#C36F49", "sea_turtle":"#5C8D56",
    "heron":"#B8C7CF", "otter":"#8C6A4C", "mangrove_crab":"#A85E3F",
    "desert_scorpion":"#8A6139", "sand_lizard":"#A28C51", "desert_snake":"#C2A55E",
    "desert_beetle":"#72543B", "swamp_slime":"#65894B", "crocodile":"#496B3E",
    "swamp_snake":"#607A42", "swamp_frog":"#73A85C", "village_rat":"#7B7169",
    "bandit":"#8B4E45", "village_dog":"#9A774D", "villager":"#B88963",
    "jungle_spider":"#493B4F", "jaguar":"#C38A39", "jungle_snake":"#4F8A46",
    "capybara":"#8C6A4C", "snow_wolf":"#CED7DE", "mountain_goat":"#D0C4A8",
    "ice_slime":"#8FD6E7", "snow_hare":"#E3E8EB", "sardine":"#8BB8C7",
    "mackerel":"#5D8FA5", "sea_bass":"#6FA09A", "carp":"#B69058",
    "crucian_carp":"#A99C70", "freshwater_bass":"#6F8B63", "mallard":"#667B52",
    "kingfisher":"#4C8FA8", "dragonfly":"#77A7A3", "damselfly":"#7F90B8",
}


def _hex(value):
    s = str(value or "#000000").strip().upper()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 7:
        s += "FF"
    return s if len(s) == 9 else "#FF00FFFF"


def _rgb_tuple(hex_value):
    s = _hex(hex_value)[1:7]
    return tuple(int(s[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def _shade(hex_value, delta):
    r, g, b = _rgb_tuple(hex_value)
    vals = [max(0.0, min(1.0, v + float(delta))) for v in (r, g, b)]
    return "#%02X%02X%02XFF" % tuple(int(round(v * 255.0)) for v in vals)


def _rgba_float(r, g, b, a=1.0):
    return "#%02X%02X%02X%02X" % (
        int(max(0, min(255, round(float(r) * 255)))),
        int(max(0, min(255, round(float(g) * 255)))),
        int(max(0, min(255, round(float(b) * 255)))),
        int(max(0, min(255, round(float(a) * 255)))),
    )


def _quad(cx, cy, w, h, color):
    return (float(cx), float(cy), max(0.0, float(w)), max(0.0, float(h)), _hex(color))


def _anchor_px(category, size):
    return source_anchor_px(category,size)


def _rasterize_fixed(quads, size, category):
    """Rasterize world-space quads without normalizing their size.

    FIX20's invariant is 1 authored pixel == PIXEL_WORLD_SCALE world pixels.
    Quads are therefore quantized into the logical canvas around a stable
    category pivot instead of being fitted/stretched to the requested NxN box.
    """
    n=max(1,int(size))
    grid=[[TRANSPARENT for _ in range(n)] for _ in range(n)]
    rows=[q for q in (quads or []) if len(q)>=5 and float(q[2])>0.0 and float(q[3])>0.0]
    if not rows:
        return grid
    scale=max(1e-6,float(PIXEL_WORLD_SCALE))
    ax,ay=_anchor_px(category,n)
    for cx,cy,w,h,color in rows:
        # Cover every logical pixel cell touched by the original world quad.
        # floor/ceil avoids losing thin 2-3 world-pixel accents during import.
        x0=int(math.floor(ax + (float(cx)-float(w)*0.5)/scale + 1e-9))
        y0=int(math.floor(ay + (float(cy)-float(h)*0.5)/scale + 1e-9))
        x1=int(math.ceil (ax + (float(cx)+float(w)*0.5)/scale - 1e-9))
        y1=int(math.ceil (ay + (float(cy)+float(h)*0.5)/scale - 1e-9))
        x0=max(0,min(n,x0)); y0=max(0,min(n,y0))
        x1=max(x0,min(n,x1)); y1=max(y0,min(n,y1))
        if x1<=x0 or y1<=y0:
            continue
        c=_hex(color)
        for yy in range(y0,y1):
            row=grid[yy]
            for xx in range(x0,x1):
                row[xx]=c
    return grid


def tile_quads(name):
    td = next((v for v in TILES.values() if str(getattr(v, "name", "")) == str(name)), None)
    if td is None or int(getattr(td, "tile_id", AIR)) == AIR:
        return []
    body = _hex(getattr(td, "color", "#777777"))
    topc = _hex(getattr(td, "top_color", getattr(td, "color", "#777777")))
    q = []
    # Match Metal _tile_render_batch() for a full-height default tile.
    if bool(getattr(td, "ladder", False)):
        q += [
            _quad(40*0.33, 20, 5, 40, body),
            _quad(40*0.67, 20, 5, 40, body),
        ]
        for frac in (0.22, 0.52, 0.82):
            q.append(_quad(20, 40*frac, 40*0.38, 4, topc))
    elif int(getattr(td, "tile_id", -1)) == ICE:
        q += [_quad(20, 20, 40, 40, body), _quad(20, 2.5, 36, 4, topc)]
    else:
        q.append(_quad(20, 20, 40, 40, body))
        if int(getattr(td, "tile_id", -1)) == GRASS_DIRT or topc != body:
            q.append(_quad(20, 3.5, 40, 7, topc))
    return q


def creature_quads(species):
    cfg = CREATURE_ARCHETYPES.get(str(species), {}) if isinstance(CREATURE_ARCHETYPES, dict) else {}
    w = float(cfg.get("w", 32) or 32); h = float(cfg.get("h", 24) or 24)
    base = CREATURE_COLORS.get(str(species), "#63C56E")
    dark = _shade(base, -0.12); light = _shade(base, 0.12)
    cx = 0.0; cy = -h*0.5
    q = [_quad(cx, cy, w, h, base)]
    sp = str(species)
    if sp in ("desert_snake", "swamp_snake", "jungle_snake"):
        q += [_quad(cx-w*0.22,cy-h*0.08,w*0.24,h*0.20,light), _quad(cx+w*0.28,cy-h*0.22,w*0.16,h*0.16,light)]
    elif sp in ("desert_scorpion","desert_beetle","jungle_spider","mangrove_crab","shore_crab"):
        q += [_quad(cx-w*0.38,cy+h*0.10,w*0.18,3,dark), _quad(cx+w*0.38,cy+h*0.10,w*0.18,3,dark), _quad(cx,cy-h*0.34,w*0.20,h*0.18,dark)]
    elif sp == "swamp_frog":
        q += [_quad(cx-w*0.20,cy+h*0.22,w*0.18,h*0.18,dark), _quad(cx+w*0.20,cy+h*0.22,w*0.18,h*0.18,dark)]
    elif sp == "crocodile":
        q += [_quad(cx-w*0.32,cy-h*0.08,w*0.36,h*0.20,dark), _quad(cx+w*0.32,cy+h*0.06,w*0.26,h*0.16,light)]
    elif sp in ("wolf","snow_wolf","village_dog","jaguar","boar","deer","mountain_goat","capybara","otter","sea_turtle"):
        q += [_quad(cx-w*0.30,cy-h*0.18,w*0.22,h*0.26,light), _quad(cx+w*0.24,cy-h*0.06,w*0.26,h*0.16,dark)]
        if sp in ("deer","mountain_goat"):
            q += [_quad(cx-w*0.34,cy-h*0.34,3,h*0.18,dark), _quad(cx-w*0.26,cy-h*0.34,3,h*0.16,dark)]
        if sp == "sea_turtle": q.append(_quad(cx,cy,w*0.72,h*0.72,dark))
    elif sp in ("sardine","mackerel","sea_bass","carp","crucian_carp","freshwater_bass"):
        q += [_quad(cx,cy,w*0.78,h*0.68,light), _quad(cx-w*0.55,cy,w*0.22,h*0.55,dark), _quad(cx,cy-h*0.34,w*0.22,2,dark)]
    elif sp in ("mallard","kingfisher"):
        q += [_quad(cx,cy,w*0.72,h*0.58,light), _quad(cx+w*0.34,cy-h*0.14,w*0.22,h*0.28,dark), _quad(cx-w*0.08,cy+h*0.10,w*0.34,3,dark)]
    elif sp in ("dragonfly","damselfly"):
        q += [_quad(cx,cy,w*0.46,max(2,h*0.35),dark), _quad(cx-w*0.22,cy-h*0.10,w*0.42,2,light), _quad(cx+w*0.22,cy+h*0.10,w*0.42,2,light)]
    elif sp in ("gull","heron"):
        q += [_quad(cx,cy,w*0.86,h*0.42,light), _quad(cx-w*0.14,cy-h*0.28,w*0.12,h*0.22,light), _quad(cx+w*0.36,cy-h*0.02,w*0.18,3,dark)]
    elif sp in ("slime","swamp_slime","ice_slime"):
        q.append(_quad(cx,cy-h*0.10,w*0.60,h*0.34,light))
    return q


def plant_quads(species, level=3):
    sp = str(species); level=max(1,min(3,int(level)))
    bx=0.0; gy=0.0; q=[]
    if sp == "map_grass":
        height={1:11.,2:21.,3:32.}[level]; width={1:7.,2:10.,3:13.}[level]
        q.append(_quad(bx,gy-height*0.5,width,height,_rgba_float(.22,.62,.24)))
        if level>=2: q.append(_quad(bx-6,gy-height*.58,8,5,_rgba_float(.28,.70,.27)))
        if level>=3: q.append(_quad(bx+7,gy-height*.72,9,5,_rgba_float(.28,.70,.27)))
    elif sp == "map_reed":
        stem_h={1:18.,2:28.,3:38.}[level]; offs=(-5.,0.,5.) if level>=2 else (-2.,2.)
        for off in offs:
            sh=stem_h*(.86 if off<0 else (1.0 if off==0 else .90))
            q += [_quad(bx+off,gy-sh*.5,3,sh,_rgba_float(.47,.58,.20)), _quad(bx+off,gy-sh+4,4.6,max(5.,sh*.24),_rgba_float(.44,.28,.13))]
        if level>=3: q += [_quad(bx-8,gy-stem_h*.44,6,3,_rgba_float(.58,.70,.28)), _quad(bx+8,gy-stem_h*.52,6,3,_rgba_float(.58,.70,.28))]
    elif sp == "map_fern":
        fronds={1:3,2:5,3:7}[level]; spread={1:8.,2:12.,3:16.}[level]; fh0={1:13.,2:18.,3:24.}[level]
        q.append(_quad(bx,gy-4,4,8,_rgba_float(.14,.36,.18)))
        for i in range(fronds):
            frac=0.0 if fronds==1 else (i/float(fronds-1)-.5); off=frac*spread; fh=fh0*(1-abs(frac)*.28)
            q.append(_quad(bx+off,gy-fh*.60,max(4.,6.-abs(frac)*2),fh,_rgba_float(.24,.66,.30) if i%2 else _rgba_float(.17,.50,.22)))
    elif sp == "map_cactus":
        th={1:20.,2:32.,3:46.}[level]; tw={1:9.,2:11.,3:13.}[level]
        body=_rgba_float(.24,.56,.30); light=_rgba_float(.31,.66,.36)
        q += [_quad(bx,gy-th*.5,tw,th,body), _quad(bx-2,gy-th*.5,1.4,th-2,light), _quad(bx+2,gy-th*.5,1.4,th-2,light)]
        if level>=2: q += [_quad(bx-8,gy-th*.58,7,10,body), _quad(bx-10,gy-th*.58-5,4,10,body)]
        if level>=3: q += [_quad(bx+8,gy-th*.48,7,12,body), _quad(bx+10,gy-th*.48-6,4,12,body)]
        for off in (-3.5,0,3.5): q.append(_quad(bx+off,gy-th*.84,1,2,_rgba_float(.93,.93,.82)))
    elif sp in ("map_tree","map_jungle_tree"):
        th={1:28.,2:46.,3:68.}[level]; tw={1:6.,2:8.,3:10.}[level]; cw={1:22.,2:34.,3:48.}[level]; ch={1:15.,2:22.,3:30.}[level]
        canopy_y=gy-th+ch*.32
        q += [_quad(bx,gy-th*.5,tw,th,_rgba_float(.43,.28,.14)), _quad(bx,canopy_y,cw,ch,_rgba_float(.18,.55,.22))]
    elif sp == "map_pine":
        th={1:30.,2:48.,3:70.}[level]; tw={1:5.,2:7.,3:9.}[level]; cw={1:24.,2:38.,3:52.}[level]; ch={1:27.,2:42.,3:58.}[level]
        q.append(_quad(bx,gy-th*.5,tw,th,_rgba_float(.34,.24,.14)))
        tiers=((.86,.34),(.66,.48),(.46,.64),(.27,.80))
        for idx,(hf,wf) in enumerate(tiers):
            q.append(_quad(bx,gy-th*hf,cw*wf,max(5.,ch*(.13+.015*idx)),_rgba_float(.16,.46,.27) if idx%2 else _rgba_float(.12,.37,.22)))
    else:
        # unknown plant stays transparent rather than inventing art.
        pass
    return q



def player_quads(name):
    name=str(name)
    if name == "sword":
        return [
            _quad(0.0,-14.0,5.0,27.0,"#D9E0E7"),
            _quad(0.0,1.0,9.0,6.0,"#76502B"),
        ]
    # Current built-in player body in Metal is a feet-anchored 28×54 yellow quad.
    return [_quad(0.0,-27.0,28.0,54.0,"#F1CD57")]

def capture_builtin(asset_id, size=16):
    """Return the current built-in art quantized at the global FIX20 density."""
    aid=str(asset_id)
    if aid.startswith("tile."):
        return _rasterize_fixed(tile_quads(aid.split(".",1)[1]), size, "tile")
    if aid.startswith("creature."):
        # FIX21: built-in creatures are true editable pixel art, not geometric
        # placeholder rectangles. The returned grid already obeys fixed density.
        return creature_default_grid(aid.split(".",1)[1], int(size), CREATURE_ARCHETYPES)
    if aid.startswith("plant."):
        return _rasterize_fixed(plant_quads(aid.split(".",1)[1],3), size, "plant")
    if aid.startswith("player."):
        return _rasterize_fixed(player_quads(aid.split(".",1)[1]), size, "player")
    return [[TRANSPARENT for _ in range(int(size))] for _ in range(int(size))]



def _bounds_size(quads, fallback=(40.0, 40.0)):
    """Return the normal-state world-space silhouette size represented by quads."""
    rows=[q for q in (quads or []) if len(q)>=4 and float(q[2])>0 and float(q[3])>0]
    if not rows:
        return (float(fallback[0]), float(fallback[1]))
    left=min(float(q[0])-float(q[2])*0.5 for q in rows)
    right=max(float(q[0])+float(q[2])*0.5 for q in rows)
    top=min(float(q[1])-float(q[3])*0.5 for q in rows)
    bottom=max(float(q[1])+float(q[3])*0.5 for q in rows)
    return (max(1.0,right-left), max(1.0,bottom-top))


def builtin_world_size(asset_id):
    """Approximate in-game draw size in world pixels (1 tile = 40 px).

    This mirrors the same normal-state geometric definitions used by
    ``capture_builtin`` and is intentionally Metal/UIKit-free so the editor can
    show a true-scale thumbnail without booting the game renderer.
    """
    aid=str(asset_id)
    if aid.startswith('tile.'):
        return (40.0,40.0)
    if aid.startswith('creature.'):
        return _bounds_size(creature_quads(aid.split('.',1)[1]), (32.0,24.0))
    if aid.startswith('plant.'):
        return _bounds_size(plant_quads(aid.split('.',1)[1],3), (24.0,40.0))
    if aid.startswith('player.'):
        return _bounds_size(player_quads(aid.split('.',1)[1]), (28.0,54.0))
    return (40.0,40.0)
