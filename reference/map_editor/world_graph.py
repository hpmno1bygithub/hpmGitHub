# -*- coding: utf-8 -*-
"""FIX32 interconnected world-graph / box-garden map authoring.

This module uses only high-level interconnected level-design principles:
loops, shortcuts, vertical routes, memorable ecological districts and gated
boss rooms. It does not reproduce any specific commercial game's map.
"""
import os
import json
import math

from config import WORLD_GEN_DEFAULT_WIDTH, WORLD_GEN_DEFAULT_HEIGHT, WORLD_GEN_SURFACE_ROW
from map_editor.model import MapDocument
from map_editor.world_generator import generate_world, _hash01
from systems.underground_background import background_metadata, dynamic_background_metadata
from systems.world_topology import WorldTopology


def ordered_map_files_from_graph(graph, discovered):
    """Return graph-declared JSON maps, compatible with v1 and v2 schemas."""
    available={str(name) for name in tuple(discovered or ()) if str(name).lower().endswith(".json")}
    ordered=[]
    if not isinstance(graph,dict):
        return ordered
    main_name=str(graph.get("main","editor_map.json") or "editor_map.json")
    if main_name in available:
        ordered.append(main_name)
    maps=graph.get("maps",{}) or {}
    if not isinstance(maps,dict):
        return ordered
    for map_key,info in maps.items():
        key_name=str(map_key or "")
        legacy_name=str(info.get("file","") if isinstance(info,dict) else info)
        name=key_name if key_name.lower().endswith(".json") else legacy_name
        if name in available and name not in ordered:
            ordered.append(name)
    return ordered
from world.biomes import (
    BIOME_OCEAN, BIOME_PLAINS, BIOME_RAINFOREST, BIOME_SNOW_MOUNTAIN, BIOME_DESERT,
)
from world.soil_layers import SOIL_LAYER_FULL


def _solid(doc, x, y, material="stone"):
    if doc.in_bounds(x, y):
        doc.set("terrain", x, y, material)
        doc.erase_layer("ground_layer", x, y)
        doc.erase_layer("water", x, y)
        doc.erase_layer("lava", x, y)
        doc.erase_layer("honey", x, y)


def _air(doc, x, y):
    if doc.in_bounds(x, y):
        doc.erase_layer("terrain", x, y)
        doc.erase_layer("ground_layer", x, y)
        doc.erase_layer("water", x, y)
        doc.erase_layer("lava", x, y)
        doc.erase_layer("honey", x, y)


def _carve_rect(doc, x0, y0, x1, y1):
    for x in range(max(1, int(x0)), min(doc.width-1, int(x1)+1)):
        for y in range(max(1, int(y0)), min(doc.height-2, int(y1)+1)):
            _air(doc, x, y)


def _carve_corridor(doc, x0, y, x1, height=4):
    a,b=sorted((int(x0),int(x1)))
    _carve_rect(doc,a,int(y)-height+1,b,int(y))


def _ladder(doc, x, top, bottom):
    for y in range(max(1,int(top)),min(doc.height-1,int(bottom))+1):
        _air(doc,x,y)
        doc.set("terrain",x,y,"ladder")


def _material(seed, x, y, theme):
    n=_hash01(seed,x,y,6101)
    if theme=="crystal_mine":
        if n>0.92:return "gold_ore"
        if n>0.78:return "iron_ore"
        if n>0.64:return "copper_ore"
        return "stone"
    if theme=="boss_dungeon":
        return "marble" if n>0.70 else "stone"
    if theme=="underground_castle":
        return "limestone" if n>0.45 else "stone"
    if theme=="magma_hell":
        return "igneous_rock" if n>0.12 else "iron_ore"
    return "limestone" if n>0.74 else "stone"


def _terrace_floors(doc, seed):
    count=0
    geo={"stone","copper_ore","iron_ore","gold_ore","marble","limestone"}
    seq=(7,7,3,3,1,1,3,3,7,7,3,3)
    for x in range(2,doc.width-2):
        for y in range(2,doc.height-2):
            if str(doc.get("terrain",x,y,None) or "") not in geo:
                continue
            if doc.get("terrain",x,y-1,None) is not None:
                continue
            mask=seq[((x//3)+y)%len(seq)]
            if _hash01(seed,x,y,6120)>0.965:
                mask=1 if _hash01(seed,x,y,6121)<0.5 else 7
            doc.set("ground_layer",x,y,int(mask));count+=1
    return count


def _floor_near(doc, tx, preferred):
    tx=max(2,min(doc.width-3,int(tx)))
    candidates=[]
    for y in range(4,doc.height-2):
        if doc.get("terrain",tx,y,None) is None:
            continue
        if doc.get("terrain",tx,y-1,None) is not None:
            continue
        if doc.get("terrain",tx,y-2,None) is not None:
            continue
        candidates.append(y)
    if not candidates:return max(5,min(doc.height-3,int(preferred)))
    return min(candidates,key=lambda y:abs(y-int(preferred)))


def _add_portal(doc, portal_id, target_map, target_portal, tx, preferred_floor, boss=False):
    floor=_floor_near(doc,tx,preferred_floor)
    tx=max(2,min(doc.width-3,int(tx)))
    # Guarantee a 3-cell-high doorway and a solid landing.
    for y in range(max(1,floor-3),floor):_air(doc,tx,y)
    if doc.get("terrain",tx,floor,None) is None:_solid(doc,tx,floor,"stone")
    doc.set("decoration",tx,floor,("boss_gate" if boss else "portal",3 if boss else 2))
    # FIX34: author the destination spawn OUTSIDE the 3x3 doorway trigger.
    side=1 if tx < doc.width*0.5 else -1
    arrival_tx=max(2,min(doc.width-3,tx+side*2))
    for y in range(max(1,floor-3),floor):
        _air(doc,arrival_tx,y)
    if doc.get("terrain",arrival_tx,floor,None) is None:
        _solid(doc,arrival_tx,floor,"stone")
    # A portal is also a respawn/load boundary.  Liquids placed earlier in the
    # generator must not flow onto its doorway or arrival apron while the player
    # is still under startup/transition lock.  Clear a generous local pocket;
    # if a liquid field touched it, add one jumpable one-cell sill on the map-
    # interior side to stop immediate refill without sealing the route.
    safe_lo=max(1,min(tx-1,arrival_tx)-3)
    safe_hi=min(doc.width-2,max(tx+1,arrival_tx)+3)
    safe_top=max(1,floor-4)
    had_liquid=False
    for px in range(safe_lo,safe_hi+1):
        for py in range(safe_top,floor):
            if (doc.get("water",px,py,None) is not None
                    or doc.get("lava",px,py,None) is not None
                    or doc.get("honey",px,py,None) is not None):
                had_liquid=True
            doc.erase_layer("water",px,py)
            doc.erase_layer("lava",px,py)
            doc.erase_layer("honey",px,py)
    if had_liquid:
        sill_x=safe_hi if side>0 else safe_lo
        _solid(doc,sill_x,floor-1,"igneous_rock")
    row={
        "id":str(portal_id),
        "rect":[tx-1,max(1,floor-3),3,3],
        "arrival":[arrival_tx,floor],
        "target_map":str(target_map),
        "target_portal":str(target_portal),
    }
    doc.metadata.setdefault("portals",[]).append(row)
    return row


def _bind_existing_portal(doc, portal_id, target_map, target_portal, tx, floor, boss=False):
    """Bind an already-authored physical gate without replacing its artwork.

    The main world's deepest desert dungeon has carried an unused boss_gate
    since the terrain generator was introduced. FIX69 turns that exact gate
    into a real endpoint while preserving its foreground geometry.
    """
    tx=max(2,min(doc.width-3,int(tx)))
    floor=max(3,min(doc.height-1,int(floor)))
    side=1 if tx < doc.width*.5 else -1
    arrival_tx=max(2,min(doc.width-3,tx+side*2))
    for y in range(max(1,floor-2),floor):
        _air(doc,arrival_tx,y)
        doc.erase_layer("water",arrival_tx,y)
        doc.erase_layer("lava",arrival_tx,y)
        doc.erase_layer("honey",arrival_tx,y)
    if doc.get("terrain",arrival_tx,floor,None) is None:
        _solid(doc,arrival_tx,floor,"stone")
    row={
        "id":str(portal_id),
        "rect":[tx-1,max(1,floor-3),3,3],
        "arrival":[arrival_tx,floor],
        "target_map":str(target_map),
        "target_portal":str(target_portal),
        "visual":"boss_gate" if boss else "portal",
        "bound_existing_decoration":True,
    }
    doc.metadata.setdefault("portals",[]).append(row)
    return row


def build_submap(map_id, theme, seed, width=144, height=64):
    seed=int(seed); width=max(96,int(width)); height=max(56,int(height))
    doc=MapDocument(width,height)
    for layer in doc.layers.values():layer.clear()
    # Solid geological envelope. All navigable space is explicitly carved.
    for x in range(width):
        for y in range(height):
            _solid(doc,x,y,_material(seed,x,y,theme))

    levels=(18,32,46)
    enhanced=theme in ("glow_moss","crystal_mine","magma_hell")
    if enhanced:
        # FIX62 room graph: short galleries, loops, shafts and cross-links.
        # There is deliberately no full-width corridor, avoiding the former
        # three-layer "subway" silhouette.
        segmented={
            18:((4,31),(42,75),(90,width-6)),
            32:((4,25),(34,60),(69,106),(117,width-5)),
            46:((5,42),(52,87),(97,width-6)),
        }
        for row,runs in segmented.items():
            for x0,x1 in runs:_carve_corridor(doc,x0,row,x1,4)
        for x,top,bottom in ((28,16,34),(61,29,48),(94,16,34),(124,29,48)):
            _ladder(doc,x,top,bottom)
        _carve_rect(doc,74,16,76,32)
    else:
        for li,row in enumerate(levels):
            _carve_corridor(doc,4,row,width-5,4)
            for ri,cx in enumerate((22,52,84,116)):
                if cx>=width-8:continue
                rw=8+int(_hash01(seed,cx,ri,6200)*6)
                rh=3+int(_hash01(seed,cx,ri,6201)*3)
                _carve_rect(doc,cx-rw,row-rh-2,cx+rw,row+1)
        _ladder(doc,36,levels[0]-2,levels[2])
        _ladder(doc,width-38,levels[0]-2,levels[2])
        _carve_rect(doc,width//2-1,levels[0]-2,width//2+1,levels[2])
        _carve_corridor(doc,36,levels[1]-7,width//2,3)
        _carve_corridor(doc,width//2,levels[1]+7,width-38,3)

    # FIX62 gives every secondary map its own room rhythm.  The pyramid's
    # successful rounded chambers, sloped links, optional shortcuts and staged
    # encounters are reused as level-design principles, while the actual room
    # silhouettes and themes remain original to each map.
    room_profiles={
        "glow_moss":((14,29,10,6),(20,14,11,6),(43,27,13,7),(58,45,12,6),(73,15,11,6),(89,31,14,8),(105,45,13,6),(121,17,12,7),(130,35,9,6)),
        "crystal_mine":((14,29,10,6),(19,15,10,6),(42,19,13,6),(57,44,13,6),(73,30,15,8),(89,14,11,6),(104,45,15,7),(121,23,13,7),(132,42,8,6)),
        "underground_castle":((18,16,11,5),(48,17,13,6),(75,29,18,8),(111,17,12,6),(29,45,15,6),(79,45,17,6),(120,44,12,7)),
        "boss_dungeon":((18,17,11,5),(48,28,16,7),(76,16,11,6),(105,28,22,9),(42,46,17,6),(91,46,16,6),(126,45,11,7)),
        "magma_hell":((13,29,9,6),(18,15,10,6),(37,27,12,7),(53,44,13,6),(68,16,11,6),(84,31,14,8),(101,45,13,6),(116,19,12,7),(128,34,12,8),(134,47,7,5)),
    }
    theme_rooms=room_profiles.get(theme,())
    for cx,cy,rx,ry in theme_rooms:
        _carve_chamber(doc,cx,cy,rx,ry)
    route_profiles={
        "glow_moss":(((20,18),(28,25),(36,32)),((25,32),(34,39),(45,46)),((42,32),(55,25),(68,18)),((73,18),(82,25),(89,32)),((90,32),(98,39),(106,46)),((104,46),(116,39),(126,32)),((112,18),(121,25),(130,32)),((48,46),(65,39),(82,32))),
        "crystal_mine":(((20,18),(28,25),(36,32)),((24,32),(39,39),(54,46)),((44,18),(57,24),(70,32)),((72,32),(82,23),(91,18)),((91,18),(104,25),(116,32)),((98,46),(108,39),(119,32)),((58,46),(72,40),(86,46)),((117,18),(126,28),(134,43))),
        "underground_castle":(((25,19),(36,25),(50,32)),((63,32),(74,24),(88,19)),((92,24),(105,30),(117,34)),((43,45),(59,39),(75,47))),
        "boss_dungeon":(((24,20),(38,25),(51,32)),((61,31),(73,24),(82,20)),((86,21),(96,27),(108,34)),((49,45),(66,40),(83,47))),
        "magma_hell":(((19,18),(28,25),(37,32)),((24,32),(38,39),(52,46)),((39,32),(53,24),(68,18)),((68,18),(77,25),(84,32)),((85,32),(93,39),(102,46)),((101,46),(113,39),(121,32)),((103,18),(114,24),(122,32)),((54,46),(69,40),(84,32))),
    }
    for points in route_profiles.get(theme,()):
        _carve_sloped_route(doc,points,height=5,width=1)

    # Physical landmark ribs break long sight-lines that can emerge where a
    # large chamber overlaps a gallery.  Each rib blocks only one route; the
    # authored upper/lower loops remain the intended bypass.
    rib_profiles={
        "glow_moss":((39,32),(109,32)),
        "crystal_mine":((32,32),(109,32)),
        "magma_hell":((45,46),(92,46)),
    }
    for rib_x,rib_floor in rib_profiles.get(theme,()):
        for dx in (0,1):
            for yy in range(int(rib_floor)-3,int(rib_floor)+1):
                _solid(doc,int(rib_x)+dx,yy,_material(seed,int(rib_x)+dx,yy,theme))

    caverns=[[int(cx),int(cy),int(rx),int(ry)] for cx,cy,rx,ry in theme_rooms]
    dungeons=[]
    if theme=="boss_dungeon":
        # Dedicated arena sits away from the center traversal shaft so the
        # giant boss has a real floor and 4+ air cells of head room.
        ax0=width//2+8; ax1=width-8; ay0=levels[1]-9; ay1=levels[1]+2
        _carve_rect(doc,ax0,ay0,ax1,ay1-1)
        dungeons.append({"bounds":[ax0,ay0,ax1,ay1],"style":"abyss_ritual_arena"})
    elif theme in ("underground_castle","magma_hell"):
        dungeons.append({"bounds":[width//2-18,levels[1]-7,width//2+18,levels[1]+2],"style":"castle_keep" if theme=="underground_castle" else "basalt_forge"})
    else:
        dungeons.append({"bounds":[width//2-14,levels[1]-6,width//2+14,levels[1]+2],"style":"living_grotto" if theme=="glow_moss" else "crystal_excavation"})

    # Theme ecology/art.
    if theme=="glow_moss":
        # Living caverns: moss carpets provide continuity while large fungi,
        # hanging vines and spore landmarks give each chamber a silhouette.
        for x in range(8,width-8,6):
            floor=_floor_near(doc,x,levels[(x//17)%3]+1)
            doc.set("decoration",x,floor,("glow_moss",1+(x//4)%3))
        for x in range(14,width-12,18):
            floor=_floor_near(doc,x,levels[(x//31)%3]+1)
            doc.set("decoration",x,floor,("root_arch",2 if x%36 else 3))
        for x,kind,size in (
            (18,"giant_mushroom",3),(37,"spore_pod",2),(52,"hanging_glow_vine",3),
            (70,"glow_pool",3),(86,"moss_stalactite",3),(103,"giant_mushroom",2),
            (119,"root_cluster",3),(132,"spore_pod",3),
        ):
            floor=_floor_near(doc,x,levels[(x//26)%3]+1)
            doc.set("decoration",x,floor,(kind,size))
        for x in range(27,width-20,34):
            floor=_floor_near(doc,x,levels[1]+1)
            for px in range(x,x+5):
                if floor-1>1:_air(doc,px,floor-1);doc.set("water",px,floor-1,.72)
    elif theme=="crystal_mine":
        # Crystal districts progress from natural seams to an abandoned mine
        # and finally a ceremonial geode chamber.
        for x in range(10,width-8,7):
            floor=_floor_near(doc,x,levels[(x//23)%3]+1)
            doc.set("decoration",x,floor,("crystal",2 if x%3 else 3))
        for x in range(18,width-12,16):
            floor=_floor_near(doc,x,levels[(x//29)%3]+1)
            doc.set("decoration",x,floor,("mine_support",2))
            if x%32==18:doc.set("decoration",x+3,floor,("crystal_lantern",1))
        for x,kind,size in (
            (15,"ceiling_crystal",3),(34,"crystal_vein",2),(49,"mine_cart",2),
            (65,"crystal_arch",3),(81,"shattered_crystal",2),(98,"hoist_chain",3),
            (115,"crystal_shrine",3),(130,"ceiling_crystal",2),
        ):
            floor=_floor_near(doc,x,levels[(x//24)%3]+1)
            doc.set("decoration",x,floor,(kind,size))
    elif theme=="underground_castle":
        for x in range(12,width-10,10):
            floor=_floor_near(doc,x,levels[1]+1)
            doc.set("decoration",x,floor,("fence",2))
            if x%20==12:doc.set("decoration",x+2,floor,("lamp",1))
        for x in range(20,width-12,24):
            floor=_floor_near(doc,x,levels[(x//24)%3]+1)
            doc.set("decoration",x,floor,("castle_banner",2))
        floor=_floor_near(doc,width//2,levels[1]+1);doc.set("decoration",width//2,floor,("stone_throne",3))
    elif theme=="boss_dungeon":
        floor=_floor_near(doc,width//2,levels[1]+1)
        doc.set("decoration",width//2,floor,("boss_gate",3))
        for x in (22,48,84,106,126):
            floor=_floor_near(doc,x,levels[(x//34)%3]+1)
            doc.set("decoration",x,floor,("rune_obelisk",3 if x in (84,106) else 2))
        arena_floor=_floor_near(doc,width-24,levels[1]+1);doc.set("decoration",width-24,arena_floor,("abyss_altar",3))
    elif theme=="magma_hell":
        # Large lava rivers on the two lower levels. Hazards stay non-solid.
        for row in (levels[1],levels[2]):
            for x in range(10,width-10):
                if (x//11)%3!=1:
                    continue
                floor=_floor_near(doc,x,row+1)
                if floor-1>1:
                    _air(doc,x,floor-1)
                    doc.set("lava",x,floor-1,1.0)
        for x in range(14,width-12,17):
            floor=_floor_near(doc,x,levels[2]+1)
            doc.set("decoration",x,floor,("obsidian_spire",1+(x//17)%3))
        for x in range(18,width-10,22):
            floor=_floor_near(doc,x,levels[(x//22)%3]+1)
            doc.set("decoration",x,floor,("magma_totem",2 if x%44 else 3))
        for x,kind,size in (
            (16,"bone_pile",2),(33,"lava_fall",3),(48,"hanging_chain",3),
            (67,"hell_furnace",3),(83,"ember_vent",2),(101,"demon_statue",3),
            (118,"hell_rune",3),(132,"lava_fall",2),
        ):
            floor=_floor_near(doc,x,levels[(x//25)%3]+1)
            doc.set("decoration",x,floor,(kind,size))

    # Theme-skinned background hazards use the same bounded, non-diggable trap
    # runtime as the pyramid.  Layout metadata owns placement and timing.
    trap_skin={"glow_moss":"glow_moss","crystal_mine":"crystal","underground_castle":"castle","boss_dungeon":"boss","magma_hell":"magma"}.get(theme,"stone")
    trap_xs={
        "glow_moss":(29,63,98,121), "crystal_mine":(25,58,89,119),
        "underground_castle":(31,61,96,123), "boss_dungeon":(34,67,98,128),
        "magma_hell":(27,57,87,118),
    }.get(theme,(30,64,98,122))
    dungeon_traps=[]
    for index,x in enumerate(trap_xs,1):
        preferred=levels[index%3]+1;floor=_floor_near(doc,x,preferred)
        dungeon_traps.append(_trap_row(
            "%s_spike_%02d"%(theme,index),"hidden_spike",x,
            floor=floor,trigger_width=1.18,damage=16.0+index*1.5,
            interval=1.15+(index%2)*.28,skin=trap_skin,
        ))
    launcher_xs=(39,73,108)
    for index,x in enumerate(launcher_xs,1):
        floor=_floor_near(doc,x,levels[(index-1)%3]+1);y=max(2,floor-2)
        direction=[1.0,0.0] if index%2 else [-1.0,0.0];dx=1 if direction[0]>0 else -1
        _air(doc,x,y);_solid(doc,x-dx,y,_material(seed,x-dx,y,theme))
        dungeon_traps.append(_trap_row(
            "%s_launcher_%02d"%(theme,index),"wall_arrow",x,y=y,
            direction=direction,interval=2.0+index*.26,speed=390.0+index*18.0,
            damage=13.0+index, lifetime=3.0,delay=index*.31,skin=trap_skin,
        ))
    orb_x=52 if theme in ("glow_moss","underground_castle") else 116
    orb_floor=_floor_near(doc,orb_x,levels[1]+1);orb_y=max(2,orb_floor-3);orb_dx=1 if orb_x<width//2 else -1
    _air(doc,orb_x,orb_y);_solid(doc,orb_x-orb_dx,orb_y,_material(seed,orb_x-orb_dx,orb_y,theme))
    dungeon_traps.append(_trap_row(
        "%s_orb_01"%theme,"bouncing_fireball",orb_x,y=orb_y,
        direction=[float(orb_dx),-.38],speed=245.0 if theme=="glow_moss" else 285.0,
        damage=19.0 if theme!="boss_dungeon" else 25.0,lifetime=5.8,
        respawn=1.18,interval=999.0,delay=.75,skin=trap_skin,
    ))

    creature_profiles={
        "glow_moss":(("cave_snorble",18,18,2,8),("blue_poop",45,32,3,7),("cave_bat",75,18,3,8),("giant_worm",105,46,2,10)),
        "crystal_mine":(("crystal_slime",20,18,4,8),("crystal_skeleton",56,32,3,10),("crystal_knight",91,46,3,10),("crystal_lizard",116,18,3,7)),
        "underground_castle":(("dungeon_bandit_mage",24,18,2,10),("zombie",58,32,3,7),("speaker_man",103,46,2,9)),
        "boss_dungeon":(("lightning_zombie",28,18,2,8),("minos",63,32,1,4),("giant_spider_monster",111,46,2,10)),
        "magma_hell":(("flying_imp",18,18,4,9),("infernal_goat",46,32,3,13),("burning_slime",72,46,4,8),("flame_turtle",104,32,2,14),("exploding_wisp",124,18,2,9)),
    }
    creature_spawns=[]
    for species,x,preferred,count,spacing in creature_profiles.get(theme,()):
        creature_spawns.append({"species":species,"x":x,"floor":_floor_near(doc,x,preferred),"count":count,"spacing":spacing})

    terraced=_terrace_floors(doc,seed)
    biome={
        "glow_moss":BIOME_RAINFOREST,
        "crystal_mine":BIOME_SNOW_MOUNTAIN,
        "boss_dungeon":BIOME_DESERT,
        "underground_castle":BIOME_PLAINS,
        "magma_hell":BIOME_DESERT,
    }.get(theme,BIOME_PLAINS)
    # FIX35 all underground submaps terminate in the same bottom magma
    # reservoir, providing a natural world-bottom hazard and a drain for vents.
    bottom=height-1
    for x in range(1,width-1):
        doc.erase_layer("terrain",x,bottom)
        doc.erase_layer("ground_layer",x,bottom)
        doc.set("lava",x,bottom,1.0)
    lava_sources=[]
    if theme=="magma_hell":
        for sx in (max(8,width//3),min(width-9,(width*2)//3)):
            sy=max(5,levels[1]-5)
            for yy in range(sy,bottom):
                doc.erase_layer("terrain",sx,yy)
                doc.erase_layer("ground_layer",sx,yy)
                doc.erase_layer("water",sx,yy)
            doc.set("lava",sx,sy,0.65)
            lava_sources.append([sx,sy,0.46])

    spawn_floor=_floor_near(doc,10,levels[1]+1)
    doc.player_spawn=[10,spawn_floor]
    encounter_zones=[
        {"id":"entry","bounds":[4,10,width//3,34],"role":"teach_theme"},
        {"id":"crossroads","bounds":[width//3,18,(width*2)//3,48],"role":"mixed_traps"},
        {"id":"deep_chamber","bounds":[(width*2)//3,10,width-5,52],"role":"elite_encounter"},
    ]
    if enhanced:
        encounter_zones=[
            {"id":"threshold","bounds":[4,11,31,35],"role":"safe_read_and_first_enemy"},
            {"id":"upper_loop","bounds":[28,8,76,28],"role":"vertical_ambush"},
            {"id":"central_landmark","bounds":[48,22,99,42],"role":"signature_set_piece"},
            {"id":"lower_detour","bounds":[38,36,108,53],"role":"risk_reward_route"},
            {"id":"deep_guard","bounds":[101,10,width-5,53],"role":"elite_exit_guard"},
        ]
    doc.metadata.update({
        "name":str(map_id),"map_id":str(map_id),"map_type":"submap",
        "theme":str(theme),"background_theme":str(theme),"generated":True,"generator":"v0.7.7.5-fix62-authored-underground",
        "seed":seed,"width":width,"height":height,"biome_version":9,
        "biome_spans":[[0,width,biome]],"underground_only":True,
        "underground_level_rows":list(levels),"underground_caverns":caverns,
        "underground_dungeons":dungeons,"spawn_abyss_boss":theme=="boss_dungeon",
        "underground_terraced_cells":int(terraced),"portals":[],
        "lava_sources":lava_sources,"lava_bottom_reservoir_row":bottom,
        "trap_layer":"background_hazard","trap_cells_indestructible":True,
        "dungeon_traps":dungeon_traps,"creature_spawns":creature_spawns,
        "underground_population_mode":"authored_only" if enhanced else "mixed",
        "encounter_zones":encounter_zones,
        "style_guide":{
            "glow_moss":{"palette":["#102D2A","#2B725A","#58E6B5"],"architecture":"rounded living caverns, root arches and flooded pockets","hazard":"toxic thorn and spore orb","landmarks":["giant luminous fungi","hanging glow vines","spore nursery","root knot"],"route_language":"organic loops with submerged detours and alternating shafts"},
            "crystal_mine":{"palette":["#15243D","#365B82","#84C9FF"],"architecture":"natural geodes interrupted by cut galleries and abandoned braces","hazard":"crystal spikes and shard launchers","landmarks":["ceiling geode","broken mine cart","hoist shaft","crystal shrine"],"route_language":"angular switchbacks, mining shortcuts and high-low crosslinks"},
            "underground_castle":{"palette":["#171722","#353443","#8A344A"],"architecture":"ruined keep, cells and banners","hazard":"iron plates, bolts and cursed orb"},
            "boss_dungeon":{"palette":["#120F1B","#3A243F","#B34C68"],"architecture":"ritual halls and abyss arena","hazard":"void runes and elite gauntlet"},
            "magma_hell":{"palette":["#260D12","#641C17","#FF6A19"],"architecture":"basalt islands, infernal forges, lava falls and chain bridges","hazard":"obsidian spikes and magma orb","landmarks":["hell furnace","demon effigy","ember vent","infernal rune circle"],"route_language":"broken ledges, furnace loops and hazardous vertical bypasses"},
        }.get(theme,{}),
        "box_garden_rules":{
            "multiple_vertical_routes":True,"loops_and_shortcuts":True,
            "interconnected_submaps":True,"one_layer_walkable":True,
        },
    })
    doc.metadata.update(background_metadata(theme))
    return doc



def _top_surface_floor(doc, tx):
    """Return the first real outdoor landing, never a buried cavern floor."""
    tx = max(2, min(doc.width - 3, int(tx)))
    for y in range(3, doc.height - 2):
        if doc.get("terrain", tx, y, None) is None:
            continue
        if all(doc.get("terrain", tx, y - dy, None) is None for dy in (1, 2, 3)):
            return y
    return _floor_near(doc, tx, 12)


def _authored_ground_floor(doc, tx):
    """Find the natural overworld ground while ignoring floating islands."""
    tx=max(2,min(doc.width-3,int(tx)))
    tree_system=(getattr(doc,"metadata",{}) or {}).get("rainforest_tree_system",{})
    for row in tree_system.get("natural_surface",()) if isinstance(tree_system,dict) else ():
        try:
            if int(row[0])==tx:
                return max(3,min(doc.height-2,int(row[1])))
        except Exception:
            continue
    for y in range(max(3,int(WORLD_GEN_SURFACE_ROW)),doc.height-2):
        if doc.get("terrain",tx,y,None) is not None:
            return y
    return _floor_near(doc,tx,int(WORLD_GEN_SURFACE_ROW))


def _carve_chamber(doc, cx, cy, rx, ry):
    """Carve a rounded chamber so rooms do not read as stacked rectangles."""
    cx = int(cx); cy = int(cy); rx = max(2, int(rx)); ry = max(2, int(ry))
    for y in range(cy - ry, cy + ry + 1):
        ratio = float(y - cy) / float(ry)
        span = int(round(rx * math.sqrt(max(0.0, 1.0 - ratio * ratio))))
        _carve_rect(doc, cx - span, y, cx + span, y)


def _carve_sloped_route(doc, points, height=5, width=1):
    """Carve connected stepped/diagonal passages through a list of floor points."""
    for index in range(len(points) - 1):
        x0, f0 = points[index]; x1, f1 = points[index + 1]
        steps = max(1, max(abs(int(x1) - int(x0)), abs(int(f1) - int(f0))) * 2)
        for step in range(steps + 1):
            t = float(step) / float(steps)
            x = int(round(float(x0) + (float(x1) - float(x0)) * t))
            floor = int(round(float(f0) + (float(f1) - float(f0)) * t))
            for ox in range(-int(width), int(width) + 1):
                for y in range(floor - int(height), floor):
                    _air(doc, x + ox, y)


def _trap_row(trap_id, kind, x, **values):
    row = {"id": str(trap_id), "type": str(kind), "x": int(x), "layer": "background_hazard", "diggable": False}
    row.update(values)
    return row
def _add_desert_pyramid(main):
    spans = list(main.metadata.get("biome_spans", []) or [])
    desert = next((row for row in spans if len(row) >= 3 and str(row[2]) == BIOME_DESERT), None)
    if desert is None:
        return
    start, end = int(desert[0]), int(desert[1])
    center = int(start + (end - start) * .58)
    # FIX44: use the first sky-exposed desert support, not a nearer underground
    # corridor.  The old _floor_near(..., 20) selected a cavern around row 24.
    floor = _authored_ground_floor(main, center)

    # FIX46 keeps one authoritative foreground geometry, but the limestone is
    # ordinary mineable terrain.  We still remember the shell only for the
    # anti-climb rule; it is no longer an indestructible gameplay exception.
    protected = set()
    for level in range(10):
        for x in range(center - 12 + level, center + 13 - level):
            y = floor - 1 - level
            _solid(main, x, y, "limestone")
            protected.add((x, y))
    _carve_rect(main, center - 3, floor - 5, center + 3, floor - 1)
    for x in range(center - 3, center + 4):
        for y in range(floor - 5, floor):
            protected.discard((x, y))

    # The real four-cell foundation closes the route from underground. It is
    # visible limestone, not an invisible collider or decorative background.
    for x in range(center - 12, center + 13):
        for y in range(floor, min(main.height, floor + 4)):
            _solid(main, x, y, "limestone")
            protected.add((x, y))

    portal = _add_portal(main, "to_pyramid", "egyptian_pyramid.json", "to_desert", center, floor)
    portal["visual"] = "pyramid_door"
    portal["requires_floor_entry"] = True
    portal["entry_floor"] = int(floor)
    portal["entry_y_tolerance_px"] = 8
    protected_rows = [[int(x), int(y)] for x, y in sorted(protected)]
    # No permanent mining lock: players can dig through the limestone shell
    # to reach the physical portal. Keep no-climb on intact pyramid cells so
    # the earlier wall-climbing/pass-through exploit does not return.
    main.metadata["indestructible_terrain_cells"] = []
    main.metadata["no_climb_terrain_cells"] = list(protected_rows)
    main.metadata.setdefault("landmarks", []).append({
        "kind": "desert_pyramid", "center": [center, floor],
        "surface_entrance": True, "base_width": 25, "height": 10,
        "geometry": "foreground_physical_tiles", "diggable": True,
    })


def _add_horizontal_world_wrap(main):
    """Join the right desert coast to the left ocean at every world depth."""
    width = int(main.width); height = int(main.height)
    if width < 96 or height < 40:
        return
    coast_width = min(32, max(16, width // 12))
    coast_start = width - coast_width
    ocean_surface = max(3,min(height-4,int(WORLD_GEN_SURFACE_ROW)))
    ocean_surface_amount = max(0.05, min(1.0, float(main.get("water", 0, ocean_surface, 1.0) or 1.0)))
    desert_floor = _authored_ground_floor(main, coast_start - 1)
    target_floor = _authored_ground_floor(main,0)

    for x in range(coast_start, width):
        t = float(x - coast_start) / float(max(1, coast_width - 1))
        smooth = t * t * (3.0 - 2.0 * t)
        floor = int(round(float(desert_floor) + (target_floor - desert_floor) * smooth))
        for y in range(1, min(height, floor + 1)):
            main.erase_layer("water", x, y)
            if y < floor:
                _air(main, x, y)
            for layer in ("vegetation", "fire", "hazard", "decoration"):
                main.erase_layer(layer, x, y)
        _solid(main, x, floor, "sea_sand")
        if floor + 1 < height:
            _solid(main, x, floor + 1, "sand")
        for y in range(ocean_surface, floor):
            main.set("water", x, y, ocean_surface_amount if y == ocean_surface else 1.0)

    # Matching edge tunnels make the cylinder traversable underground too.
    tunnel_floors = [
        int(row)+4 for row in (main.metadata.get("underground_level_rows",()) or ())
        if 7 <= int(row)+4 < height-2
    ]
    approach = min(56, max(24, width // 10))
    edge_columns = list(range(0, approach)) + list(range(width - approach, width))
    for floor in tunnel_floors:
        for x in edge_columns:
            _solid(main, x, floor - 5, "stone")
            for y in range(floor - 4, floor):
                _air(main, x, y)
            _solid(main, x, floor, "stone")

    spans = []
    for row in list(main.metadata.get("biome_spans", []) or []):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        start, end, biome = int(row[0]), int(row[1]), str(row[2])
        if biome == BIOME_DESERT:
            end = min(end, coast_start)
        if end > start:
            spans.append([start, end, biome])
    spans.append([coast_start, width, BIOME_OCEAN])
    spans.sort(key=lambda row: int(row[0]))
    main.metadata["biome_spans"] = spans
    main.metadata["horizontal_wrap"] = True
    main.metadata["horizontal_wrap_version"] = 1
    main.metadata["wrap_coast"] = {
        "start": int(coast_start), "end": int(width),
        "water_surface": int(ocean_surface), "target_floor": int(target_floor),
        "background_blend_tiles": 8,
    }
    main.metadata["wrap_underground_floors"] = [int(v) for v in tunnel_floors]
    main.metadata["wrap_underground_approach_tiles"] = int(approach)

def build_egyptian_pyramid(seed, width=160, height=64):
    doc = MapDocument(width, height)
    for layer in doc.layers.values():
        layer.clear()
    for x in range(width):
        for y in range(height):
            _solid(doc, x, y, "limestone" if _hash01(seed, x, y, 7201) > .18 else "marble")

    # Six chambers use rounded/stepped silhouettes and are joined by diagonal
    # stair tunnels.  This replaces FIX42's three perfectly horizontal floors.
    _carve_chamber(doc, 13, 13, 11, 5)    # entrance antechamber, floor ~18
    _carve_chamber(doc, 52, 21, 18, 7)    # column hall, floor ~28
    _carve_chamber(doc, 88, 17, 17, 8)    # solar chamber, floor ~25
    _carve_chamber(doc, 139, 13, 18, 7)   # sphinx court, floor ~20
    _carve_chamber(doc, 45, 37, 21, 7)    # scarab vault, floor ~44
    _carve_chamber(doc, 116, 41, 25, 8)   # serpent sanctum, floor ~49

    _carve_sloped_route(doc, ((20, 18), (31, 21), (39, 25), (52, 28)), height=5, width=1)
    _carve_sloped_route(doc, ((65, 27), (75, 24), (88, 25), (104, 23), (122, 20)), height=5, width=1)
    _carve_sloped_route(doc, ((30, 25), (25, 30), (31, 35), (27, 40), (34, 44)), height=5, width=1)
    _carve_sloped_route(doc, ((62, 43), (73, 40), (84, 44), (96, 49)), height=5, width=1)
    _carve_sloped_route(doc, ((105, 49), (114, 45), (124, 49), (137, 47)), height=5, width=1)
    _carve_sloped_route(doc, ((103, 23), (110, 29), (105, 35), (112, 41)), height=5, width=1)

    # Two short ladders remain as optional shortcuts, but no route is a stack
    # of full-width horizontal/vertical corridors anymore.
    _ladder(doc, 72, 24, 40)
    _ladder(doc, 145, 19, 46)

    def floor_at(x, preferred):
        return int(_floor_near(doc, int(x), int(preferred)))

    decorations = (
        (10, 18, "brazier", 1), (42, 28, "egypt_column", 2),
        (58, 28, "hieroglyph", 2), (86, 25, "brazier", 1),
        (96, 25, "sarcophagus", 2), (132, 20, "sphinx_statue", 3),
        (38, 44, "hieroglyph", 3), (57, 44, "sarcophagus", 2),
        (105, 49, "brazier", 1), (126, 49, "hieroglyph", 3),
    )
    for x, preferred, kind, size in decorations:
        doc.set("decoration", x, floor_at(x, preferred), (kind, size))

    traps = []
    for index, (x, preferred) in enumerate(((24, 20), (46, 28), (80, 25), (115, 22), (39, 44), (87, 46), (130, 49)), 1):
        floor = floor_at(x, preferred)
        traps.append(_trap_row(
            "spike_%02d" % index, "hidden_spike", x,
            floor=floor, trigger_width=1.15, damage=18.0, interval=1.25 + (index % 2) * .25,
        ))

    # Launchers are stored in air cells with a solid wall immediately behind
    # them.  They are metadata/background hazards and cannot be mined away.
    launcher_defs = (
        ("arrow_01", "wall_arrow", 35, 22, [1.0, 0.0], 2.15, 390.0, 14.0, 0.25),
        ("arrow_02", "wall_arrow", 68, 20, [-1.0, 0.0], 2.55, 420.0, 14.0, 1.05),
        ("arrow_03", "wall_arrow", 122, 15, [1.0, 0.0], 2.30, 410.0, 15.0, 0.60),
        ("arrow_04", "wall_arrow", 66, 38, [-1.0, 0.0], 2.05, 430.0, 15.0, 1.40),
        ("arrow_05", "wall_arrow", 92, 43, [1.0, 0.0], 2.70, 440.0, 16.0, 0.90),
    )
    for trap_id, kind, x, y, direction, interval, speed, damage, delay in launcher_defs:
        dx = 1 if direction[0] > 0 else -1
        _air(doc, x, y); _solid(doc, x - dx, y, "limestone")
        traps.append(_trap_row(trap_id, kind, x, y=y, direction=direction, interval=interval, speed=speed, damage=damage, lifetime=3.2, delay=delay))

    fireball_defs = (
        ("fireball_01", 37, 18, [1.0, -0.42], 245.0, 5.5, 0.35),
        ("fireball_02", 102, 15, [-1.0, -0.30], 270.0, 6.0, 1.10),
        ("fireball_03", 92, 38, [1.0, -0.52], 255.0, 6.4, 0.75),
        ("fireball_04", 139, 41, [-1.0, -0.36], 285.0, 5.8, 1.55),
    )
    for trap_id, x, y, direction, speed, lifetime, delay in fireball_defs:
        dx = 1 if direction[0] > 0 else -1
        _air(doc, x, y); _solid(doc, x - dx, y, "marble")
        traps.append(_trap_row(
            trap_id, "bouncing_fireball", x, y=y, direction=direction,
            speed=speed, damage=20.0, lifetime=lifetime, respawn=1.05,
            interval=999.0, delay=delay,
        ))

    mummy_floor = floor_at(29, 22)
    sphinx_floor = floor_at(143, 20)
    minos_floor = floor_at(77, 25)
    isis_floor = floor_at(121, 49)
    scarab_floor = floor_at(48, 44)
    doc.player_spawn = [10, floor_at(10, 18)]
    doc.metadata.update({
        "name": "古埃及金字塔機關地牢", "map_id": "egyptian_pyramid",
        "map_type": "submap", "theme": "egyptian_pyramid",
        "background_theme": "egyptian_pyramid", "generated": True,
        "generator": "v0.7.7.5-fix44-pyramid-trap-dungeon", "seed": seed,
        "width": width, "height": height, "biome_version": 11,
        "biome_spans": [[0, width, BIOME_DESERT]], "underground_only": True,
        "underground_level_rows": [18, 25, 28, 40, 44, 49],
        "underground_caverns": [[13, 13, 11, 5], [52, 21, 18, 7], [88, 17, 17, 8], [139, 13, 18, 7], [45, 37, 21, 7], [116, 41, 25, 8]],
        "underground_dungeons": [{"bounds": [2, 6, 158, 51], "style": "irregular_trap_tomb"}],
        "trap_layer": "background_hazard", "trap_cells_indestructible": True,
        "pyramid_traps": traps,
        "creature_spawns": [
            {"species": "mummy", "x": 29, "floor": mummy_floor, "count": 2, "spacing": 8},
            {"species": "sphinx_monster", "x": 143, "floor": sphinx_floor, "count": 1},
            {"species": "minos", "x": 77, "floor": minos_floor, "count": 1},
            {"species": "isis_serpent", "x": 121, "floor": isis_floor, "count": 1},
            {"species": "blue_scarab", "x": 48, "floor": scarab_floor, "count": 7, "spacing": 3},
        ],
    })
    doc.metadata.update(background_metadata("egyptian_pyramid"))
    return_portal = _add_portal(doc, "to_desert", "editor_map.json", "to_pyramid", 7, doc.player_spawn[1])
    return_portal["requires_floor_entry"] = True
    return_portal["entry_floor"] = int(return_portal["rect"][1] + return_portal["rect"][3])
    return_portal["entry_y_tolerance_px"] = 8
    return doc

def build_world_graph(project_root, seed=20260901):
    """Generate the main world and register all interconnected child worlds."""
    root=os.path.abspath(project_root)
    maps=os.path.join(root,"maps");os.makedirs(maps,exist_ok=True)
    seed=int(seed)
    main=generate_world(seed,width=WORLD_GEN_DEFAULT_WIDTH,height=WORLD_GEN_DEFAULT_HEIGHT)
    main.metadata["map_id"]="main_world"
    main.metadata["spawn_abyss_boss"]=True
    main.metadata["world_graph_version"]=5
    main.metadata["portals"]=[]
    main.metadata.update(dynamic_background_metadata())
    levels=main.metadata.get("underground_level_rows",[24,38,52])
    # two independent entries from the main map
    _add_portal(main,"to_glow","glow_moss_cavern.json","to_main",int(main.width*.60),int(levels[0])+1)
    _add_portal(main,"to_castle","underground_castle.json","to_main",int(main.width*.50),int(levels[1])+1)
    _add_desert_pyramid(main)
    # The rainforest surface provides a natural visual transition into the
    # bamboo district of the martial-arts world. This is a real decoration +
    # collision-safe apron, not metadata-only topology.
    _span_by_biome={str(row[2]):(int(row[0]),int(row[1])) for row in main.metadata.get("biome_spans",())}
    _rain_start,_rain_end=_span_by_biome.get(BIOME_RAINFOREST,(int(main.width*.55),int(main.width*.70)))
    _wuxia_x=int(_rain_start+(_rain_end-_rain_start)*.68)
    wuxia_gate=_add_portal(
        main,"to_wuxia","wuxia_world.json","to_main_world",
        _wuxia_x,_authored_ground_floor(main,_wuxia_x)
    )
    wuxia_gate.update({
        "route_id":"main_wuxia","visual":"bamboo_rift",
        "requires_floor_entry":True,"entry_floor":int(wuxia_gate["rect"][1]+wuxia_gate["rect"][3]),
        "entry_y_tolerance_px":8,
    })
    # Reuse the deepest dungeon's decorative boss gate. Its coordinate is
    # derived from authored bounds so map-width/depth changes cannot detach the
    # route from the gate artwork.
    _boss_zone=next((
        row for row in main.metadata.get("underground_biomes",())
        if isinstance(row,dict) and str(row.get("type",""))=="boss_dungeon"
    ),{})
    try:
        _bx0,_by0,_bx1,_by1=[int(value) for value in _boss_zone.get("bounds",())]
        _biolume_x,_biolume_floor=(_bx0+_bx1)//2,_by1
    except Exception:
        _biolume_x,_biolume_floor=int(main.width*.92),int(levels[-1])+1
    biolume_gate=_bind_existing_portal(
        main,"to_biolume","biolume_sky_world.json","to_main_world",
        _biolume_x,_biolume_floor,boss=True
    )
    biolume_gate.update({
        "route_id":"main_biolume","visual":"biolume_seal",
        "requires_floor_entry":True,"entry_floor":int(biolume_gate["rect"][1]+biolume_gate["rect"][3]),
        "entry_y_tolerance_px":8,
    })
    _add_horizontal_world_wrap(main)

    glow=build_submap("glow_moss_cavern","glow_moss",seed+101)
    crystal=build_submap("crystal_mine","crystal_mine",seed+202)
    castle=build_submap("underground_castle","underground_castle",seed+303)
    boss=build_submap("boss_dungeon","boss_dungeon",seed+404)
    magma=build_submap("magma_hell","magma_hell",seed+505)
    pyramid=build_egyptian_pyramid(seed+606)
    # FIX69 secondary worlds remain deterministic offline authoring helpers.
    # Regenerating the world graph must recreate them too, otherwise pressing
    # 「箱庭世界」would leave topology entries pointing at stale/missing files.
    from systems.wuxia_world import build_wuxia_world
    from systems.biolume_world import build_biolume_world
    wuxia=build_wuxia_world(seed=seed+707)
    biolume=build_biolume_world(width=224,height=72)

    # Bidirectional graph with loops, not a simple linear chain.
    _add_portal(glow,"to_main","editor_map.json","to_glow",8,33)
    _add_portal(glow,"to_crystal","crystal_mine.json","to_glow",glow.width-9,33)

    _add_portal(crystal,"to_glow","glow_moss_cavern.json","to_crystal",8,33)
    _add_portal(crystal,"to_castle","underground_castle.json","to_crystal",crystal.width-9,33)

    _add_portal(castle,"to_main","editor_map.json","to_castle",8,33)
    _add_portal(castle,"to_crystal","crystal_mine.json","to_castle",castle.width-9,19)
    _add_portal(castle,"to_boss","boss_dungeon.json","to_castle",castle.width-9,33,boss=True)
    _add_portal(castle,"to_magma","magma_hell.json","to_castle",castle.width-9,47)

    _add_portal(boss,"to_castle","underground_castle.json","to_boss",8,33)
    _boss_levels=boss.metadata.get("underground_level_rows",(18,32,46))
    wuxia_back_gate=_bind_existing_portal(
        boss,"to_wuxia_graveyard","wuxia_world.json","to_boss_dungeon",
        boss.width//2,_floor_near(boss,boss.width//2,int(_boss_levels[1])+1),boss=True,
    )
    wuxia_back_gate.update({
        "route_id":"boss_wuxia_graveyard","visual":"grave_seal",
        "requires_floor_entry":True,"entry_floor":int(wuxia_back_gate["rect"][1]+wuxia_back_gate["rect"][3]),
        "entry_y_tolerance_px":8,
    })
    # Enter the infernal map on its upper basalt gallery.  The lower two levels
    # carry pressure-fed lava rivers and are intentionally reached only after
    # the player has control, never as a load/respawn surface.
    _add_portal(magma,"to_castle","underground_castle.json","to_magma",8,19)

    # Portal carving may create a landing one row above the preliminary spawn
    # chosen by build_submap.  Make that validated arrival the authored restart
    # point too, so a clean start can never begin with the player's head inside
    # the newly-created landing block.
    for child in (glow,crystal,castle,boss,magma):
        rows=child.metadata.get("portals",[]) or []
        if rows and isinstance(rows[0],dict):
            arrival=rows[0].get("arrival",())
            if isinstance(arrival,(list,tuple)) and len(arrival)>=2:
                child.player_spawn=[int(arrival[0]),int(arrival[1])]

    docs={
        "editor_map.json":main,
        "glow_moss_cavern.json":glow,
        "crystal_mine.json":crystal,
        "underground_castle.json":castle,
        "boss_dungeon.json":boss,
        "magma_hell.json":magma,
        "egyptian_pyramid.json":pyramid,
        "wuxia_world.json":wuxia,
        "biolume_sky_world.json":biolume,
    }
    for name,doc in docs.items():doc.save(os.path.join(maps,name))
    # Secondary-world teams own their content. If their authored files are
    # present, include them in the readable graph without regenerating/saving
    # those maps here.
    graph_docs=dict(docs)
    for name in ("wuxia_world.json","biolume_sky_world.json"):
        path=os.path.join(maps,name)
        if not os.path.isfile(path):
            continue
        try:graph_docs[name]=MapDocument.load(path)
        except Exception as exc:print("WORLD GRAPH external map skipped:",name,repr(exc))
    topology=WorldTopology.from_project_root(root)
    world_by_map={
        str(row.get("entry_map","")):row
        for row in topology.worlds.values() if isinstance(row,dict)
    }
    graph_links=[]
    for name,doc in graph_docs.items():
        for row in (doc.metadata.get("portals",[]) or []):
            if not isinstance(row,dict):continue
            resolved=topology.resolve(name,row) or dict(row)
            graph_links.append({
                "route_id":str(resolved.get("route_id",row.get("route_id","")) or ""),
                "from":name,
                "portal":str(row.get("id","")),
                "to":str(resolved.get("target_map","")),
                "target_portal":str(resolved.get("target_portal","")),
            })
    graph={
        "format":2,"version":"FIX69","main":"editor_map.json",
        "topology_catalog":"assets/world_topology.json",
        "worlds":[dict(row) for row in topology.worlds.values()
                  if str(row.get("entry_map","")) in graph_docs],
        "maps":{name:{
            "map_id":str(doc.metadata.get("map_id",name)),
            "world_id":str((world_by_map.get(name) or {}).get("id",doc.metadata.get("map_id",name))),
            "display_name":str((world_by_map.get(name) or {}).get("display_name",doc.metadata.get("name",doc.metadata.get("map_id",name)))),
            "theme":str(doc.metadata.get("theme","main")),
            "background_profile":str(doc.metadata.get("background_profile","")),
            "background_profile_mode":str(doc.metadata.get("background_profile_mode","")),
        } for name,doc in graph_docs.items()},
        "links":sorted(graph_links,key=lambda row:(row["from"],row["portal"])),
    }
    graph_path=os.path.join(root,"assets","world_graph.json")
    tmp=graph_path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:json.dump(graph,f,ensure_ascii=False,indent=2)
    os.replace(tmp,graph_path)
    portal_audit=topology.audit_bidirectional_files()
    if not bool(portal_audit.get("ok",False)):
        raise RuntimeError(
            "portal graph contains one-way or missing endpoints: "
            + " | ".join(portal_audit.get("errors",())[:8])
        )
    audit_path=os.path.join(root,"assets","portal_link_audit.json")
    audit_tmp=audit_path+".tmp"
    with open(audit_tmp,"w",encoding="utf-8") as f:
        json.dump(portal_audit,f,ensure_ascii=False,indent=2)
    os.replace(audit_tmp,audit_path)
    return docs
