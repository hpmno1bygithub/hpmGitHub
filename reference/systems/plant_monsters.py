# -*- coding: utf-8 -*-
"""FIX82 rooted plants, ceiling ambushes and bounded cloud-island flight.

Gameplay effects use the existing toxic field, projectile collision, damage and
shield paths. No timers, UI objects or per-frame global terrain scans are added.
"""
import math
from config import TILE_SIZE, GRAVITY
from world.tile_registry import tile_def

PLANT_MODES = ("rooted", "ceiling")


def clear_ray(world, x1, y1, x2, y2):
    """Small-cell sampled segment prevents attacks through walls/floors."""
    steps = max(1, min(128, int(math.hypot(x2-x1, y2-y1)/(TILE_SIZE*.22))+1))
    for i in range(1, steps+1):
        t = i/float(steps)
        x, y = x1+(x2-x1)*t, y1+(y2-y1)*t
        tx, ty = int(x//TILE_SIZE), int(y//TILE_SIZE)
        if not (0 <= tx < world.width_tiles and 0 <= ty < world.height_tiles):
            return False
        if (tx,ty) in getattr(world, 'custom_solid_cells', ()):
            return False
        if tile_def(world.get_tile(tx,ty)).solid:
            top = world.solid_top_y(tx,ty)
            if top is None or y >= top:
                return False
    return True


def hold_plant(system, c, dt):
    world = system.physics.world
    c.x = float(c.home_x)
    c.vx = 0.0
    if c.locomotion == 'ceiling':
        c.y = float(c.home_y)
        c.vy = 0.0
        c.grounded = False
        tx = int(c.x//TILE_SIZE)
        ty = int((c.home_y-c.height()-1.0)//TILE_SIZE)
        if not tile_def(world.get_tile(tx,ty)).solid:
            # Mining the attachment removes this fixed hazard; normal death and
            # drop/respawn paths handle it, never an invisible floating mouth.
            c.hp = 0.0
    else:
        c.vy = min(700.0, float(c.vy)+GRAVITY*dt)
        hit, grounded = system.physics.move_vertical(c,c.vy*dt)
        if hit:
            c.vy=0.0
        c.grounded=bool(grounded)
        if grounded:
            c.home_y = float(c.y)


def plant_hit(system, c, action):
    p=system.player
    pb=p.combat_bbox() if hasattr(p,'combat_bbox') else p.bbox()
    effect=action.get('effect')
    if effect == 'ceiling_bite':
        sx,sy=float(c.x),float(c.y)-14.0
        reach=float(action.get('hit_radius',48.0))
        overlap=(pb[2]>=sx-c.width()*.5-8 and pb[0]<=sx+c.width()*.5+8
                 and pb[3]>=sy-16 and pb[1]<=sy+reach*.5)
        tx=max(pb[0],min(pb[2],sx));ty=max(pb[1],min(pb[3],sy))
    elif effect == 'vine_whip':
        sx,sy=float(c.x),float(c.y)-float(c.height())*.52
        direction=1 if c.ai_action_target_x>=c.x else -1
        reach=float(action.get('hit_radius',112.0))
        left,right=sorted((sx,sx+direction*reach))
        overlap=pb[2]>=left and pb[0]<=right and pb[3]>=sy-24 and pb[1]<=sy+24
        tx=max(pb[0],min(pb[2],sx+direction*reach));ty=max(pb[1],min(pb[3],sy))
    else:
        return None
    return bool(overlap and clear_ray(system.physics.world,sx,sy,tx,ty))


def release_spores(system,c,action):
    env=system.env
    if env is None:
        return False
    world=system.physics.world
    sx,sy=float(c.x),float(c.y)-float(c.height())*.7
    cx,cy=int(sx//TILE_SIZE),int(sy//TILE_SIZE)
    count=0
    # At most thirteen cells per release, local dose with a finite TTL. Existing
    # ToxicSystem supplies damage; fire and the reaction system consume the gas.
    for dy in range(-2,3):
        for dx in range(-2,3):
            if dx*dx+dy*dy>4:continue
            tx,ty=cx+dx,cy+dy
            px,py=(tx+.5)*TILE_SIZE,(ty+.5)*TILE_SIZE
            if not clear_ray(world,sx,sy,px,py):continue
            if system._point_in_water(px,py):continue
            key=(tx,ty)
            # Hard bounded field addition; never replace distant existing gas.
            if key not in env.poison_gas and len(env.poison_gas)>=384:continue
            env.poison_gas[key]=min(1.0,max(float(env.poison_gas.get(key,0)),.85-.1*abs(dx)))
            env.poison_gas_ttl[key]=max(float(env.poison_gas_ttl.get(key,0)),4.0)
            count+=1
    system._emit_attack_vfx(c,action=action,phase='release',outcome='spawned',
                            authoritative='attack',radius=2*TILE_SIZE,attack_kind='poison_puff')
    return bool(count)


def update_sky_flyer(system,c,dt,allow_hostile=True):
    c.motion_time=float(c.motion_time)+dt
    dx=system.player.x-c.x
    dy=(system.player.y-system.player.height()*.5)-(c.y-c.height()*.5)
    detected=allow_hostile and system._can_attack_player(c) and bool(c.player_detected)
    # All cloud flyers stay near their authored island; do not follow players
    # down into the ocean/caves or oscillate around the world's topmost floor.
    rx=max(48.,float(c.patrol_radius));ry=180. if c.species=="wind_god_pterosaur" else 88.
    if detected and abs(system.player.x-c.home_x)<rx*1.5 and abs(system.player.y-c.home_y)<ry*1.5:
        tx=max(c.home_x-rx,min(c.home_x+rx,system.player.x))
        ty=max(c.home_y-ry,min(c.home_y+(105. if c.species=="wind_god_pterosaur" else 32.),system.player.y-system.player.height()*.4))
        c.behavior_state='chase'
    else:
        tx=c.home_x+math.sin(c.motion_time*.55)*rx*.72
        ty=c.home_y+math.sin(c.motion_time*.91)*22.
        c.behavior_state='fly'
    c.vx=system._approach(c.vx,max(-c.speed,min(c.speed,(tx-c.x)*1.3)),c.speed*3*dt)
    c.vy=system._approach(c.vy,max(-c.speed*.7,min(c.speed*.7,(ty-c.y)*1.4)),c.speed*3*dt)
    oldx,oldy=c.x,c.y
    system.physics.move_horizontal(c,c.vx*dt)
    hit,_=system.physics.move_vertical(c,c.vy*dt)
    if hit:c.vy=0.;c.y=oldy
    if system._point_in_water(c.x,c.y):c.x,c.y=oldx,oldy;c.vx=c.vy=0.
    c.y=max(c.height()+2.,min(float(c.y),c.home_y+(125. if c.species=="wind_god_pterosaur" else 40.)))
    c.x=max(c.home_x-rx,min(float(c.x),c.home_x+rx))
    if abs(c.vx)>.5:c.facing=1 if c.vx>0 else -1
    c.grounded=False
    c.animation_state_override='move'
