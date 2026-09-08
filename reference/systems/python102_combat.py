# -*- coding: utf-8 -*-
"""FIX102 finite world-space whip and growing travelling cyclone.
All age/collision clocks are simulation dt: study, password lock and menus
pause these with the existing BossWeaponSystem. No timers/UI/background jobs.
"""
import math
from config import TILE_SIZE as T
from engine.math2d import rects_overlap
from systems.plant_monsters import clear_ray
from systems.python102_defs import WEAPONS

class PythonBossCombat:
    MAX_CYCLONES=4
    MAX_WHIP_FLASHES=6
    MAX_DEATHS=1
    def __init__(self,game):
        self.game=game;self.cyclones=[];self.whips=[];self.deaths=[]
        self.events=[]

    def log(self,kind,**kw):
        self.events.append(dict(kind=kind,**kw));del self.events[:-48]

    def can_start(self,heavy):
        if not heavy:return True
        p=self.game.player;f=1 if p.facing>=0 else -1
        return (len(self.cyclones)<self.MAX_CYCLONES and
                not any(r['owner'] is p for r in self.cyclones) and
                self._path_free(p.x+f*.51*T,p.x+f*T,p.y-.1))

    def _targets(self,owner):
        if owner is self.game.player:
            return list(self.game.boss_weapons._entities())
        return [self.game.player]

    def _hit(self,owner,target,damage,direction,source_x,source_y,kind):
        if owner is self.game.player:
            return self.game.boss_weapons._hurt(target,damage,direction,32. if kind=='python_cyclone' else 90.)
        cs=self.game.creature_system;p=self.game.player
        if not owner.active or owner.hp<=0 or not cs._can_attack_player(owner) or not cs._player_can_take_damage():return False
        result=cs._resolve_player_damage(source_x,source_y,damage,kind)
        p.hurt_timer=.2;p.hurt_invulnerability=.45
        if not result.get('blocked'):
            p.vx=direction*(60. if kind=='python_cyclone' else 140.)
        return not bool(result.get('blocked'))

    def _solid_boxes(self,box):
        return [b for _,_,_,b in self.game.world.solid_cells_in_rect(box,padding=0)]

    def whip_box(self,owner):
        f=1 if owner.facing>=0 else -1
        sx=owner.x+f*owner.width()*.5
        sy=owner.y-min(owner.height()*.55,1.05*T)
        ex=sx+f*3*T
        # The visible tail stops at the same wall as the hitbox, not through it.
        box=(min(sx,ex),sy-.48*T,max(sx,ex),sy+.48*T)
        for b in self._solid_boxes(box):
            if b[3]<=box[1] or b[1]>=box[3]:continue
            edge=b[0] if f>0 else b[2]
            if 0<=f*(edge-sx)<f*(ex-sx):ex=edge
        return (min(sx,ex),box[1],max(sx,ex),box[3]),sx,sy,f

    def whip(self,owner,damage):
        box,sx,sy,f=self.whip_box(owner);hits=0
        for c in self._targets(owner):
            if c is owner:continue
            b=c.combat_bbox() if hasattr(c,'combat_bbox') else c.bbox()
            if not rects_overlap(box,b):continue
            tx=max(b[0],min(b[2],(box[0]+box[2])*.5));ty=max(b[1],min(b[3],sy))
            if not clear_ray(self.game.world,sx,sy,tx,ty):continue
            ok=self._hit(owner,c,damage,f,sx,sy,'python_whip');hits+=int(ok)
            if ok:
                self.game.boss_weapons.expansion.flash('effect.python_whip_hit',tx,ty,28.,.25)
        self.whips.append(dict(box=box,age=0.,life=.35,facing=f,owner=owner))
        del self.whips[:-self.MAX_WHIP_FLASHES]
        self.log('whip',box=box,hits=hits,player=owner is self.game.player)
        return True

    def _box(self,r,age):
        a=min(1.,max(0.,age/r['life']))
        x=r['sx']+r['facing']*4*T*a
        h=T*(2+3*a)
        bottom=r['bottom'];left=x-.5*T;right=x+.5*T
        # Ceiling clipping: do not display/damage through a solid roof. Floors
        # only touch bottom and are ignored. A horizontal wall cancels travel.
        top=bottom-h
        for b in self._solid_boxes((left,top,right,bottom-.1)):
            if b[0]<right and b[2]>left and top<b[3]<bottom-.2:
                top=max(top,b[3])
        return (left,top,right,bottom)

    def _path_free(self,x1,x2,bottom):
        # Capsule travel near the base (including custom/layered collision).
        half=.5*T
        box=(min(x1,x2)-half,bottom-T*.35,max(x1,x2)+half,bottom-.2)
        return not any(rects_overlap(box,b) for b in self._solid_boxes(box))

    def cyclone(self,owner,damage):
        if len(self.cyclones)>=self.MAX_CYCLONES:return False
        f=1 if owner.facing>=0 else -1
        # Player starts exactly one tile forward; a long boss's tail release
        # uses its front edge so the cyclone does not start inside its body.
        ox=owner.x if owner is self.game.player else owner.x+f*owner.width()*.5
        sx=ox+f*T;bottom=owner.y-.1
        if not self._path_free(ox+f*.51*T,sx,bottom):
            self.log('blocked_cast',player=owner is self.game.player);return False
        r=dict(owner=owner,age=0.,life=3.,sx=sx,bottom=bottom,facing=f,
               damage=float(damage),next_tick=0.,box=None)
        r['box']=self._box(r,0.);self.cyclones.append(r)
        self.log('cyclone',x=sx,bottom=bottom,player=owner is self.game.player)
        return True

    def fire(self,heavy):
        row=WEAPONS['python_whip'];p=self.game.player
        return self.cyclone(p,24.) if heavy else self.whip(p,row['damage'])

    def creature_action(self,c,action):
        if action.get('effect')=='python_tail':return self.whip(c,float(action.get('damage',26.)))
        if action.get('effect')=='python_tornado':return self.cyclone(c,float(action.get('damage',12.)))
        return False

    def death(self,c):
        # Render an authored collapse after the normal one-time death/drop path.
        self.deaths.append(dict(x=c.x,y=c.y,facing=c.facing,age=0.,life=.9))
        del self.deaths[:-self.MAX_DEATHS]

    def _pulse(self,r,age):
        box=self._box(r,age);sx=(box[0]+box[2])*.5;sy=box[3]-T*.35;hits=0
        if box[3]-box[1]<1:return
        for c in self._targets(r['owner']):
            if c is r['owner']:continue
            b=c.combat_bbox() if hasattr(c,'combat_bbox') else c.bbox()
            if not rects_overlap(box,b):continue
            tx=max(b[0],min(b[2],sx));ty=max(b[1],min(b[3],(box[1]+box[3])*.5))
            if clear_ray(self.game.world,sx,sy,tx,ty):
                ok=self._hit(r['owner'],c,r['damage'],r['facing'],sx,sy,'python_cyclone');hits+=int(ok)
                if ok and hits<=2:
                    self.game.boss_weapons.expansion.flash('effect.python_whip_hit',tx,ty,20.,.18)
        self.log('pulse',age=round(age,4),box=box,hits=hits)

    def update(self,dt):
        dt=max(0.,min(.25,float(dt)))
        if not dt:return
        keep=[]
        for r in self.cyclones:
            owner=r['owner']
            if not getattr(owner,'active',True) or owner.hp<=0:continue
            old=r['age'];new=min(r['life'],old+dt)
            oldx=r['sx']+r['facing']*4*T*old/r['life']
            # Process chronological pulse intervals and movement segments so
            # neither walls nor victims can be skipped at a low frame rate.
            cancelled=False;cursor=oldx
            while r['next_tick']<r['life']-1e-7 and r['next_tick']<=new+1e-8:
                age=r['next_tick'];x=r['sx']+r['facing']*4*T*age/r['life']
                if not self._path_free(cursor,x,r['bottom']):cancelled=True;break
                self._pulse(r,age);cursor=x;r['next_tick']+=.25
            nx=r['sx']+r['facing']*4*T*new/r['life']
            if cancelled or not self._path_free(cursor,nx,r['bottom']):
                self.log('wall_stop',x=nx);continue
            r['age']=new;r['box']=self._box(r,new)
            if new<r['life']-1e-8:keep.append(r)
            else:self.log('finished',x=nx,height=5*T)
        self.cyclones=keep
        for key in ('whips','deaths'):
            rows=getattr(self,key)
            for r in rows:r['age']+=dt
            setattr(self,key,[r for r in rows if r['age']<r['life']])

    def reserve(self):
        return min(640,len(self.cyclones)*128+len(self.whips)*96+len(self.deaths)*420)

    def _stamp(self,renderer,out,aid,state,age,box,flip,budget):
        data=self.game.assets.pixel_animation_rects(aid,state,time_seconds=age)
        if not data:return False
        rects=data.get('rects',());n=float(data.get('size',32))
        if not rects:return False
        # Do not truncate an intricate edited effect mid-column: caller uses
        # a complete low-detail silhouette if the authored budget is exceeded.
        if len(rects)>budget:return False
        l,t,r,b=box
        for x0,y0,x1,y1,col in rects:
            x=(x0+x1)*.5/n
            if flip:x=1.-x
            out.append(renderer._quad(l+x*(r-l),t+(y0+y1)*.5/n*(b-t),
                 (x1-x0)/n*(r-l)+.03,(y1-y0)/n*(b-t)+.03,col))
        return True

    def append_visuals(self,renderer,out,budget=640):
        allrows=len(self.cyclones)+len(self.whips)+len(self.deaths)
        if not allrows or budget<8:return
        allowance=max(8,budget//allrows);q=[]
        for r in self.cyclones:
            box=r['box'];room=min(128,allowance,budget-len(q))
            if room<8:break
            if self._stamp(renderer,q,'weapon.python_whip','heavy_effect',r['age'],box,r['facing']<0,room):continue
            l,t,rr,b=box;h=b-t;cx=(l+rr)*.5
            # Pixel-step column with visible spiral bands. This never uses
            # another texture allocation or removes unrelated terrain quads.
            bands=min(10,max(2,room//3));col=(.48,.72,.59,.75)
            for i in range(bands):
                u=(i+.5)/bands;cy=b-u*h;w=(rr-l)*(.45+.55*u)
                shift=math.sin(r['age']*15+u*14)*w*.12
                q.append(renderer._quad(cx+shift,cy,w,h/bands+.5,col))
                q.append(renderer._quad(cx+shift+w*.1,cy-h/bands*.2,w*.7,max(2,h/bands*.23),(.82,.92,.7,.96)))
        for r in self.whips:
            room=min(96,allowance,budget-len(q))
            if room<4:break
            if self._stamp(renderer,q,'weapon.python_whip','normal_effect',r['age'],r['box'],r['facing']<0,room):continue
            l,t,rr,b=r['box'];cy=(t+b)*.5
            for i in range(6):
                q.append(renderer._quad(l+(rr-l)*(i+.5)/6,cy+math.sin(i*.7+r['age']*10)*7,(rr-l)/6+1,4.,(.76,.82,.46,1.)))
        for r in self.deaths:
            room=min(420,allowance,budget-len(q))
            if room<8:break
            box=(r['x']-120.,r['y']-240.,r['x']+120.,r['y'])
            if not self._stamp(renderer,q,'creature.giant_python','death',r['age'],box,r['facing']<0,room):
                for i in range(6):
                    q.append(renderer._quad(r['x']-80+i*30,r['y']-13,38,18,(.24,.32,.19,1-r['age']/r['life'])))
        out.extend(q[:budget])
