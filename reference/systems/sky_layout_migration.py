# -*- coding: utf-8 -*-
"""Relocate only FIX82 cloud-island save coordinates; never move the ground."""
import copy,json,os
from config import TILE_SIZE
from systems.sky_save_migration import NEW,TILE_FIELDS

def migrate(data,project_root):
    if not isinstance(data,dict) or int(data.get('sky_layout_version',0) or 0)>=83:return data,False
    try:
        with open(os.path.join(project_root,'maps/editor_map.json'),encoding='utf-8') as f:meta=json.load(f)['metadata']
        if int(meta.get('sky_layout_version',0))<83:return data,False
        zones=meta['sky_island_relocations'];routes=meta['sky_access_routes']
    except (OSError,KeyError,TypeError,ValueError):return data,False
    def main(sig):return isinstance(sig,dict) and all(sig.get(k)==v for k,v in NEW.items())
    has=main(data.get('world_base')) or any(main((r.get('state') or {}).get('world_base')) for r in (data.get('map_sessions') or {}).get('sessions',()) if isinstance(r,dict))
    if not has:return data,False
    out=copy.deepcopy(data)
    def moved_y(x,y,pixels=False):
        scale=TILE_SIZE if pixels else 1.0;tx=x/scale;ty=y/scale
        if ty>=96:return y
        for z in zones:
            a,b,c,d=z['bounds']
            if a<=tx<=c and b<=ty<=d:return y+z['dy']*scale
        # Preserve a player's place along a ladder between new tiers.
        if pixels and any(abs(tx-(r['x']+.5))<1.2 for r in routes):
            knots=((0,0),(19,47),(34,62),(49,67),(63,81),(79,87),(93,101),(104,104))
            for (a,b),(c,d) in zip(knots,knots[1:]):
                if a<=ty<=c:return (b+(ty-a)*(d-b)/(c-a))*scale
        return y
    def rows(seq):
        for r in seq or ():
            if isinstance(r,list) and len(r)>=2:r[1]=int(round(moved_y(r[0],r[1])))
    def actors(seq):
        for r in seq or ():
            if not isinstance(r,dict) or 'y' not in r:continue
            r['y']=moved_y(r.get('x',0),r['y'],True)
            if 'home_y' in r:r['home_y']=moved_y(r.get('home_x',r.get('x',0)),r['home_y'],True)
    def env(e):
        if isinstance(e,dict):
            for k in TILE_FIELDS:rows(e.get(k))
    if main(out.get('world_base')):
        actors([out.get('player',{})]);w=out.get('world',{});rows(w.get('changes'));env(w.get('environment'))
        rows(out.get('ecology',{}).get('topsoil_wet_clock'))
        es=out.get('entities',{})
        for k in ('world_items','creatures','physics_objects'):actors(es.get(k))
    for row in out.get('map_sessions',{}).get('sessions',()):
        st=row.get('state',{})
        if not main(st.get('world_base')):continue
        rows(st.get('world_changes'));env(st.get('environment'));rows(st.get('topsoil_wet_clock'))
        for k in ('world_items','creatures'):actors(st.get(k))
    for r in out.get('recovery_chests',{}).get('records',()):
        if r.get('map_key')=='editor_map' and 'floor' in r:r['floor']=int(round(moved_y(r.get('x',0),r['floor'])))
    out['sky_layout_version']=83
    return out,True
