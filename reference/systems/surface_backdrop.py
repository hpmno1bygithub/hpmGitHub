# -*- coding: utf-8 -*-
"""FIX83 immutable, fractional terrain silhouette; no camera-relative dirt line.

Plans cover ALL visible columns before spending budget on detail. Deep bands
merge horizontally, so a wide viewport cannot lose its rightmost back wall.
"""
from config import TILE_SIZE

SURFACE = (0.27,0.20,0.15,1.0)
SHALLOW = (0.20,0.18,0.16,1.0)
DEEP = (0.13,0.14,0.16,1.0)

def surface_y(world, tx):
    row=world.backdrop_surface_row(int(tx))
    if row is None:return None
    ratio=max(0.0,min(1.0,float(world.backdrop_surface_top_ratio(int(tx)))))
    return (row+ratio)*TILE_SIZE

def visible_columns(world, cx, width):
    lo=max(0,int(cx//TILE_SIZE)-1)
    hi=min(world.width_tiles-1,int((cx+width)//TILE_SIZE)+1)
    return [(tx,surface_y(world,tx)) for tx in range(lo,hi+1)]

def backdrop_plan(world,cx,cy,width,height,budget=112):
    """World-space centre/size/RGBA tuples with opaque subsoil coverage."""
    top,bottom=cy-2.,cy+height+2.
    cols=[(tx,sy) for tx,sy in visible_columns(world,cx,width) if sy is not None and sy<bottom]
    if not cols:return ()
    out=[]
    runs=[]
    for tx,sy in cols:
        sy=max(top,sy)
        if runs and runs[-1][1]==tx-1 and abs(runs[-1][2]-sy)<1e-6:
            runs[-1][1]=tx
        else:runs.append([tx,tx,sy])
    # One complete opaque rectangle per equal-height run. Columns outside the
    # ground silhouette remain sky; gaps cannot be bridged by a global rectangle.
    for a,b,sy in runs:
        out.append((((a+b+1)*TILE_SIZE)*.5,(sy+bottom)*.5,
                    (b-a+1)*TILE_SIZE,bottom-sy,DEEP))
    # Optional depth details get only the remaining budget. Coverage stays intact.
    for color,offset,depth in ((SHALLOW,0,7*TILE_SIZE),(SURFACE,0,1.35*TILE_SIZE)):
        for a,b,sy in runs:
            y1=max(top,sy+offset);y2=min(bottom,sy+offset+depth)
            if y2>y1 and len(out)<budget:
                out.append((((a+b+1)*TILE_SIZE)*.5,(y1+y2)*.5,
                            (b-a+1)*TILE_SIZE,y2-y1,color))
    return tuple(out)

def clip_below(rows, top, budget):
    out=[]
    for x,y,w,h,color in rows:
        y0=max(float(top),y-h*.5);y1=y+h*.5
        if y1>y0 and len(out)<budget:
            out.append((x,(y0+y1)*.5,w,y1-y0,color))
    return out
