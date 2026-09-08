# -*- coding: utf-8 -*-
"""FIX90 authored map objects. Pure data and bounded gameplay integration."""
import copy
import math
import os
from dataclasses import dataclass
from config import TILE_SIZE
from entities.creature import Creature
from entities.chest import Chest
from world.tile_registry import tile_def, IMPERMEABLE_ROCK_TILES, DIRT, GRASS_DIRT, ASH, MUD, SWAMP_SOIL, SAND, JUNGLE_SOIL, SNOW_DIRT, SEA_SAND
from systems.creature_attributes import normalize_habitat_mode, effective_locomotion

MAX_POINTS=512
MAX_ACTORS=256
MAX_PER_POINT=16
MAX_LOOT_ROWS=24
EDITOR_KEY='editor_objects'
FLYING=frozenset(('bird','insect_low','fly_low','cave_fly','ghost_fly','sky_fly','background_fly'))
AQUATIC=frozenset(('fish','swim','waterbird'))
AI_MOVE_MODES=frozenset(('default','hold','patrol','chase','patrol_chase'))
AI_BEHAVIOR_MODES=frozenset(('default','passive','retaliate','hostile'))
AI_ATTACK_MODES=frozenset(('default','none','contact','normal','special','mixed'))
HIDING_TILES=frozenset(tuple(IMPERMEABLE_ROCK_TILES)+(DIRT,GRASS_DIRT,ASH,MUD,SWAMP_SOIL,SAND,JUNGLE_SOIL,SNOW_DIRT,SEA_SAND))

def normalize_editor_ai(row):
    row=row if isinstance(row,dict) else {}
    move=str(row.get('move_mode','default') or 'default')
    behavior=str(row.get('behavior_mode','default') or 'default')
    attack=str(row.get('attack_mode','default') or 'default')
    if move not in AI_MOVE_MODES:move='default'
    if behavior not in AI_BEHAVIOR_MODES:behavior='default'
    if attack not in AI_ATTACK_MODES:attack='default'
    return {
        'move_mode':move,
        'behavior_mode':behavior,
        'attack_mode':attack,
        'habitat_mode':normalize_habitat_mode(row.get('habitat_mode','default')),
        'detect_tiles':finite(row.get('detect_tiles',0.0),0.0,0.0,40.0),
        'detect_vertical_tiles':finite(row.get('detect_vertical_tiles',0.0),0.0,0.0,24.0),
        'speed_scale':finite(row.get('speed_scale',1.0),1.0,.0,3.0),
        'attack_cooldown_scale':finite(row.get('attack_cooldown_scale',1.0),1.0,.25,4.0),
    }

def apply_editor_ai_overrides(creature,row):
    """Attach one MapEditor placement's AI policy to a Creature instance.

    Locomotion remains species-authored (a fish stays aquatic, a flyer stays a
    flyer). These knobs control voluntary movement, hostility and action choice.
    """
    if creature is None:return creature
    ai=normalize_editor_ai(row)
    creature.editor_move_mode=ai['move_mode']
    creature.editor_behavior_mode=ai['behavior_mode']
    creature.editor_attack_mode=ai['attack_mode']
    creature.editor_habitat_mode=ai['habitat_mode']
    creature.editor_step_height_tiles=2.0 if ai['habitat_mode']=='land' else 0.0
    # The ecology override changes only this placed instance.  The original
    # species definition remains untouched for every other spawn.
    if ai['habitat_mode']!='default':
        creature.locomotion=effective_locomotion({'locomotion':getattr(creature,'locomotion','ground')},{'habitat_mode':ai['habitat_mode']})
    creature.editor_detection_radius_px=float(ai['detect_tiles'])*TILE_SIZE
    creature.editor_detection_vertical_px=float(ai['detect_vertical_tiles'])*TILE_SIZE
    creature.editor_attack_cooldown_scale=float(ai['attack_cooldown_scale'])
    creature.editor_speed_scale=float(ai['speed_scale'])
    creature.speed=float(creature.speed)*float(ai['speed_scale'])
    return creature

def finite(value,default=0.0,lo=-1e7,hi=1e7):
    try: value=float(value)
    except (ValueError,TypeError):value=float(default)
    if not math.isfinite(value):value=float(default)
    return max(lo,min(hi,value))

def objects_from_metadata(meta):
    raw=meta.get(EDITOR_KEY,[]) if isinstance(meta,dict) else []
    if not isinstance(raw,list):return []
    return [r for r in raw[:MAX_POINTS] if isinstance(r,dict)]

def occupied(world,x,y,w,h):
    a=(x-w/2+.2,y-h+.2,x+w/2-.2,y-.2)
    for tx in range(int(math.floor(a[0]/TILE_SIZE)),int(math.floor(a[2]/TILE_SIZE))+1):
        for ty in range(int(math.floor(a[1]/TILE_SIZE)),int(math.floor(a[3]/TILE_SIZE))+1):
            if not(0<=tx<world.width_tiles and 0<=ty<world.height_tiles):return True
            top=world.solid_top_y(tx,ty)
            if top is not None and a[0]<(tx+1)*TILE_SIZE and a[2]>tx*TILE_SIZE and a[1]<(ty+1)*TILE_SIZE and a[3]>top:return True
    return False

def locate(world,water,cfg,tx,ty,anchor='auto',habitat_mode='default'):
    """Return exact feet coordinates using the selected placement ecology."""
    tx=int(tx);ty=int(ty)
    if not(0<=tx<world.width_tiles and 0<=ty<world.height_tiles):raise ValueError('放置位置超出地圖')
    w=finite(cfg.get('w',30),30,4,400);h=finite(cfg.get('h',24),24,4,400)
    mode=normalize_habitat_mode(habitat_mode)
    loc=effective_locomotion(cfg,{'habitat_mode':mode});x=(tx+.5)*TILE_SIZE
    if mode=='burrow':
        tile_id=int(world.get_tile(tx,ty))
        if tile_id not in HIDING_TILES:
            raise ValueError('岩土躲藏生物必須放在土壤、沙土或岩石／礦石圖塊內')
        # The actor stays inactive while the tile exists.  Once that exact cell
        # is completely excavated it wakes at the newly opened cell's bottom.
        return x,(ty+1)*TILE_SIZE
    if loc in ('ceiling','ceiling_drop'):
        for cy in range(max(0,ty-4),min(world.height_tiles,ty+1)):
            if world.solid_top_y(tx,cy) is not None:
                y=(cy+1)*TILE_SIZE+h+.3
                if not occupied(world,x,y,w,h):return x,y
        raise ValueError('倒吊生物需要上方 4 格內有實體天花板，而且嘴部空間不能被堵住')
    if mode=='aquatic' or loc in AQUATIC:
        y=(ty+.85)*TILE_SIZE
        if float(water.get((tx,ty),0))<.40:raise ValueError('水中生物必須放在有足夠水量的水體內')
        if loc=='waterbird' and mode=='default':y=ty*TILE_SIZE+4
        if not occupied(world,x,y,w,h):return x,y
        raise ValueError('水中生物的身體會與牆面重疊，請換一格')
    if mode=='fly_rest' or loc in FLYING or anchor=='air':
        y=(ty+.5)*TILE_SIZE+h*.5
        if not occupied(world,x,y,w,h):return x,y
        raise ValueError('飛行生物需要足夠的無碰撞空間')
    for fy in range(max(0,ty-1),min(world.height_tiles,ty+13)):
        y=world.solid_top_y(tx,fy)
        if y is not None and not occupied(world,x,y,w,h):return x,float(y)
    raise ValueError('此處下方 12 格內沒有可站立的地面，或生物身體空間不足')

def spawn_editor_creatures(game,rows):
    from systems.biome_system import CREATURE_ARCHETYPES
    out=[];warnings=[];map_key=game._map_key()
    for row in rows:
        if row.get('kind')!='creature' or row.get('enabled',True) is False:continue
        species=str(row.get('species',''));cfg=CREATURE_ARCHETYPES.get(species)
        if not cfg:warnings.append('未知生物：'+species);continue
        for i in range(int(finite(row.get('count',1),1,1,MAX_PER_POINT))):
            if len(out)>=MAX_ACTORS:break
            tx=int(row.get('x',0))+i*int(finite(row.get('spacing',2),2,1,16));ty=int(row.get('y',0))
            habitat_mode=normalize_habitat_mode(row.get('habitat_mode','default'))
            try:x,y=locate(game.world,game.environment.state.water,cfg,tx,ty,row.get('anchor','auto'),habitat_mode)
            except (ValueError,TypeError) as exc:warnings.append(str(exc));continue
            loc=effective_locomotion(cfg,row)
            c=Creature(entity_id='editor_spawn:%s:%s:%d'%(map_key,row['id'],i),x=x,y=y,
                species=species,name=str(cfg['name']),hp=float(cfg['hp']),max_hp=float(cfg['hp']),
                attack_damage=float(cfg.get('attack',8)),speed=float(cfg.get('speed',40)),
                width_px=float(cfg['w']),height_px=float(cfg['h']),hostile=bool(cfg.get('hostile',False)),
                home_x=x,home_y=y,patrol_radius=TILE_SIZE*finite(row.get('patrol',3),3,0,32),
                biome=game.biomes.biome_at_world_x(x),loot_table=tuple(cfg.get('loot',())),
                asset_id='creature.'+species,locomotion=loc,element_type=str(cfg.get('element','neutral')),
                boss=bool(cfg.get('boss',False)),boss_title=str(cfg.get('boss_title',cfg['name'])))
            apply_editor_ai_overrides(c,row)
            if habitat_mode=='burrow':
                c.editor_hidden_until_mined=True
                c.editor_hidden_tile=(int(tx),int(ty))
                c.editor_revealed=(int(game.world.get_tile(int(tx),int(ty))) not in HIDING_TILES)
                c.active=bool(c.editor_revealed)
            c.poison_immune=bool(cfg.get('poison_immune',False))
            c.respawn_enabled=(False if c.background_only else bool(row.get('respawn',False if c.boss else True)))
            c.respawn_delay_seconds=finite(row.get('respawn_seconds',120),120,1,86400)
            c.underground=bool(row.get('underground',y/TILE_SIZE>float(game.biomes.metadata.get('underground_start_row',999999))))
            c.respawn_region='editor';c.grounded=loc not in FLYING|AQUATIC|{'ceiling','ceiling_drop'}
            game.scene.add_entity(c);out.append(c)
    return out,warnings

def spawn_editor_chests(game,rows):
    out=[];warnings=[]
    for row in rows:
        if row.get('kind')!='chest' or row.get('enabled',True) is False:continue
        try:x,y=locate(game.world,{},dict(w=30,h=24),row.get('x',0),row.get('y',0))
        except (ValueError,TypeError) as exc:warnings.append(str(exc));continue
        loot=copy.deepcopy(row.get('loot',[])[:MAX_LOOT_ROWS])
        if not loot:continue
        eid='editor_chest:%s:%s'%(game._map_key(),row['id'])
        chest=Chest(entity_id=eid,x=x,y=y,loot_item_id=str(loot[0]['id']),
            loot_name=str(loot[0]['name']),count=int(loot[0]['count']),opened=eid in game.opened_chest_ids)
        chest.editor_loot=loot
        game.scene.add_entity(chest);out.append(chest)
    return out,warnings

def add_loot_atomically(inventory,entries):
    """All-or-nothing, preserving heavy-use arrays and full-bag chest state."""
    from systems.inventory_system import InventorySystem
    with inventory._lock:
        trial=InventorySystem(None)
        trial.import_state(inventory.export_state())
        for row in entries:
            item_id=str(row['id']);count=int(row['count'])
            limit=inventory.WEAPON_MAX_STACK if item_id.startswith('weapon_') else inventory.DEFAULT_MAX_STACK
            accepted=trial.add(item_id,str(row['name']),count,max_stack=limit)
            if accepted!=count:return False
        inventory.slots=trial.slots
        return True


# FIX93 runtime/editor population bridge.  The game writes a tiny snapshot once
# per map load so the editor can show the same automatic population without
# instantiating the whole simulation or thousands of UIKit objects.
def runtime_population_snapshot_path(project_root, map_key):
    safe=''.join(ch if ch.isalnum() or ch in ('_','-') else '_' for ch in str(map_key or 'map'))[:96]
    return os.path.join(str(project_root),'maps','.runtime_population_'+safe+'.pop')

def write_runtime_population_snapshot(project_root,map_key,creatures,editor_revision=''):
    import json, os, tempfile
    rows=[]
    for c in creatures or ():
        eid=str(getattr(c,'entity_id','') or '')
        # creature_spawns already have editable legacy rows in the map editor.
        origin='authored' if eid.startswith(('authored_','secondary_','fix82_','fix83_')) else 'auto'
        rows.append({
            'entity_id':eid,'origin':origin,'species':str(getattr(c,'species','') or ''),
            'name':str(getattr(c,'name','') or getattr(c,'species','') or '生物'),
            'x':float(getattr(c,'home_x',getattr(c,'x',0.0)))/TILE_SIZE,
            'y':float(getattr(c,'home_y',getattr(c,'y',0.0)))/TILE_SIZE,
            'patrol':float(getattr(c,'patrol_radius',0.0))/TILE_SIZE,
            'respawn':bool(getattr(c,'respawn_enabled',not bool(getattr(c,'boss',False)))),
            'respawn_seconds':float(getattr(c,'respawn_delay_seconds',120.0)),
            'boss':bool(getattr(c,'boss',False)),
            'locomotion':str(getattr(c,'locomotion','ground') or 'ground'),
            'habitat_mode':str(getattr(c,'editor_habitat_mode','default') or 'default'),
            'element':str(getattr(c,'element_type','earth') or 'earth'),
            'move_mode':str(getattr(c,'editor_move_mode','default') or 'default'),
            'behavior_mode':str(getattr(c,'editor_behavior_mode','default') or 'default'),
            'attack_mode':str(getattr(c,'editor_attack_mode','default') or 'default'),
            'detect_tiles':float(getattr(c,'editor_detection_radius_px',0.0) or 0.0)/TILE_SIZE if float(getattr(c,'editor_detection_radius_px',0.0) or 0.0)>0 else 0.0,
            'detect_vertical_tiles':float(getattr(c,'editor_detection_vertical_px',0.0) or 0.0)/TILE_SIZE if float(getattr(c,'editor_detection_vertical_px',0.0) or 0.0)>0 else 0.0,
            'speed_scale':float(getattr(c,'editor_speed_scale',1.0) or 1.0),
            'attack_cooldown_scale':float(getattr(c,'editor_attack_cooldown_scale',1.0) or 1.0),
        })
    payload={'format':1,'map_key':str(map_key),'editor_revision':str(editor_revision or ''),'count':len(rows),'creatures':rows}
    path=runtime_population_snapshot_path(project_root,map_key);os.makedirs(os.path.dirname(path),exist_ok=True)
    temp=path+'.tmp'
    try:
        with open(temp,'w',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False,separators=(',',':'))
        os.replace(temp,path)
    except Exception:
        try:os.remove(temp)
        except OSError:pass
    return path
