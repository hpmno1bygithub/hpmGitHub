# -*- coding: utf-8 -*-
"""Versioned, idempotent FIX81 main-world -> FIX82 sky-padding translation.

Only the exact original main-world signature is eligible. Child maps never
move. Tile, chunk and pixel coordinate namespaces are deliberately separate.
The caller keeps the original file as a backup before using the converted data.
"""
import copy
import json
import os
from config import TILE_SIZE, CHUNK_SIZE

PAD = 96
OLD = {'kind':'custom_map','width':1024,'height':96,
       'generator':'v0.7.7.5-fix74-ecosystem-world','seed':20260901,
       'biome_version':8,'map_id':'main_world'}
NEW = dict(OLD, height=192)
TILE_FIELDS = frozenset('water lava honey fire soil soil_layers plants steam poison_gas poison_gas_ttl acid poison_liquid electric_shock reaction_wind magnetic_sources electric_charge temperature water_temperature ice_mass cold_surface_water snow_cover ice_melt_progress water_freeze_progress ash ash_wet_time swamp_wet_time swamp_dry_time'.split())
CHUNK_FIELDS = frozenset('wind updraft fireball_lift climate vapor groundwater smoke organic'.split())


def eligible(signature):
    return isinstance(signature,dict) and all(signature.get(k)==v for k,v in OLD.items())


def shift_rows(rows, offset):
    return [[r[0],r[1]+offset]+list(r[2:]) if isinstance(r,(list,tuple)) and len(r)>=2 else r for r in (rows or ())]


def environment(data):
    if not isinstance(data,dict):return data
    for k in TILE_FIELDS:
        if k in data:data[k]=shift_rows(data[k],PAD)
    for k in CHUNK_FIELDS:
        if k in data:data[k]=shift_rows(data[k],PAD//CHUNK_SIZE)
    # cloud_water/cloud_desert_fresh have pseudo (cx,0) reservoir keys, not Y.
    return data


def pixel_rows(rows):
    for r in rows or ():
        if isinstance(r,dict) and 'y' in r:r['y']+=PAD*TILE_SIZE


def session(state):
    if not isinstance(state,dict) or not eligible(state.get('world_base')):return False
    state['world_base']=dict(NEW)
    state['world_changes']=shift_rows(state.get('world_changes',()),PAD)
    state['environment']=environment(state.get('environment'))
    state['topsoil_wet_clock']=shift_rows(state.get('topsoil_wet_clock',()),PAD)
    pixel_rows(state.get('world_items'));pixel_rows(state.get('creatures'))
    return True


def migrate(data, project_root):
    """Return (private converted copy, changed); never mutate the caller."""
    try:
        with open(os.path.join(project_root,'maps','editor_map.json'),encoding='utf-8') as f:m=json.load(f)
        meta=m.get('metadata',{})
        if m.get('size') != [1024,192] or meta.get('sky_extension',{}).get('top_padding_tiles')!=PAD:
            return data,False
        if any(meta.get(k)!=OLD[k] for k in ('generator','seed','biome_version','map_id')):return data,False
    except (OSError,ValueError,TypeError):
        return data,False
    current=eligible(data.get('world_base'))
    sessions=(data.get('map_sessions') or {}).get('sessions',())
    has_main_session=any(isinstance(r,dict) and eligible((r.get('state') or {}).get('world_base')) for r in sessions)
    if not current and not has_main_session:return data,False
    out=copy.deepcopy(data)
    if current:
        out['world_base']=dict(NEW)
        pixel_rows([out.get('player',{})])
        world=out.setdefault('world',{})
        world['changes']=shift_rows(world.get('changes',()),PAD)
        world['environment']=environment(world.get('environment'))
        ecology=out.setdefault('ecology',{})
        ecology['topsoil_wet_clock']=shift_rows(ecology.get('topsoil_wet_clock',()),PAD)
        entities=out.setdefault('entities',{})
        pixel_rows([entities.get('npc_test',{})])
        for key in ('world_items','creatures','physics_objects'):pixel_rows(entities.get(key))
    for row in out.get('map_sessions',{}).get('sessions',()):
        if isinstance(row,dict):session(row.get('state'))
    # Chests are global collections shared by main and child-map saves.
    # Recovery IDs are opaque and kept stable; only their actual position moves.
    for row in out.get('recovery_chests',{}).get('records',()):
        if isinstance(row,dict) and row.get('map_key')=='editor_map':row['floor']+=PAD
    opened=[]
    for ident in out.get('opened_chests',()):
        parts=str(ident).split(':')
        if len(parts)==5 and parts[:2]==['chest','editor_map']:
            try:parts[-1]=str(int(parts[-1])+PAD);ident=':'.join(parts)
            except ValueError:pass
        opened.append(ident)
    out['opened_chests']=opened
    out['sky_padding_migration']={'version':82,'tiles':PAD,'source_height':96}
    return out,True
