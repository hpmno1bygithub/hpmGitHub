# -*- coding: utf-8 -*-
"""FIX88 CPU render plans for the existing single Metal quad batch.
No textures, UIImage decoding, per-item UIKit controls or GPU allocations.
"""
import math
from systems.boss_weapon_defs import BOSS_WEAPON_DEFS
from config import TILE_SIZE

BOSS_VISUAL_BUDGET = 2440  # prior 900 + bounded FIX102 python visuals (640)

def append_icon(renderer, out, game, weapon, cx, cy, span=34.0):
    """One source for backpack and drop icons, including editor edits."""
    if weapon not in BOSS_WEAPON_DEFS:
        return False
    data = game.assets.pixel_rects('weapon.'+weapon)
    if not data or not data.get('rects'):
        return False
    rects = data['rects']
    x0=min(r[0] for r in rects);y0=min(r[1] for r in rects)
    x1=max(r[2] for r in rects);y1=max(r[3] for r in rects)
    scale=float(span)/max(1.,x1-x0,y1-y0)
    mx,my=(x0+x1)*.5,(y0+y1)*.5
    for a,b,c,d,color in rects:
        out.append(renderer._quad(cx+((a+c)*.5-mx)*scale,cy+((b+d)*.5-my)*scale,
                                  (c-a)*scale+.03,(d-b)*scale+.03,color))
    return True


def _effect_data(game, weapon, heavy, age):
    state='heavy_attack' if heavy else 'normal_attack'
    binding=game.assets.combat_binding('weapon.'+weapon,state) or {}
    effect=binding.get('effect_state','heavy_effect' if heavy else 'normal_effect')
    effect_asset=str(binding.get('effect_asset','weapon.'+weapon) or ('weapon.'+weapon))
    data=game.assets.pixel_animation_rects(effect_asset,effect,time_seconds=age)
    if data is None and effect_asset!='weapon.'+weapon:
        data=game.assets.pixel_animation_rects('weapon.'+weapon,'heavy_effect' if heavy else 'normal_effect',time_seconds=age)
    return data, binding


def _beam_quads(renderer, game, beam):
    """Stretch a LOCAL effect strip along its exact world-space beam.
    Diagonal strips are subdivided to keep axis-aligned pixel quads narrow.
    The geometry remains visible beyond the 64px animation canvas.
    """
    data,binding=_effect_data(game,beam['weapon'],beam['heavy'],beam['age'])
    if not data or beam['length']<=0.:
        return []
    rects=data.get('rects',())
    n=max(1.,float(data.get('size',64)))
    ox,oy,ux,uy=beam['ox'],beam['oy'],beam['ux'],beam['uy']
    length,width=beam['length'],beam['width']
    # The collision-width remains authoritative; editor scale affects only
    # local art. Compact fixed limits protect Pyto when someone imports noise.
    scale=max(.25,min(2.,float(binding.get('scale',1.) or 1.)))
    out=[]
    # Long strips are primary, edge sparkle is expendable under heavy load.
    rows=sorted(rects,key=lambda r: -(r[2]-r[0])*(r[3]-r[1]))
    for x0,y0,x1,y1,color in rows:
        if len(out)>=112:
            break
        world_length=(x1-x0)/n*length
        diagonal=abs(ux)>.05 and abs(uy)>.05
        splits=max(1,min(32,int(math.ceil(world_length/12.)))) if diagonal else 1
        splits=min(splits,112-len(out))
        for j in range(splits):
            a=x0+(x1-x0)*j/splits;b=x0+(x1-x0)*(j+1)/splits
            along=(a+b)*.5/n*length
            normal=((y0+y1)*.5/n-.5)*width*scale
            dx=(b-a)/n*length;dy=(y1-y0)/n*width*scale
            out.append(renderer._quad(ox+ux*along-uy*normal,oy+uy*along+ux*normal,
                        abs(ux)*dx+abs(uy)*dy+.05,abs(uy)*dx+abs(ux)*dy+.05,color))
    return out


def _local_effect(renderer, game, effect, budget):
    data,binding=_effect_data(game,effect['weapon'],effect['heavy'],effect['age'])
    if not data:
        return []
    n=max(1.,float(data.get('size',64)));scale=2.5
    if effect['weapon']=='bee_swarm':
        scale=.75
    out=[]
    for a,b,c,d,color in data.get('rects',())[:max(0,budget)]:
        out.append(renderer._quad(effect['x']+((a+c)*.5-n*.5)*scale,
                                  effect['y']+((b+d)*.5-n*.5)*scale,
                                  (c-a)*scale+.04,(d-b)*scale+.04,color))
    return out


def reserve_quads(game):
    system=getattr(game,'boss_weapons',None)
    if system is None:
        return 0
    alive=sum(bool(b['active']) for b in system.bees)
    hostile_bees=sum(bool(b.get('active',True)) for b in getattr(system,'hostile_bees',()))
    hostile_shots=len(getattr(system,'hostile_projectiles',()))
    expansion_reserve=system.expansion.reserve()+system.python.reserve()+system.fix103.reserve()
    active=expansion_reserve+len(system.beams)+alive+hostile_bees+hostile_shots+len(system.impacts)
    hand=140 if getattr(game.melee,'selected_weapon','') in BOSS_WEAPON_DEFS else 0
    if not active:
        return hand
    return min(BOSS_VISUAL_BUDGET,hand+expansion_reserve+len(system.beams)*112+(alive+hostile_bees)*80+hostile_shots*90+min(120,len(system.impacts)*12))


def append_visuals(renderer, out, game, now=0., alpha=1.):
    system=getattr(game,'boss_weapons',None)
    if system is None:
        return
    generated=[]
    for beam in system.beams:
        generated.extend(_beam_quads(renderer,game,beam))
    for b in system.bees:
        if not b['active']:
            continue
        x=b['prev_x']+(b['x']-b['prev_x'])*alpha
        y=b['prev_y']+(b['y']-b['prev_y'])*alpha
        state=('heavy_attack' if b['state']=='heavy_dive' else
               'heavy_charge' if b['state']=='waiting' else
               'normal_attack' if b['state']=='dive' else 'idle')
        data=game.assets.pixel_animation_rects('weapon.bee_swarm',state,time_seconds=b['age'])
        if not data:
            continue
        n=float(data['size']);scale=1.20
        flip=(b['x']-b['prev_x'])<-.02
        for a,c,d,e,color in data['rects']:
            off=(a+d)*.5-n*.5
            if flip:off=-off
            generated.append(renderer._quad(x+off*scale,y+((c+e)*.5-n*.5)*scale,
                                             (d-a)*scale+.03,(e-c)*scale+.03,color))
        if b['state'] in ('dive','heavy_dive'):
            for j,(tx,ty) in enumerate(b['trail'][-3:]):
                generated.append(renderer._quad(tx,ty,2.8,2.8,(1.,.75,.25,.20+j*.18)))
    for p in getattr(system,'hostile_projectiles',()):
        generated.extend(_local_effect(renderer,game,dict(weapon=p['weapon'],heavy=True,age=p['age'],x=p['x'],y=p['y']),90))
    for b in getattr(system,'hostile_bees',()):
        if not b.get('active',True):
            continue
        x=b['prev_x']+(b['x']-b['prev_x'])*alpha
        y=b['prev_y']+(b['y']-b['prev_y'])*alpha
        state='heavy_charge' if b['age']<b['delay'] else 'heavy_attack'
        data=game.assets.pixel_animation_rects('weapon.bee_swarm',state,time_seconds=max(0.,b['age']-b['delay']))
        if not data: continue
        n=float(data['size']);scale=1.20
        flip=(b['x']-b['prev_x'])<-.02
        for a,c,d,e,color in data['rects']:
            off=(a+d)*.5-n*.5
            if flip: off=-off
            generated.append(renderer._quad(x+off*scale,y+((c+e)*.5-n*.5)*scale,
                                             (d-a)*scale+.03,(e-c)*scale+.03,color))
    # Every bee and both columns get drawn before decorative impact fragments.
    for f in reversed(system.impacts):
        room=max(0,BOSS_VISUAL_BUDGET-len(generated))
        if room<=0:
            break
        generated.extend(_local_effect(renderer,game,f,min(room,90)))
    # Authored images can be arbitrarily complex. Under pathological edits,
    # fallback per-bee silhouettes still preserve all eight actor positions.
    if len(generated)>BOSS_VISUAL_BUDGET:
        generated=[]
        for beam in system.beams:
            generated.extend(_beam_quads(renderer,game,beam))
        for b in system.bees:
            if b['active']:
                for dx,dy,w,h,c in ((0,0,12,7,(.94,.67,.18,1.)),(0,0,3,7,(.08,.12,.17,1.)),
                                   (-2,-5,8,4,(.75,.92,.99,1.)),(6,-1,3,3,(.08,.12,.17,1.))):
                    generated.append(renderer._quad(b['x']+dx,b['y']+dy,w,h,c))
    extra=[]
    system.expansion.append_visuals(renderer,extra,budget=480)
    python_visuals=[]
    system.python.append_visuals(renderer,python_visuals,budget=640)
    fix103_visuals=[]
    system.fix103.append_visuals(renderer,fix103_visuals,budget=1200)
    out.extend(generated[:max(0,BOSS_VISUAL_BUDGET-len(extra)-len(python_visuals)-len(fix103_visuals))])
    out.extend(extra)
    out.extend(python_visuals)
    out.extend(fix103_visuals)
