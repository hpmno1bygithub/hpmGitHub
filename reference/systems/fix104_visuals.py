# -*- coding: utf-8 -*-
"""FIX104 visual-only adapters for the six FIX103 bosses.
No UIKit, additional threads, Pillow, NumPy or textures are needed on device.
The complete silhouette is reduced uniformly if the available quad budget is
small; no frame is truncated into a missing head/wing. Damage stays in FIX103.
"""
from collections import OrderedDict, Counter
import math
SPECIES=('lava_beetle_emperor','crystal_nine_tail','abyss_crab_king',
         'thunder_roc','drill_worm','ancient_tree_demon')
_CACHE=OrderedDict()
_CACHE_LIMIT=32

def _merge(grid,n):
    active={};out=[]
    for y,row in enumerate(grid):
        new={};x=0
        while x<n:
            col=row[x]
            if col is None:x+=1;continue
            a=x;x+=1
            while x<n and row[x]==col:x+=1
            key=(a,x,col)
            new[key]=(a,active[key][1] if key in active else y,x,y+1,col)
        out.extend(v for k,v in active.items() if k not in new)
        active=new
    out.extend(active.values())
    return tuple((a/n,b/n,c/n,d/n,col) for a,b,c,d,col in out)

def compact(data,budget=1200):
    """Bound CPU/GPU data while keeping the entire frame and transparent holes."""
    if not data:return ()
    budget=max(4,int(budget));size=max(1,int(data.get('size',1)))
    raw=data.get('rects',())
    if len(raw)<=budget:
        return tuple((a/size,b/size,c/size,d/size,col) for a,b,c,d,col in raw)
    key=(data.get('asset_id'),data.get('signature'),data.get('animation'),data.get('frame_index'),size,budget,id(raw))
    cached=_CACHE.get(key)
    if cached is not None:_CACHE.move_to_end(key);return cached
    # Grid is only 128x128 at worst. It is temporary, not a growing per-frame image cache.
    grid=[[None]*size for _ in range(size)]
    for a,b,c,d,col in raw:
        for y in range(max(0,int(b)),min(size,int(d))):
            grid[y][max(0,int(a)):min(size,int(c))]=[col]*max(0,min(size,int(c))-max(0,int(a)))
    result=()
    for n in (64,48,40,32,24,20,16,12,8,4,2):
        if n>=size:continue
        small=[]
        for gy in range(n):
            row=[]
            y0=gy*size//n;y1=max(y0+1,(gy+1)*size//n)
            for gx in range(n):
                x0=gx*size//n;x1=max(x0+1,(gx+1)*size//n)
                vals=[grid[y][x] for y in range(y0,y1) for x in range(x0,x1)]
                visible=[v for v in vals if v is not None]
                if len(visible)<len(vals)*.28:row.append(None)
                else:row.append(Counter(visible).most_common(1)[0][0])
            small.append(row)
        result=_merge(small,n)
        if len(result)<=budget:break
    _CACHE[key]=result;_CACHE.move_to_end(key)
    while len(_CACHE)>_CACHE_LIMIT:_CACHE.popitem(last=False)
    return result

def stamp(renderer,out,data,box,flip=False,budget=500,fit_visible=False):
    l,t,r,b=box
    if r<=l or b<=t or budget<4:return 0
    before=len(out); rows=compact(data,budget)
    if not rows:return 0
    if fit_visible:
        minx=min(a for a,c,d,e,col in rows); maxx=max(d for a,c,d,e,col in rows)
        miny=min(c for a,c,d,e,col in rows); maxy=max(e for a,c,d,e,col in rows)
        sx=max(1e-6,maxx-minx); sy=max(1e-6,maxy-miny)
        for a,c,d,e,col in rows:
            x0=(a-minx)/sx; x1=(d-minx)/sx
            if flip:x0,x1=1.-x1,1.-x0
            y0=(c-miny)/sy; y1=(e-miny)/sy
            out.append(renderer._quad(l+(x0+x1)*.5*(r-l),t+(y0+y1)*.5*(b-t),
                                      (x1-x0)*(r-l)+.025,(y1-y0)*(b-t)+.025,col))
    else:
        for a,c,d,e,col in rows:
            cx=(a+d)*.5
            if flip:cx=1.-cx
            out.append(renderer._quad(l+cx*(r-l),t+(c+e)*.5*(b-t),(d-a)*(r-l)+.025,(e-c)*(b-t)+.025,col))
    return len(out)-before

def append_body(renderer,out,c,budget=1800):
    """FIX111: fit logical boss pixels to the creature's gameplay dimensions.

    The old FIX104 path multiplied source resolution by 2.5, so a fake 96px
    export became 240 world pixels even when the boss collision body was only
    ~100-180px.  Source resolution no longer decides in-game magnification.
    """
    sp=str(getattr(c,'species',''))
    if sp not in SPECIES:return False
    assets=getattr(renderer.game,'assets',None)
    if assets is None:return False
    state=str(getattr(c,'animation_state_override','') or '')
    if not state:
        if float(getattr(c,'hurt_timer',0.))>0:state='hurt'
        else:state='move' if abs(float(getattr(c,'vx',0.)))+abs(float(getattr(c,'vy',0.)))>1. else 'idle'
    age=float(getattr(c,'visual_animation_time',getattr(c,'motion_time',0.)))
    if state=='hurt':age=max(0.,.3-float(getattr(c,'hurt_timer',0.)))
    data=assets.pixel_animation_rects('creature.'+sp,state,time_seconds=age)
    if data is None:data=assets.pixel_rects('creature.'+sp)
    if data is None:return False
    w=max(1.0,float(c.width())); h=max(1.0,float(c.height()))
    stamp(renderer,out,data,(c.x-w*.5,c.y-h,c.x+w*.5,c.y),getattr(c,'facing',1)<0,budget,fit_visible=True)
    return True

def append_combat(system,renderer,out,budget=900):
    rows=list(system.effects)+list(system.flashes)+list(getattr(system,'deaths',()))
    if not rows or budget<4:return
    # Cull offscreen effects before allocating per-effect budgets.
    camx=float(getattr(system.game,'camera_x',0.));camy=float(getattr(system.game,'camera_y',0.))
    vieww=float(getattr(system.game,'viewport_w',100000.));viewh=float(getattr(system.game,'viewport_h',100000.))
    visible=[]
    for e in rows:
        if e.get('visual',True) is False:
            continue
        if e.get('death'):
            sp=e['species'];data=system.game.assets.pixel_animation_rects('creature.'+sp,'death',time_seconds=e['age'])
            w=max(1.0,float(e.get('w',96.0)));h=max(1.0,float(e.get('h',96.0)))
            box=(e['x']-w*.5,e['y']-h,e['x']+w*.5,e['y'])
        else:
            heavy=not ('box' in e)
            attack_state='heavy_attack' if heavy else 'normal_attack'
            weapon_asset='weapon.'+str(e['weapon'])
            binding=None
            try:binding=system.game.assets.combat_binding(weapon_asset,attack_state)
            except Exception:binding=None
            effect_asset=str((binding or {}).get('effect_asset',weapon_asset) or weapon_asset)
            effect_state=str((binding or {}).get('effect_state','heavy_effect' if heavy else 'normal_effect') or ('heavy_effect' if heavy else 'normal_effect'))
            data=system.game.assets.pixel_animation_rects(effect_asset,effect_state,time_seconds=e['age'])
            if data is None:
                data=system.game.assets.pixel_animation_rects(weapon_asset,'heavy_effect' if heavy else 'normal_effect',time_seconds=e['age'])
            box=e.get('box') or system._box(e)
        l,t,r,b=box
        if r<camx-32 or l>camx+vieww+32 or b<camy-32 or t>camy+viewh+32:continue
        if data is not None:visible.append((e,data,box))
    if not visible:return
    each=max(4,budget//len(visible));q=[]
    for e,data,box in visible:
        room=min(each,budget-len(q))
        if room<4:break
        stamp(renderer,q,data,box,e.get('facing',1)<0,room,fit_visible=bool(e.get('death')))
    out.extend(q)
