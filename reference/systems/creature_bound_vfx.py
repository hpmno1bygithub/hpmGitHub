# -*- coding: utf-8 -*-
"""FIX121 shared authored Creature/Boss VFX track.

FIX112 separated body animation and attack VFX. FIX120 adds two rules required
for large native-pixel boss effects:

* ``pixel_density_mode=match_owner`` computes the effect world cell size from
  the boss body's *visible authored pixels*, so one FX source pixel is the same
  visual grain as one boss source pixel instead of stretching a 128px effect
  over an arbitrary 7-10 tile combat box.
* optional ``segments`` split one large attack into delayed, independently
  positioned authored frames. Large range therefore comes from several native
  stamps, not from enlarging one low-density bitmap.

FIX129 additionally supports ``world_motion_mode=distance``: one authored FX
travels continuously over a signed tile distance during a bounded duration.
``motion_repeat=loop`` repeats only the visual frame clip while travelling; the
world path still completes once, so authoring can never create an infinite hit.

Combat/damage remains authoritative in the existing combat systems. This module
only schedules visual rows and never changes HP, hit boxes or physics.
"""
from config import TILE_SIZE


class CreatureBoundVFXSystem:
    MAX=48
    def __init__(self,game,asset_registry):
        self.game=game; self.assets=asset_registry; self.rows=[]

    def _life(self,asset,state):
        try:
            info=self.assets.animation_info(asset,state)
            if info:return max(.05,float(info.get('frame_count',1))/max(1.,float(info.get('fps',8.))))
        except Exception:pass
        return .5

    def _animation_info(self,asset,state):
        try:
            info=self.assets.animation_info(asset,state)
            return info if isinstance(info,dict) else {}
        except Exception:return {}

    def _visible_extent(self,asset,state,frame_index=0):
        """Return authored visible width/height in source pixels."""
        try:data=self.assets.pixel_animation_rects(asset,state,frame_index=int(frame_index))
        except Exception:data=None
        if not isinstance(data,dict):return 1.0,1.0
        rects=tuple(data.get('rects',()) or ())
        if not rects:
            size=max(1.0,float(data.get('size',1) or 1));return size,size
        try:
            minx=min(float(r[0]) for r in rects);maxx=max(float(r[2]) for r in rects)
            miny=min(float(r[1]) for r in rects);maxy=max(float(r[3]) for r in rects)
            return max(1.0,maxx-minx),max(1.0,maxy-miny)
        except Exception:
            size=max(1.0,float(data.get('size',1) or 1));return size,size

    def _matched_effect_world_size(self,c,owner_asset,owner_state,effect_asset,effect_state,scale=1.0):
        """Match FX source-pixel grain to the visible boss body's source grain."""
        owner_info=self._animation_info(owner_asset,owner_state)
        try:owner_frame=int(owner_info.get('action_frame') if owner_info.get('action_frame') is not None else 0)
        except Exception:owner_frame=0
        ovw,ovh=self._visible_extent(owner_asset,owner_state,owner_frame)
        try:grain_x=max(.35,min(6.0,float(c.width())/max(1.0,ovw)))
        except Exception:grain_x=1.0
        try:grain_y=max(.35,min(6.0,float(c.height())/max(1.0,ovh)))
        except Exception:grain_y=grain_x
        einfo=self._animation_info(effect_asset,effect_state)
        try:canvas=max(1.0,float(einfo.get('canvas_size',96) or 96))
        except Exception:canvas=96.0
        try:s=max(.25,min(2.5,float(scale or 1.0)))
        except Exception:s=1.0
        return canvas*grain_x*s,canvas*grain_y*s

    @staticmethod
    def _segment_center(c,anchor,f,dx_tiles,dy_tiles,w,h,ox,oy,target_x,target_y):
        dx=float(dx_tiles or 0.0)*TILE_SIZE;dy=float(dy_tiles or 0.0)*TILE_SIZE
        if anchor=='target':
            return target_x+f*dx+ox,target_y+dy+oy
        if anchor in ('ground','ground_forward','action_box'):
            # Segment x is a centre offset from the owner; y is relative to the
            # ground baseline, with negative dy moving upward.
            return float(c.x)+f*dx+ox,float(c.y)+dy+oy-h*.5
        return float(c.x)+f*dx+ox,float(c.y)-float(c.height())*.5+dy+oy

    def emit(self,c,action,state):
        if self.assets is None:return False
        aid=str(getattr(c,'asset_id','') or ('creature.'+str(getattr(c,'species',''))))
        try:binding=self.assets.combat_binding(aid,state)
        except Exception:binding=None
        if not binding or str(binding.get('render_mode','authored'))!='authored':return False
        effect_asset=str(binding.get('effect_asset','') or '')
        effect_state=str(binding.get('effect_state','effect') or 'effect')
        if not effect_asset:return False
        f=1 if int(getattr(c,'facing',1))>=0 else -1
        try:radius=max(8.,float(action.get('hit_radius',max(c.width(),c.height())*.55)))
        except Exception:radius=max(float(c.width()),float(c.height()))*.55
        ww=float(binding.get('world_width_tiles',0.) or 0.)*TILE_SIZE
        hh=float(binding.get('world_height_tiles',0.) or 0.)*TILE_SIZE
        if ww<=0.:ww=max(float(c.width()),radius*2.)
        if hh<=0.:hh=max(float(c.height())*.65,radius*1.35)
        forward=float(binding.get('forward_tiles',0.) or 0.)*TILE_SIZE
        anchor=str(binding.get('anchor','actor') or 'actor')
        ox=float(binding.get('offset_x',0.) or 0.);oy=float(binding.get('offset_y',0.) or 0.)
        target_x=float(getattr(c,'ai_action_target_x',getattr(c,'x',0.)))
        target_y=float(getattr(c,'ai_action_target_y',getattr(c,'y',0.)-float(c.height())*.5))
        full_life=self._life(effect_asset,effect_state)
        einfo=self._animation_info(effect_asset,effect_state)
        frame_count=max(1,int(einfo.get('frame_count',1) or 1));fps=max(1.0,float(einfo.get('fps',8.0) or 8.0))
        density_mode=str(binding.get('pixel_density_mode','') or '').lower()
        if density_mode=='match_owner':
            ww,hh=self._matched_effect_world_size(c,aid,state,effect_asset,effect_state,binding.get('pixel_density_scale',1.0))
        else:
            try:s=max(.25,min(2.5,float(binding.get('scale',1.0) or 1.0)))
            except Exception:s=1.0
            ww*=s;hh*=s

        motion_mode=str(binding.get('world_motion_mode','') or '').lower()
        segments=binding.get('segments',())
        if motion_mode not in ('stationary','segments','distance'):
            motion_mode='segments' if isinstance(segments,list) and segments else 'stationary'
        if motion_mode=='distance':
            try:start_delay=max(0.0,min(4.0,float(binding.get('travel_start_delay',0.0) or 0.0)))
            except Exception:start_delay=0.0
            try:start_dx=max(-16.0,min(16.0,float(binding.get('travel_start_x_tiles',0.0) or 0.0)))
            except Exception:start_dx=0.0
            try:start_dy=max(-16.0,min(16.0,float(binding.get('travel_start_y_tiles',0.0) or 0.0)))
            except Exception:start_dy=0.0
            try:travel_dx=max(-32.0,min(32.0,float(binding.get('travel_distance_tiles',4.0) or 0.0)))
            except Exception:travel_dx=4.0
            try:travel_dy=max(-16.0,min(16.0,float(binding.get('travel_y_tiles',0.0) or 0.0)))
            except Exception:travel_dy=0.0
            try:travel_duration=max(.05,min(8.0,float(binding.get('travel_duration',full_life) or full_life)))
            except Exception:travel_duration=max(.05,full_life)
            repeat=str(binding.get('motion_repeat','once') or 'once').lower()
            if repeat not in ('once','loop'):repeat='once'
            sx,sy=self._segment_center(c,anchor,f,start_dx,start_dy,ww,hh,ox,oy,target_x,target_y)
            ex,ey=self._segment_center(c,anchor,f,start_dx+travel_dx,start_dy+travel_dy,ww,hh,ox,oy,target_x,target_y)
            self.rows.append(dict(
                asset=effect_asset,state=effect_state,age=0.,delay=start_delay,life=travel_duration,
                x=sx,y=sy,w=ww,h=hh,facing=f,owner=c,frame_index=None,
                frame_start=0,frame_span=frame_count,fps=fps,frame_count=frame_count,
                follow=False,anchor=anchor,forward=0.,ox=ox,oy=oy,segmented=False,
                distance_motion=True,start_x=sx,start_y=sy,end_x=ex,end_y=ey,
                travel_duration=travel_duration,motion_repeat=repeat,
            ))
            self.rows=self.rows[-self.MAX:]
            return True

        if motion_mode=='segments' and isinstance(segments,list) and segments:
            added=0
            playback=str(binding.get('segment_playback','phase') or 'phase').lower()
            for seg in segments[:16]:
                if not isinstance(seg,dict):continue
                try:delay=max(0.0,min(4.0,float(seg.get('delay',0.0) or 0.0)))
                except Exception:delay=0.0
                try:seg_scale=max(.25,min(2.0,float(seg.get('scale',1.0) or 1.0)))
                except Exception:seg_scale=1.0
                sw=ww*seg_scale;sh=hh*seg_scale
                frame_index=None; frame_start=0; frame_span=0
                if playback in ('clip','full_clip','animate'):
                    # FIX121: each delayed world segment plays a small authored
                    # flipbook.  FIX120 pinned every segment to one frame for
                    # ~0.16 s, which looked like very low FPS even when the source
                    # animation itself had many frames.
                    try:frame_start=max(0,min(frame_count-1,int(seg.get('start_frame',binding.get('segment_start_frame',0)) or 0)))
                    except Exception:frame_start=0
                    try:frame_span=int(seg.get('clip_frames',binding.get('segment_clip_frames',frame_count-frame_start)) or (frame_count-frame_start))
                    except Exception:frame_span=frame_count-frame_start
                    frame_span=max(1,min(frame_count-frame_start,frame_span))
                    hold=max(1.0/fps,float(frame_span)/fps)
                else:
                    try:
                        if 'frame' in seg:frame_index=max(0,min(frame_count-1,int(seg.get('frame',0))))
                        else:
                            phase=max(0.0,min(1.0,float(seg.get('phase',0.0) or 0.0)))
                            frame_index=max(0,min(frame_count-1,int(round(phase*(frame_count-1)))))
                    except Exception:frame_index=0
                    try:hold=max(1.0/fps,min(.55,float(seg.get('hold',max(2.0/fps,.11)) or max(2.0/fps,.11))))
                    except Exception:hold=max(2.0/fps,.11)
                cx,cy=self._segment_center(c,anchor,f,seg.get('dx_tiles',0.0),seg.get('dy_tiles',0.0),sw,sh,ox,oy,target_x,target_y)
                self.rows.append(dict(
                    asset=effect_asset,state=effect_state,age=0.,delay=delay,life=hold,
                    x=cx,y=cy,w=sw,h=sh,facing=f,owner=c,frame_index=frame_index,
                    frame_start=frame_start,frame_span=frame_span,fps=fps,
                    follow=False,anchor=anchor,forward=0.,ox=ox,oy=oy,segmented=True,
                ));added+=1
            self.rows=self.rows[-self.MAX:]
            return bool(added)

        # Legacy/full-flipbook path. Match-owner mode uses the native-density
        # world size above; otherwise FIX112 world-width/height behaviour remains.
        if anchor in ('target',):
            cx=target_x+ox; cy=target_y+oy
        elif anchor in ('ground','ground_forward','action_box'):
            start=float(c.x)+f*(forward+ww*.5); bottom=float(c.y)+oy
            cx=start+ox; cy=bottom-hh*.5
        else:
            cx=float(c.x)+f*forward+ox; cy=float(c.y)-float(c.height())*.5+oy
        self.rows.append(dict(
            asset=effect_asset,state=effect_state,age=0.,delay=0.,life=full_life,
            x=cx,y=cy,w=ww,h=hh,facing=f,owner=c,frame_index=None,
            follow=bool(binding.get('follow_owner',False)) and anchor not in ('ground','ground_forward','target','action_box'),
            anchor=anchor,forward=forward,ox=ox,oy=oy,segmented=False,
        ))
        self.rows=self.rows[-self.MAX:]
        return True

    def update(self,dt):
        dt=max(0.,min(.25,float(dt)));keep=[]
        for r in self.rows:
            r['age']+=dt
            delay=max(0.0,float(r.get('delay',0.0) or 0.0))
            if r.get('follow') and r['age']>=delay:
                c=r.get('owner')
                if c is not None and bool(getattr(c,'active',True)):
                    f=1 if int(getattr(c,'facing',1))>=0 else -1
                    r['facing']=f;r['x']=float(c.x)+f*float(r.get('forward',0.))+float(r.get('ox',0.))
                    r['y']=float(c.y)-float(c.height())*.5+float(r.get('oy',0.))
            if r['age']<delay+float(r.get('life',.1)):keep.append(r)
        self.rows=keep

    def snapshot(self):
        out=[]
        for r in self.rows:
            delay=max(0.0,float(r.get('delay',0.0) or 0.0))
            if r['age']<delay:continue
            local_age=max(0.0,r['age']-delay)
            cx=float(r.get('x',0.0));cy=float(r.get('y',0.0))
            if r.get('distance_motion'):
                duration=max(.05,float(r.get('travel_duration',r.get('life',.1)) or .1))
                progress=max(0.0,min(1.0,local_age/duration))
                cx=float(r.get('start_x',cx))+(float(r.get('end_x',cx))-float(r.get('start_x',cx)))*progress
                cy=float(r.get('start_y',cy))+(float(r.get('end_y',cy))-float(r.get('start_y',cy)))*progress
            item=dict(asset=r['asset'],state=r['state'],age=local_age,life=r['life'],
                      box=(cx-r['w']*.5,cy-r['h']*.5,cx+r['w']*.5,cy+r['h']*.5),
                      facing=r['facing'])
            if r.get('distance_motion'):
                fc=max(1,int(r.get('frame_count',r.get('frame_span',1)) or 1))
                try:rfps=max(1.0,float(r.get('fps',8.0) or 8.0))
                except Exception:rfps=8.0
                raw_step=max(0,int(local_age*rfps))
                if str(r.get('motion_repeat','once') or 'once').lower()=='loop':
                    item['frame_index']=raw_step%fc
                else:item['frame_index']=min(fc-1,raw_step)
            elif r.get('frame_index') is not None:
                item['frame_index']=int(r['frame_index'])
            elif int(r.get('frame_span',0) or 0)>0:
                # Animated segment window; frame changes at the source FX FPS.
                span=max(1,int(r.get('frame_span',1) or 1)); start=max(0,int(r.get('frame_start',0) or 0))
                try:rfps=max(1.0,float(r.get('fps',8.0) or 8.0))
                except Exception:rfps=8.0
                step=max(0,min(span-1,int(max(0.0,item['age'])*rfps)))
                item['frame_index']=start+step
            out.append(item)
        return tuple(out)
