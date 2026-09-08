# -*- coding: utf-8 -*-
"""FIX99 finite simulation queues for punches, elemental bolts and storm lightning.
Damage follows visible world-space geometry, terrain, roll and shield gates.
No UIKit, threads, wall-clock clocks, auto-spawns or persistent-map changes.
"""
import math
from config import TILE_SIZE as T, GRAVITY
from entities.creature import Creature
from engine.math2d import rects_overlap
from systems.plant_monsters import clear_ray


def interactable(c):
    return isinstance(c, Creature) and c.active and c.hp > 0 and not getattr(c, 'background_only', False)


def update_special_actor(system, c, dt, startup_safe=False):
    """True means this actor's locomotion has been fully handled this tick."""
    world=system.physics.world
    if getattr(c,'background_only',False):
        c.motion_time += dt
        phase=c.motion_time
        c.x=c.home_x+math.sin(phase*.65)*max(16.,c.patrol_radius*.55)
        c.y=c.home_y+math.sin(phase*1.8)*12.+math.sin(phase*.35)*8.
        c.facing=1 if math.cos(phase*.65)>=0 else -1
        c.animation_state_override='move';c.vx=c.vy=0.;c.grounded=False
        c.hp=c.max_hp;c.hurt_timer=0.;c.poisoned=False;c.stun_timer=0.
        return True
    if c.species!='corrosive_slime':return False
    p=system.player
    state=getattr(c,'_f99_drop','hanging')
    if state=='hanging':
        c.x,c.y=c.home_x,c.home_y;c.vx=c.vy=0.;c.grounded=False
        c.animation_state_override='idle'
        tx=int(c.home_x//T);ty=int((c.home_y-c.height()-1)//T)
        attached=world.solid_top_y(tx,ty) is not None
        below=(abs(p.x-c.x)<=c.width()*.5+18 and 0 < p.y-p.height()*.5-c.y < 7*T)
        if not attached or (not startup_safe and below and clear_ray(world,c.x,c.y+1,p.x,p.y-p.height()*.5)):
            c._f99_drop='warning';c._f99_drop_age=0.;c.visual_animation_time=0.
    elif state=='warning':
        c.animation_state_override='attack';c._f99_drop_age+=dt
        if c._f99_drop_age>=.3:c._f99_drop='fall';c._f99_drop_age=0.
    elif state=='fall':
        c.animation_state_override='fall';c._f99_drop_age+=dt
        # Substep collision so even a slow render frame cannot skip the player.
        c.vy=min(600.,c.vy+GRAVITY*dt)
        n=max(1,int(math.ceil(abs(c.vy*dt)/6.)))
        for _ in range(n):
            hit,grounded=system.physics.move_vertical(c,c.vy*dt/n)
            if rects_overlap(c.bbox(),p.bbox()) and not system._player_is_rolling():
                if system._player_can_take_damage():
                    system._resolve_player_damage(c.x,c.y,c.attack_damage,'corrosive_slime')
                    p.hurt_timer=.2;p.hurt_invulnerability=.45
                c.hp=0.;c._f99_drop='spent'
                if system.boss_weapon_system:
                    system.boss_weapon_system.expansion.flash('effect.corrosive_splash',c.x,c.y-10.,42.)
                break
            if hit or grounded or c._f99_drop_age>5.:
                c._f99_drop='splat';c._f99_drop_age=0.;c.vy=0.
                if system.boss_weapon_system:system.boss_weapon_system.expansion.flash('effect.corrosive_splash',c.x,c.y-5.,46.)
                break
    elif state=='splat':
        c.animation_state_override='splat';c._f99_drop_age+=dt
        if c._f99_drop_age>=.24:c.hp=0.
    return True


def agile_kong(system,c,dt,startup_safe=False):
    if c.species!='congo_kong' or c.stun_timer>0 or startup_safe or not c.player_detected:return False
    # Repositioning is separate from the actual action. Idle gaps let the boss
    # retreat, turn and hop instead of sliding through the player continuously.
    c.motion_time+=dt;p=system.player;dx=p.x-c.x
    c.facing=1 if dx>=0 else -1
    phase=int(c.motion_time/1.0)%4
    direction=-c.facing if abs(dx)<100. or (phase==1 and abs(dx)<210.) else c.facing
    wanted=direction*c.speed*(.78 if direction==-c.facing else 1.)
    c.vx=system._approach(c.vx,wanted,c.speed*5.*dt)
    if c.grounded and int(c.motion_time/1.8)!=getattr(c,'_f99_hop_slot',-1):
        c._f99_hop_slot=int(c.motion_time/1.8);c.vy=-230.;c.grounded=False
    system.physics.move_horizontal(c,c.vx*dt)
    c.vy=min(700.,c.vy+GRAVITY*dt)
    hit,grounded=system.physics.move_vertical(c,c.vy*dt)
    if hit:c.vy=0.
    c.grounded=bool(grounded);c.animation_state_override='move';c.behavior_state='reposition'
    return True


class ExpansionCombat:
    MAX_ORBS=64;MAX_PUNCHES=16;MAX_STORMS=2;MAX_FLASHES=24;MAX_BOLTS=12
    def __init__(self,game):
        self.game=game;self.punches=[];self.orbs=[];self.storms=[];self.flashes=[];self.lightning=[]
        self.event_log=[];self.serial=0

    def emit(self,kind,**data):
        self.event_log.append(dict(kind=kind,**data));del self.event_log[:-64]
        self.game.events.emit('fauna99_'+kind,**data)

    def busy(self,weapon):
        return any(x['owner'] is self.game.player and x['weapon']==weapon for x in self.punches+self.storms)

    def can_start(self,weapon):
        if self.busy(weapon):return False
        if weapon=='kong_gauntlets':return len(self.punches)<self.MAX_PUNCHES
        if weapon=='triple_staff':return len(self.storms)<self.MAX_STORMS and len(self.orbs)+3<=self.MAX_ORBS
        return True

    def _targets(self):
        return [c for c in self.game.scene.entities if interactable(c)]

    def flash(self,asset,x,y,span=40.,life=.25,animation='idle'):
        self.flashes.append(dict(asset=asset,x=float(x),y=float(y),span=float(span),age=0.,life=life,animation=animation))
        del self.flashes[:-self.MAX_FLASHES]

    def punch(self,owner,heavy=False,damage=20.,weapon='kong_gauntlets'):
        if len(self.punches)>=self.MAX_PUNCHES:return False
        self.punches.append(dict(owner=owner,heavy=bool(heavy),weapon=weapon,age=0.,next=0,
            facing=1 if owner.facing>=0 else -1,damage=float(damage),life=3. if heavy else .45,
            hits=15 if heavy else 2,interval=.2,visual_age=99.,visual_index=0))
        return True

    def fire(self,weapon,heavy):
        p=self.game.player
        if weapon=='kong_gauntlets':return self.punch(p,heavy,22. if heavy else 40.)
        if weapon=='triple_staff':
            f=1 if p.facing>=0 else -1;sx=p.x+f*18.;sy=p.y-p.height()*.6
            self.flash('weapon.triple_staff',sx,sy,52.,.32,'heavy_effect' if heavy else 'normal_effect')
            if heavy:
                # The cloud footprint is captured on cast, and never dragged by
                # subsequent movement. All 3 balls visibly converge at its centre.
                cx=p.x+f*3*T;cy=max(T,sy-4*T)
                # Do not conjure a cloud THROUGH a ceiling. Raise it only as far
                # as free space permits, keeping underground casts functional.
                while cy<sy-20 and not clear_ray(self.game.world,cx,sy,cx,cy):cy+=T*.25
                storm=dict(owner=p,weapon=weapon,age=0.,life=4.2,facing=f,
                    start_x=p.x+f*.5*T,cx=cx,cy=cy,base_y=p.y,strike=0,charge=.7,active_seconds=3.)
                self.storms.append(storm)
                for i,element in enumerate(('water','fire','ice')):
                    self._orb(p,element,sx,sy,0.,0.,damage=32.,target=(cx+(i-1)*16.*f,cy),charge=.7,arc_x=(i-1)*28.*f)
                self.emit('storm_cast',x=cx,y=cy,width=5*T)
            else:
                # No ordinary attack was specified: a modest three-element
                # volley is provided; it does not call a storm or spend uses.
                ux,uy=self.game.melee._aim_direction()
                for angle,element in zip((-.12,0.,.12),('water','fire','ice')):
                    x=ux*math.cos(angle)-uy*math.sin(angle);y=ux*math.sin(angle)+uy*math.cos(angle)
                    self._orb(p,element,sx,sy,x*290.,y*290.,damage=32.)
            return True
        return False

    def creature_action(self,c,action):
        effect=action.get('effect')
        if effect=='kong_combo':
            return self.punch(c,c.ai_action_id=='barrage',action.get('damage',20.))
        if effect=='triple_breath':
            f=1 if c.facing>=0 else -1
            # Mouth positions match the three differently coloured authored heads.
            for i,element in enumerate(('water','fire','electric')):
                sx=c.x+f*((48,67,85)[i]+8-48)*2.5
                sy=c.y-((96-((44,28,16)[i]+6))*2.5)
                tx,ty=c.ai_action_target_x,c.ai_action_target_y
                ang=math.atan2(ty-sy,tx-sx)
                for spread in (-.23,0.,.23):
                    self._orb(c,element,sx,sy,math.cos(ang+spread)*300.,math.sin(ang+spread)*300.,action.get('damage',22.),life=3.5)
                self.flash('effect.tri_orb_'+element,sx,sy,36.,.2)
            self.emit('triple_breath',owner=c.entity_id,count=9)
            return True
        return False

    def _orb(self,owner,element,x,y,vx,vy,damage=20.,target=None,charge=0.,life=3.,arc_x=0.):
        if len(self.orbs)>=self.MAX_ORBS:return False
        self.serial+=1
        self.orbs.append(dict(id=self.serial,owner=owner,element=element,x=float(x),y=float(y),sx=float(x),sy=float(y),
            vx=vx,vy=vy,damage=damage,age=0.,life=charge if target else life,target=target,charge=charge,arc_x=float(arc_x),radius=9.))
        return True

    def _hurt_player(self,source,x,y,damage,kind):
        cs=self.game.creature_system;p=self.game.player
        if not source.active or source.hp<=0 or not cs._can_attack_player(source) or not cs._player_can_take_damage():return False
        result=cs._resolve_player_damage(x,y,damage,kind)
        p.hurt_timer=.2;p.hurt_invulnerability=.42
        return not bool(result.get('blocked'))

    def _punch_box(self,row):
        c=row['owner'];f=row['facing']
        start=c.x+f*max(12.,c.width()*.38)
        left,right=sorted((start,start+f*2*T))
        return (left,c.y-3*T,right,c.y)

    def _hit_punch(self,row):
        c=row['owner'];box=self._punch_box(row)
        sx,sy=c.x,c.y-min(c.height()*.55,2*T)
        targets=self._targets() if c is self.game.player else [self.game.player]
        n=0
        for t in targets:
            if t is c:continue
            b=t.combat_bbox() if hasattr(t,'combat_bbox') else t.bbox()
            if not rects_overlap(box,b):continue
            tx=max(b[0],min(b[2],(box[0]+box[2])*.5));ty=max(b[1],min(b[3],sy))
            if not clear_ray(self.game.world,sx,sy,tx,ty):continue
            if c is self.game.player:
                if self.game.boss_weapons._hurt(t,row['damage'],row['facing'],18.):n+=1
            elif self._hurt_player(c,sx,sy,row['damage'],'kong_punch'):n+=1
        row['visual_age']=0.;row['visual_index']=row['next']
        self.emit('punch',owner=getattr(c,'entity_id','player'),index=row['next']+1,heavy=row['heavy'],box=box,hits=n)

    def _clip_column(self,x,y,max_y):
        """Stop at the first floor/roof, including actual layered tile surfaces."""
        world=self.game.world;end=max_y
        for _,_,_,box in world.solid_cells_in_rect((x-7.,y,x+7.,max_y),padding=0):
            if box[3]>y and box[1]<end:end=max(y,min(end,box[1]))
        return end

    def _strike(self,s,index):
        # Every wave visits all five tiles; deterministic order avoids random
        # unlucky holes and stays strictly inside the five-tile footprint.
        slot=(index*3+(index//5))%5
        x=s['start_x']+s['facing']*(slot+.5)*T
        y=s['cy']+8.;bottom=self._clip_column(x,y,s['base_y']+2*T)
        if bottom<=y+1:return
        box=(x-7,y,x+7,bottom)
        self.lightning.append(dict(x=x,y=y,bottom=bottom,age=0.,life=.16,index=index))
        del self.lightning[:-self.MAX_BOLTS]
        for c in self._targets():
            if rects_overlap(box,c.bbox()):self.game.magic._damage_creature_with_magic(c,'electric',1,0.,300.)
        self.flash('effect.triple_storm_spark',x,bottom,26.,.2)
        self.emit('lightning',x=x,width=14.,top=y,bottom=bottom,index=index)

    def _orb_collision(self,o,oldx,oldy,newx,newy,targets):
        length=math.hypot(newx-oldx,newy-oldy);n=max(1,int(math.ceil(length/6.)))
        world=self.game.world;p=self.game.player
        for j in range(1,n+1):
            x=oldx+(newx-oldx)*j/n;y=oldy+(newy-oldy)*j/n;r=o['radius']
            box=(x-r,y-r,x+r,y+r)
            if not (0<x<world.width_tiles*T and 0<y<world.height_tiles*T):return True
            wall=False
            for _,_,_,b in world.solid_cells_in_rect(box,padding=0):
                if rects_overlap(box,b):wall=True;break
            if wall:
                self.flash('effect.tri_orb_'+o['element'],x,y,28.,.18);return True
            ts=targets if o['owner'] is p else [p]
            for t in ts:
                if not rects_overlap(box,t.bbox()):continue
                if t is p:
                    if self.game.creature_system._player_is_rolling():continue
                    self._hurt_player(o['owner'],oldx,oldy,o['damage'],'dragon_'+o['element'])
                else:self.game.magic._damage_creature_with_magic(t,o['element'],1,o['vx'],o['vy'])
                self.flash('effect.tri_orb_'+o['element'],x,y,34.,.22)
                return True
        return False

    def update(self,dt):
        dt=max(0.,min(.25,float(dt)))
        if not dt:return
        targets=self._targets()
        keep=[]
        for r in self.punches:
            c=r['owner']
            if not getattr(c,"active",True) or c.hp<=0:continue
            old=r['age'];r['age']+=dt;r['visual_age']+=dt
            # Exact 2 normal / 15 heavy opportunities independent of frame rate.
            while r['next']<r['hits'] and r['next']*r['interval']<min(r['age']+1e-9,r['life']):
                self._hit_punch(r);r['next']+=1
            if r['age']<r['life']:keep.append(r)
        self.punches=keep
        keep=[]
        for s in self.storms:
            if self.game.player.hp<=0:continue
            s['age']+=dt
            while s['strike']<15 and s['age']>=s['charge']+.2+s['strike']*.2:
                self._strike(s,s['strike']);s['strike']+=1
            if s['age']<s['life']:keep.append(s)
        self.storms=keep
        keep=[]
        for o in self.orbs:
            o['age']+=dt
            if o['target']:
                t=min(1.,o['age']/max(.001,o['charge']));tx,ty=o['target']
                o['x']=o['sx']+(tx-o['sx'])*t+math.sin(t*math.pi)*o.get('arc_x',0.);o['y']=o['sy']+(ty-o['sy'])*t-math.sin(t*math.pi)*18.
                if o['age']<o['life']:keep.append(o)
                continue
            if o['age']>o['life']:continue
            if o['owner'] is not self.game.player and (not o['owner'].active or o['owner'].hp<=0):continue
            x,y=o['x'],o['y'];nx=x+o['vx']*dt;ny=y+o['vy']*dt
            if not self._orb_collision(o,x,y,nx,ny,targets):o['x']=nx;o['y']=ny;keep.append(o)
        self.orbs=keep[-self.MAX_ORBS:]
        for attr in ('flashes','lightning'):
            rows=getattr(self,attr)
            for r in rows:r['age']+=dt
            setattr(self,attr,[r for r in rows if r['age']<r['life']])

    def _asset(self,renderer,out,aid,box,age,animation='idle',flip=False,budget=96):
        # Never truncate an authored sprite by row: that hid lower fists and
        # the bottom of lightning. Draw complete sprites; the global fallback
        # below switches ALL positions to cheap complete symbols under load.
        if len(out)>900:return
        reg=self.game.assets
        data=reg.pixel_animation_rects(aid,animation,time_seconds=age) or reg.pixel_rects(aid)
        if not data:return
        n=float(data['size']);left,top,right,bottom=box
        for x0,y0,x1,y1,col in data.get('rects',())[:513]:
            x=(x0+x1)*.5/n
            if flip:x=1.-x
            out.append(renderer._quad(left+x*(right-left),top+(y0+y1)*.5/n*(bottom-top),
                (x1-x0)/n*(right-left)+.03,(y1-y0)/n*(bottom-top)+.03,col))

    def append_visuals(self,renderer,out,budget=640):
        q=[]
        for s in self.storms:
            if s['age']<s['charge']*.5:continue
            self._asset(renderer,q,'effect.triple_storm_cloud',(s['cx']-2.5*T,s['cy']-24,s['cx']+2.5*T,s['cy']+16),s['age'],budget=64)
        for b in self.lightning:
            self._asset(renderer,q,'effect.triple_storm_lightning',(b['x']-7,b['y'],b['x']+7,b['bottom']),b['age'],budget=40)
        for r in self.punches:
            if r['visual_age']>.18:continue
            box=self._punch_box(r)
            # Display exactly the same authored VFX edited in the weapon editor.
            self._asset(renderer,q,'weapon.kong_gauntlets',box,r['age'],
                'heavy_effect' if r['heavy'] else 'normal_effect',flip=r['facing']<0,budget=110)
        for o in self.orbs:
            r=o['radius']*1.5
            self._asset(renderer,q,'effect.tri_orb_'+o['element'],(o['x']-r,o['y']-r,o['x']+r,o['y']+r),o['age'],budget=35)
        for f in self.flashes:
            r=f['span']*.5
            self._asset(renderer,q,f['asset'],(f['x']-r,f['y']-r,f['x']+r,f['y']+r),f['age'],f.get('animation','idle'),budget=40)
        # Never lose lightning/punch positions to decorative art under pressure.
        if len(q)>budget:
            q=[]
            def quad(x,y,w,h,col):q.append(renderer._quad(x,y,w,h,col))
            for s in self.storms:
                if s['age']>=s['charge']*.5:
                    quad(s['cx'],s['cy'],5*T,32.,(.22,.28,.38,.95))
                    quad(s['cx']-T*.5,s['cy']-10.,3*T,16.,(.38,.43,.51,1.))
            for b in self.lightning:
                h=b['bottom']-b['y'];cy=(b['bottom']+b['y'])*.5
                quad(b['x'],cy,14.,h,(.62,.65,.96,.55));quad(b['x'],cy,4.,h,(1.,.98,.75,1.))
            for r in self.punches:
                if r['visual_age']>.18:continue
                a,b,c,d=self._punch_box(r);f=r['facing']
                for i in range(3 if r['heavy'] else 1):
                    quad((a+c)*.5,(b+d)*.5+(i-1)*28,30,22,(.84,.61,.35,.9))
                    quad((a+c)*.5+f*8,(b+d)*.5+(i-1)*28-4,15,7,(1.,.9,.65,1.))
            colors={'water':(.25,.65,.95,1.),'fire':(1.,.4,.1,1.),'ice':(.58,.9,.94,1.),'electric':(.96,.84,.35,1.)}
            for o in self.orbs:
                quad(o['x'],o['y'],18.,18.,colors[o['element']]);quad(o['x']-2,o['y']-2,6.,6.,(1.,1.,.9,1.))
        out.extend(q[:budget])

    def reserve(self):
        if not (self.punches or self.orbs or self.storms or self.flashes or self.lightning):return 0
        return min(640,len(self.punches)*256+len(self.orbs)*80+len(self.storms)*128+len(self.lightning)*128+len(self.flashes)*128)


def background_fauna_quads(renderer,game,camera_x,camera_y,budget=64):
    """Noninteractive butterflies live in the PRE-terrain background pass.
    They use only spare background slots: existing cave back walls are never
    displaced. Under load a complete four-quad butterfly replaces detail.
    """
    budget=max(0,int(budget));out=[]
    if budget<4:return out
    actors=[c for c in getattr(game,'creatures',()) if getattr(c,'background_only',False)
            and c.active and camera_x-40<=c.x<=camera_x+game.viewport_w+40
            and camera_y-40<=c.y<=camera_y+game.viewport_h+40]
    if not actors:return out
    allowance=max(4,budget//len(actors))
    for c in actors:
        left=budget-len(out)
        if left<4:break
        data=game.assets.pixel_animation_rects(c.asset_id,'move',time_seconds=c.visual_animation_time)
        rects=data.get('rects',()) if data else ()
        if data and len(rects)<=min(left,allowance):
            n=float(data['size'])
            for x0,y0,x1,y1,color in rects:
                xx=(x0+x1)*.5-n*.5
                if c.facing<0:xx=-xx
                out.append(renderer._quad(c.x+xx*2.5,c.y-((n-(y0+y1)*.5)*2.5),
                           (x1-x0)*2.5+.03,(y1-y0)*2.5+.03,color))
        else:
            wing=5.+3.*abs(math.sin(c.visual_animation_time*12.));cy=c.y-18.
            for dx,dy,w,h,col in ((-wing*.5,0,wing,13,(.88,.59,.28,1.)),
                                 (wing*.5,0,wing,13,(.58,.4,.68,1.)),
                                 (0,2,2.5,15,(.2,.23,.24,1.)),(0,-6,3,3,(.92,.81,.56,1.))):
                out.append(renderer._quad(c.x+dx,cy+dy,w,h,col))
    return out
