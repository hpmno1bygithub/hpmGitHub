# -*- coding: utf-8 -*-
"""Cheap, view-culled pixel cloud undersides for physical floating terrain."""
from config import TILE_SIZE

def cloud_bank_quads(metadata, camera_x, camera_y, width, height, budget=72):
    rows=[]
    if not isinstance(metadata,dict):return rows
    for island in metadata.get('sky_islands',()):
        if not island.get('cloud_bank'):continue
        b=island.get('bounds',())
        if len(b)!=4:continue
        x0,y0,x1,y1=[float(v)*TILE_SIZE for v in b]
        if x1<camera_x-120 or x0>camera_x+width+120 or y1<camera_y-100 or y0>camera_y+height+100:continue
        center=(x0+x1)*.5
        # Low, stepped rectangles; no circles/gradient textures/physics tiles.
        for i in range(9):
            if len(rows)>=budget:return rows
            t=(i-4)/4.
            x=center+t*(x1-x0)*.49
            y=y0+TILE_SIZE*(1.9+(1.-abs(t))*3.5)
            color=(.75,.86,.91,.80) if i%2 else (.91,.96,.96,.86)
            rows.append((x,y,140.,TILE_SIZE*(1.1+(i%3)*.3),color))
            if i%2==0 and len(rows)<budget:
                rows.append((x+10,y+22,100.,16.,(.59,.73,.82,.56)))
    return rows


def ladder_landing(player, world, direction=1, top=False):
    """Side-step from an authored cloud shaft onto a real, unobstructed island."""
    info=getattr(player,'climb',None) or {}
    if info.get('type')!='ladder':return None
    tx=int(info.get('tx',-1))
    floors=getattr(world,'cloud_ladder_landings',{}).get(tx,())
    if not floors:return None
    for floor in sorted(floors,key=lambda r:abs(float(r)*TILE_SIZE-player.y)):
        if top and abs(float(floor)*TILE_SIZE-player.y)>TILE_SIZE*1.5:continue
        if not top and abs(float(floor)*TILE_SIZE-player.y)>14.:continue
        for side in ((1,-1) if top else (1 if direction>0 else -1,)):
            nx=(tx+side+.5)*TILE_SIZE
            surface=world.solid_top_y(tx+side,int(floor))
            if surface is None:continue
            box=player.bbox(x=nx,y=float(surface),state='stand')
            blocked=False
            for _tx,_ty,_tid,rect in world.solid_cells_in_rect(box,padding=1):
                if box[0]<rect[2] and box[2]>rect[0] and box[1]<rect[3] and box[3]>rect[1]:
                    blocked=True;break
            if not blocked:return nx,float(surface)
    return None
