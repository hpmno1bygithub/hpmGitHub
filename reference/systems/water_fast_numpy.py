# -*- coding: utf-8 -*-
"""V0.7.0 NumPy local-window liquid solver for Pyto/iOS.

The authoritative world water is sparse EnvironmentState.water. NumPy arrays
cover only the active liquid window around the player, so RAM/startup cost is
independent of total map dimensions (256x144 vs 8400x2400).
"""
try:
    import numpy as np
except Exception:
    np = None

from config import (
    WATER_FALL_FLOW, WATER_SIDE_FLOW, WATER_LEVEL_RELAX,
    WATER_SUPPORT_THRESHOLD, WATER_FLOW_EPSILON, CHUNK_SIZE,
    WATER_FLOATING_ICE_PRESSURE_RELAX,
    WATER_FLOATING_ICE_PRESSURE_MAX_FLOW,
    WATER_FLOATING_ICE_PRESSURE_MIN_COLUMN,
    WATER_SOLVER_SUBSTEPS,
    WATER_LATERAL_SUPPORT_THRESHOLD,
    WATER_DIAGONAL_FALL_FLOW,
    WATER_SPAN_RELAX,
    WATER_SPAN_MAX_TILES,
    WATER_SURFACE_BRIDGE_RELAX,
    WATER_SURFACE_BRIDGE_MAX_FLOW,
    WATER_SURFACE_BRIDGE_EPSILON,
)
from world.tile_registry import tile_def, ICE
from systems import native_accel

class FastWaterSolver:
    def __init__(self, world, env):
        self.world=world; self.env=env; self.available=np is not None
        self.water_grid=None; self.solid_grid=None; self.ice_grid=None
        self.capacity_grid=None; self.floor_grid=None
        self.origin_x=0; self.origin_y=0; self.window_bounds=None
        self._cache_epoch=int(getattr(world,"cache_epoch",0))
        self._geometry_revision=int(getattr(world,"revision",0))
        self.water_by_chunk={}
        if not self.available:return
        self._reindex_sparse()
        env._water_change_callback=self.on_water_changed
        env._water_reset_callback=self.on_water_reset
        try: world.add_tile_change_listener(self.on_tile_changed)
        except Exception: pass

    @staticmethod
    def _chunk(tx,ty): return (int(tx)//CHUNK_SIZE,int(ty)//CHUNK_SIZE)

    def _reindex_sparse(self):
        self.water_by_chunk.clear()
        for key,amount in tuple(self.env.water.items()):
            if float(amount)>0.0:
                self.water_by_chunk.setdefault(self._chunk(*key),set()).add((int(key[0]),int(key[1])))

    def _in_window(self,tx,ty):
        if self.window_bounds is None:return False
        x0,y0,x1,y1=self.window_bounds
        return x0<=int(tx)<=x1 and y0<=int(ty)<=y1

    def _local(self,tx,ty): return int(tx)-self.origin_x,int(ty)-self.origin_y

    def _iter_window_water_keys(self,bounds):
        x0,y0,x1,y1=bounds
        c0x=x0//CHUNK_SIZE;c1x=x1//CHUNK_SIZE;c0y=y0//CHUNK_SIZE;c1y=y1//CHUNK_SIZE
        for cy in range(c0y,c1y+1):
            for cx in range(c0x,c1x+1):
                for key in tuple(self.water_by_chunk.get((cx,cy),())):
                    if x0<=key[0]<=x1 and y0<=key[1]<=y1:
                        yield key

    def _configure_window(self,bounds,force=False):
        x0,y0,x1,y1=map(int,bounds)
        bounds=(x0,y0,x1,y1)
        if not force and bounds==self.window_bounds and self.water_grid is not None:return
        self.window_bounds=bounds;self.origin_x=x0;self.origin_y=y0
        h=max(1,y1-y0+1);w=max(1,x1-x0+1)
        self.water_grid=np.zeros((h,w),dtype=np.float32)
        self.solid_grid=np.zeros((h,w),dtype=np.bool_)
        self.ice_grid=np.zeros((h,w),dtype=np.bool_)
        self.capacity_grid=np.ones((h,w),dtype=np.float32)
        self.floor_grid=np.zeros((h,w),dtype=np.bool_)
        # Fractional layered terrain is no longer promoted to a full-cell wall.
        # Capacity is the empty vertical fraction above the remaining material.
        for ly in range(h):
            ty=y0+ly;row=self.solid_grid[ly];ice_row=self.ice_grid[ly]
            cap_row=self.capacity_grid[ly];floor_row=self.floor_grid[ly]
            for lx in range(w):
                tx=x0+lx
                tile_id=int(self.world.get_tile(tx,ty))
                cap=float(getattr(self.world,"liquid_capacity_at",lambda _x,_y: 0.0 if tile_def(tile_id).solid else 1.0)(tx,ty))
                cap=max(0.0,min(1.0,cap))
                cap_row[lx]=cap
                row[lx]=(cap<=1e-7)
                floor_row[lx]=(cap>1e-7 and cap<1.0-1e-7)
                ice_row[lx]=(tile_id==ICE)
        for tx,ty in self._iter_window_water_keys(bounds):
            lx,ly=self._local(tx,ty)
            self.water_grid[ly,lx]=max(0.0,min(float(self.capacity_grid[ly,lx]),float(self.env.water.get((tx,ty),0.0))))
        self.water_grid[self.solid_grid]=0.0

    def on_water_reset(self):
        self.water_by_chunk.clear()
        if self.water_grid is not None:self.water_grid.fill(0.0)

    def on_water_changed(self,tx,ty,amount):
        tx=int(tx);ty=int(ty);amount=float(amount);key=(tx,ty);ck=self._chunk(tx,ty)
        if amount>1e-12:self.water_by_chunk.setdefault(ck,set()).add(key)
        else:
            cells=self.water_by_chunk.get(ck)
            if cells is not None:
                cells.discard(key)
                if not cells:self.water_by_chunk.pop(ck,None)
        if self.water_grid is not None and self._in_window(tx,ty):
            lx,ly=self._local(tx,ty)
            if 0<=ly<self.water_grid.shape[0] and 0<=lx<self.water_grid.shape[1]:
                self.water_grid[ly,lx]=max(0.0,min(1.0,amount))

    def on_tile_changed(self,tx,ty,_old_tile_id,new_tile_id):
        # Layer masks and tile IDs both affect liquid capacity. Rebuilding the
        # bounded active window on the next tick is safer than maintaining two
        # subtly different incremental geometry paths.
        self._geometry_revision=-1

    def _equalize_floating_ice_pressure(self, water, solid, rate_scale):
        """Equalize communicating water columns around floating ICE.

        Tile liquid normally moves only across open neighboring cells. A tall
        floating ice stack can therefore look like a sealed dam even when the
        sea is still connected underneath it. This pass detects horizontal ICE
        runs and, only when both sides are joined by a continuous liquid path
        below the deepest ice cell, transfers a conservative amount of surface
        water across the barrier.

        It is a pressure shortcut, not teleporting groundwater: it only touches
        free-water cells already connected under ICE inside the active NumPy
        window. Terrain walls and floor-touching ice remain true dams.
        """
        if self.ice_grid is None or not np.any(self.ice_grid):
            return 0.0
        native_moved = native_accel.equalize_floating_ice_pressure(
            water,
            solid,
            self.ice_grid,
            rate_scale,
            WATER_FLOW_EPSILON,
            WATER_FLOATING_ICE_PRESSURE_MIN_COLUMN,
            WATER_FLOATING_ICE_PRESSURE_RELAX,
            WATER_FLOATING_ICE_PRESSURE_MAX_FLOW,
        )
        if native_moved is not None:
            return float(native_moved)
        h,w=water.shape
        eps=max(float(WATER_FLOW_EPSILON),1e-6)
        min_col=max(eps,float(WATER_FLOATING_ICE_PRESSURE_MIN_COLUMN))
        relax=max(0.0,float(WATER_FLOATING_ICE_PRESSURE_RELAX))*max(0.10,float(rate_scale))
        limit=max(0.0,float(WATER_FLOATING_ICE_PRESSURE_MAX_FLOW))*max(0.10,float(rate_scale))
        moved_total=0.0

        for ly in range(h):
            xs=np.flatnonzero(self.ice_grid[ly])
            if xs.size==0:
                continue
            start=prev=int(xs[0])
            runs=[]
            for raw in xs[1:]:
                x=int(raw)
                if x==prev+1:
                    prev=x
                else:
                    runs.append((start,prev));start=prev=x
            runs.append((start,prev))

            for x0,x1 in runs:
                left=x0-1;right=x1+1
                if left<0 or right>=w:
                    continue
                if solid[ly,left] or solid[ly,right]:
                    continue

                # The bypass row must sit below the deepest contiguous ICE in
                # this run; otherwise the object genuinely closes the channel.
                deepest=ly
                for xx in range(x0,x1+1):
                    yy=ly
                    while yy+1<h and self.ice_grid[yy+1,xx]:
                        yy+=1
                    if yy>deepest: deepest=yy
                by=deepest+1
                if by>=h:
                    continue

                # Require an actual submerged communicating path under the ice.
                corridor=water[by,left:right+1]
                if np.any(solid[by,left:right+1]) or np.any(corridor<min_col):
                    continue
                if by>ly:
                    if np.any(solid[ly+1:by+1,left]) or np.any(solid[ly+1:by+1,right]):
                        continue
                    if np.any(water[ly+1:by+1,left]<min_col) or np.any(water[ly+1:by+1,right]<min_col):
                        continue

                a=float(water[ly,left]);b=float(water[ly,right])
                diff=a-b
                if abs(diff)<=eps:
                    continue
                requested=min(limit,abs(diff)*0.5*relax)
                if diff>0.0:
                    moved=min(requested,a,max(0.0,1.0-b))
                    if moved>eps:
                        water[ly,left]-=moved;water[ly,right]+=moved;moved_total+=moved
                else:
                    moved=min(requested,b,max(0.0,1.0-a))
                    if moved>eps:
                        water[ly,right]-=moved;water[ly,left]+=moved;moved_total+=moved
        return float(moved_total)

    def _relax_supported_spans(self, water, solid, support_threshold, rate_scale):
        """Flatten contiguous supported water rows without O(width^2) diffusion.

        Pairwise neighbour exchange is physically plausible but converges very
        slowly across a wide newly flooded basin: the free surface can remain a
        visible ramp for seconds.  This pass computes the mean fill of short
        contiguous supported spans and nudges every cell toward that mean.
        It is conservative because each span target has exactly the same sum.
        """
        h,w=water.shape
        if w<2:
            return 0.0
        if h>=2:
            supported=np.ones_like(solid,dtype=np.bool_)
            supported[:-1,:]=solid[1:,:] | (water[1:,:]>=float(support_threshold))
            supported[-1,:]=True
        else:
            supported=np.ones_like(solid,dtype=np.bool_)
        supported &= ~solid

        relax=max(0.0,min(1.0,float(WATER_SPAN_RELAX)*max(0.10,float(rate_scale))*2.0))
        max_span=max(2,int(WATER_SPAN_MAX_TILES))
        if relax<=0.0:
            return 0.0
        adjusted=0.0

        for y in range(h):
            mask=supported[y]
            xs=np.flatnonzero(mask)
            if xs.size<2:
                continue
            start=prev=int(xs[0])
            spans=[]
            for raw in xs[1:]:
                x=int(raw)
                if x==prev+1 and (x-start+1)<=max_span:
                    prev=x
                else:
                    spans.append((start,prev));start=prev=x
            spans.append((start,prev))
            for x0,x1 in spans:
                if x1<=x0:
                    continue
                row=water[y,x0:x1+1]
                total=float(np.sum(row,dtype=np.float64))
                if total<=float(WATER_FLOW_EPSILON):
                    continue
                target=total/float(row.size)
                before=row.copy()
                row += (target-row)*relax
                # floating point correction: preserve the exact span sum.
                correction=total-float(np.sum(row,dtype=np.float64))
                if abs(correction)>1e-9:
                    row[0]+=correction
                adjusted+=float(np.sum(np.abs(row-before),dtype=np.float64))*0.5
        return adjusted

    def _equalize_open_column_surfaces(self, water, solid, capacity, rate_scale):
        """Flatten connected open-water basins by world-space surface height.

        A tile-row solver cannot flatten a lake when the free surface crosses
        from row N into row N+1 at a shallow bank.  Instead, find contiguous
        *column spans* that are genuinely connected by water, compute one mean
        free-surface elevation for each span, and relax every column toward it.
        This is O(window cells) in NumPy + O(window width) in Python and avoids
        slow edge-to-edge diffusion across a wide lake.
        """
        h, w = water.shape
        if h < 1 or w < 2:
            return 0.0
        eps = max(1e-5, float(WATER_FLOW_EPSILON))
        wet = water > eps
        has = np.any(wet, axis=0)
        top = np.argmax(wet, axis=0)
        surfaces = np.full((w,), np.nan, dtype=np.float32)
        xs = np.flatnonzero(has)
        if xs.size < 2:
            return 0.0
        surfaces[xs] = top[xs].astype(np.float32) + capacity[top[xs], xs] - water[top[xs], xs]

        # Edge x connects columns x and x+1 only if their water volumes
        # actually overlap through an open row. This prevents flattening across
        # terrain walls or unrelated stacked aquifers.
        connected = np.zeros((w - 1,), dtype=np.bool_)
        for x in range(w - 1):
            if not has[x] or not has[x + 1]:
                continue
            y = max(int(top[x]), int(top[x + 1]))
            if y >= h:
                continue
            if solid[y, x] or solid[y, x + 1]:
                continue
            if capacity[y, x] <= eps or capacity[y, x + 1] <= eps:
                continue
            if water[y, x] <= eps or water[y, x + 1] <= eps:
                continue
            connected[x] = True

        relax = max(0.15, min(0.92, 0.84 * max(0.10, float(rate_scale))))
        moved_total = 0.0

        def remove_from_column(x, amount):
            remaining = max(0.0, float(amount)); removed = 0.0
            if remaining <= eps:
                return 0.0
            # Drain from the current free surface downward only as far as
            # needed. Usually this touches one cell.
            for y in range(int(top[x]), h):
                avail = float(water[y, x])
                if avail <= eps:
                    if removed > 0.0:
                        break
                    continue
                q = min(remaining, avail)
                water[y, x] -= q; remaining -= q; removed += q
                if remaining <= eps:
                    break
            return removed

        def add_to_column(x, amount):
            remaining = max(0.0, float(amount)); added = 0.0
            if remaining <= eps:
                return 0.0
            y = int(top[x])
            # Fill current surface cell, then spill one row upward if needed.
            for yy in (y, y - 1):
                if yy < 0 or solid[yy, x]:
                    continue
                room = max(0.0, float(capacity[yy, x]) - float(water[yy, x]))
                q = min(remaining, room)
                if q > eps:
                    water[yy, x] += q; remaining -= q; added += q
                if remaining <= eps:
                    break
            return added

        x = 0
        while x < w:
            if not has[x]:
                x += 1; continue
            x0 = x
            while x < w - 1 and connected[x]:
                x += 1
            x1 = x
            x += 1
            if x1 <= x0:
                continue
            span_s = surfaces[x0:x1 + 1].astype(np.float64)
            if not np.all(np.isfinite(span_s)):
                continue
            target = float(np.mean(span_s))
            # Positive delta means this column's surface must rise (add water).
            deltas = (span_s - target) * relax
            donors = [(x0+i, -float(d)) for i,d in enumerate(deltas) if d < -eps]
            receivers = [(x0+i, float(d)) for i,d in enumerate(deltas) if d > eps]
            if not donors or not receivers:
                continue

            # Remove into a tiny scalar pool, then redistribute. Sum(deltas)=0
            # analytically, so this remains conservative apart from fp epsilon.
            pool = 0.0
            for cx, need in donors:
                pool += remove_from_column(cx, need)
            if pool <= eps:
                continue
            for cx, need in receivers:
                if pool <= eps:
                    break
                q = add_to_column(cx, min(need, pool))
                pool -= q; moved_total += q
            # Capacity edge cases can leave a tiny pool. Put it back into the
            # first donor rather than deleting mass.
            if pool > eps:
                add_to_column(donors[0][0], pool)

        return float(moved_total)


    def _bridge_neighbor_surface_rows(self, water, solid, capacity, rate_scale):
        """Fast cross-row free-surface leveling for adjacent columns.

        The ordinary vectorized row solver is excellent when two neighboring
        surfaces live in the same tile row.  At a sloped lake bank, however,
        one column can expose row N while its neighbor exposes row N+1.  Those
        columns are still physically connected below the surface, but a
        same-row solver can leave a visible central hump / edge depression.

        This pass deliberately does *not* redistribute an entire basin.  It
        touches only adjacent top-water cells (plus at most the cell immediately
        above a receiver when that surface rises across a row boundary).  Cost
        is O(active-window width), mass is exactly conserved, and underground
        water behind a solid edge cannot be coupled accidentally.
        """
        h, w = water.shape
        if h < 1 or w < 2:
            return 0.0
        native_moved = native_accel.bridge_neighbor_surface_rows(
            water,
            solid,
            capacity,
            rate_scale,
            WATER_FLOW_EPSILON,
            WATER_SURFACE_BRIDGE_EPSILON,
            WATER_SURFACE_BRIDGE_RELAX,
            WATER_SURFACE_BRIDGE_MAX_FLOW,
        )
        if native_moved is not None:
            return float(native_moved)
        eps = max(float(WATER_FLOW_EPSILON), float(WATER_SURFACE_BRIDGE_EPSILON))
        relax = max(0.0, min(1.0, float(WATER_SURFACE_BRIDGE_RELAX) * max(0.10, float(rate_scale))))
        max_flow = max(0.0, float(WATER_SURFACE_BRIDGE_MAX_FLOW) * max(0.10, float(rate_scale)))
        if relax <= 0.0 or max_flow <= eps:
            return 0.0

        moved_total = 0.0

        # Checkerboard ordering prevents one column from being written by two
        # pairs at the same time. Recompute the top row between the two passes
        # because the first pass may create/remove a surface cell.
        for parity in (0, 1):
            wet = water > eps
            has = np.any(wet, axis=0)
            if np.count_nonzero(has) < 2:
                break
            top = np.argmax(wet, axis=0).astype(np.int32, copy=False)

            for x in range(parity, w - 1, 2):
                xr = x + 1
                if not bool(has[x]) or not bool(has[xr]):
                    continue
                yl = int(top[x]); yr = int(top[xr])

                # The two columns must overlap as real free water through the
                # shared vertical edge. This is the critical guard that keeps a
                # lake from being coupled to a separate underground aquifer.
                link_y = max(yl, yr)
                if link_y >= h:
                    continue
                if bool(solid[link_y, x]) or bool(solid[link_y, xr]):
                    continue
                if float(capacity[link_y, x]) <= eps or float(capacity[link_y, xr]) <= eps:
                    continue
                if float(water[link_y, x]) <= eps or float(water[link_y, xr]) <= eps:
                    continue

                surf_l = float(yl) + float(capacity[yl, x]) - float(water[yl, x])
                surf_r = float(yr) + float(capacity[yr, xr]) - float(water[yr, xr])
                delta = surf_r - surf_l
                if abs(delta) <= eps:
                    continue

                # Smaller world-Y means the surface is physically higher.
                if delta > 0.0:
                    donor_x, donor_y = x, yl
                    recv_x, recv_y = xr, yr
                else:
                    donor_x, donor_y = xr, yr
                    recv_x, recv_y = x, yl

                desired = min(max_flow, abs(delta) * 0.5 * relax)
                donor_amount = float(water[donor_y, donor_x])
                if donor_amount <= eps:
                    continue

                # Receiver first fills its current exposed cell. If that cell
                # is full and the surface must rise into the row above, allow
                # exactly that one open cell to accept the overflow.
                room_here = max(0.0, float(capacity[recv_y, recv_x]) - float(water[recv_y, recv_x]))
                room_up = 0.0
                up_y = recv_y - 1
                if up_y >= 0 and not bool(solid[up_y, recv_x]):
                    room_up = max(0.0, float(capacity[up_y, recv_x]) - float(water[up_y, recv_x]))
                room = room_here + room_up
                moved = min(desired, donor_amount, room)
                if moved <= eps:
                    continue

                water[donor_y, donor_x] -= moved
                q_here = min(moved, room_here)
                if q_here > 0.0:
                    water[recv_y, recv_x] += q_here
                remain = moved - q_here
                if remain > 0.0 and room_up > 0.0:
                    water[up_y, recv_x] += remain
                moved_total += moved

        return float(moved_total)

    def _release_pressure_touched_by_water(self, bounds):
        """Unlock trapped-air components once liquid reaches their boundary."""
        cells=getattr(self.world,"pressurized_air_cells",None)
        if not cells:
            return 0
        x0,y0,x1,y1=bounds
        released=0
        # Iterate a snapshot because release mutates the set.
        for x,y in tuple(cells):
            if x<x0-1 or x>x1+1 or y<y0-1 or y>y1+1:
                continue
            touching=False
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if float(self.env.water_amount(nx,ny))>0.015:
                    touching=True
                    break
            if touching:
                released += int(getattr(self.world,"release_pressurized_air_component",lambda _x,_y:0)(x,y))
        return released

    def total_mass(self):
        return sum(float(v) for v in self.env.water.values())

    def sync_from_sparse(self):
        self._reindex_sparse()
        if self.window_bounds is not None:self._configure_window(self.window_bounds,force=True)

    def step(self,parent,dt):
        if not self.available:return None
        rain_added=parent._add_rain(dt)
        bounds=tuple(map(int,parent._solver_bounds()))
        pressure_released=self._release_pressure_touched_by_water(bounds)
        epoch=int(getattr(self.world,"cache_epoch",0))
        geom=int(getattr(self.world,"revision",0))
        force=(epoch!=self._cache_epoch) or (geom!=self._geometry_revision)
        if force:
            self._cache_epoch=epoch
            self._geometry_revision=geom
            self._reindex_sparse()
        self._configure_window(bounds,force=force)
        # V0.7.5.5: porous desert sand drains before lateral pooling.
        # env.set_water callbacks patch this local NumPy grid in-place.
        infiltrated=parent._desert_sand_infiltration_pass(dt)
        old=self.water_grid.copy();water=old.copy();solid=self.solid_grid
        capacity=self.capacity_grid
        floor=self.floor_grid
        water[solid]=0.0
        # Any old value left above a newly lowered capacity is conservatively
        # pushed upward instead of being clipped away.
        excess=np.maximum(0.0,water-capacity)
        np.minimum(water,capacity,out=water)
        if water.shape[0]>=2 and np.any(excess>1e-8):
            for yy in range(water.shape[0]-1,0,-1):
                ex=excess[yy]
                if not np.any(ex>1e-8):
                    continue
                dst=water[yy-1]
                room=np.maximum(0.0,capacity[yy-1]-dst)
                moved=np.minimum(ex,room)
                dst+=moved
                excess[yy]-=moved
                excess[yy-1]+=excess[yy]
                excess[yy].fill(0.0)

        rate_scale=max(0.10,min(2.0,float(dt)*30.0))
        # V0.7.7.0: one capacity-aware vectorized pass replaces V0.7.6.9's
        # three substeps plus Python span scans. This restores iPhone frame rate
        # while equalizing the actual free-surface height over LOW/MID/FULL
        # terrain rather than equalizing raw per-tile fill fractions.
        eps=float(WATER_FLOW_EPSILON)

        # 1) Gravity. Water resting inside a fractional terrain cell has an
        # internal solid floor and must not fall through its own material.
        if water.shape[0]>=2:
            source=water[:-1,:];below=water[1:,:]
            open_down=(~solid[:-1,:]) & (~solid[1:,:]) & (~floor[:-1,:])
            room=np.maximum(0.0,capacity[1:,:]-below)
            flow=np.minimum(np.minimum(source,room),float(WATER_FALL_FLOW)*rate_scale)
            flow*=open_down
            source-=flow;below+=flow

        # 2) Diagonal gravity around edges. A fractional cell's internal floor
        # also prevents diagonal tunnelling through the material itself.
        if water.shape[0]>=2 and water.shape[1]>=2:
            for direction in (-1,1):
                if direction<0:
                    src=water[:-1,1:]; src_floor=floor[:-1,1:]
                    side_cap=capacity[:-1,:-1]
                    dst=water[1:,:-1]; dst_cap=capacity[1:,:-1]
                    dst_solid=solid[1:,:-1]
                else:
                    src=water[:-1,:-1]; src_floor=floor[:-1,:-1]
                    side_cap=capacity[:-1,1:]
                    dst=water[1:,1:]; dst_cap=capacity[1:,1:]
                    dst_solid=solid[1:,1:]
                room=np.maximum(0.0,dst_cap-dst)
                desired=np.minimum(np.minimum(src,room),float(WATER_DIAGONAL_FALL_FLOW)*rate_scale)
                open_diag=(~src_floor) & (side_cap>1e-7) & (~dst_solid)
                mask=open_diag & (desired>eps)
                moved=np.where(mask,desired,0.0).astype(np.float32,copy=False)
                src-=moved;dst+=moved

        # 3) Horizontal hydrostatic equalization by SURFACE HEIGHT.
        # For one row, surface ratio = capacity - water. Equalizing raw water
        # amounts is wrong when neighboring tiles have different layer heights.
        supported=np.array(floor,dtype=np.bool_,copy=True)
        if water.shape[0]>=2:
            below_ok=solid[1:,:] | floor[1:,:] | (
                water[1:,:] >= np.maximum(0.02,capacity[1:,:]*float(WATER_LATERAL_SUPPORT_THRESHOLD))
            )
            supported[:-1,:] |= below_ok
        supported[-1,:]=True
        supported &= ~solid

        if water.shape[1]>=2:
            # FIX77: Cython/C++ handles the hot checkerboard loops when its
            # optional extension is present. A None result means unavailable or
            # rejected input, so the exact authoritative NumPy path runs below.
            native_horizontal = native_accel.horizontal_water_pass(
                water,
                solid,
                capacity,
                supported,
                rate_scale,
                eps,
                WATER_LEVEL_RELAX,
                WATER_SIDE_FLOW,
            )
            if native_horizontal is None:
                # Two checkerboard passes are still fully vectorized and converge
                # much faster than one left-to-right diffusion pass.
                for parity in (0,1):
                    left=water[:,parity:-1:2]
                    right=water[:,parity+1::2]
                    cap_l=capacity[:,parity:-1:2]
                    cap_r=capacity[:,parity+1::2]
                    sup_l=supported[:,parity:-1:2]
                    sup_r=supported[:,parity+1::2]
                    # Shared opening exists only above the higher of the two solids.
                    shared=np.minimum(cap_l,cap_r)
                    surf_l=cap_l-left
                    surf_r=cap_r-right
                    head=(left-cap_l)-(right-cap_r)  # positive => left surface higher
                    raw=head*(0.5*float(WATER_LEVEL_RELAX)*rate_scale)
                    raw=np.clip(raw,-float(WATER_SIDE_FLOW)*rate_scale,float(WATER_SIDE_FLOW)*rate_scale)
                    open_edge=(shared>1e-7) & (~solid[:,parity:-1:2]) & (~solid[:,parity+1::2])
                    # A source must physically reach the shared opening.
                    can_lr=(surf_l < shared-1e-6)
                    can_rl=(surf_r < shared-1e-6)
                    pos=(raw>eps)&sup_l&open_edge&can_lr
                    neg=(raw<-eps)&sup_r&open_edge&can_rl
                    flux=np.zeros_like(raw,dtype=np.float32)
                    if np.any(pos):
                        flux[pos]=np.minimum(raw[pos],left[pos])
                        flux[pos]=np.minimum(flux[pos],np.maximum(0.0,cap_r[pos]-right[pos]))
                    if np.any(neg):
                        q=np.minimum(-raw[neg],right[neg])
                        q=np.minimum(q,np.maximum(0.0,cap_l[neg]-left[neg]))
                        flux[neg]=-q
                    left-=flux;right+=flux

        # V0.7.7.4: cheap adjacent-column bridge.  This fixes lake/cavity
        # surfaces that cross tile-row boundaries without re-enabling the risky
        # whole-basin equalizer from 0.7.7.1.
        surface_moved=self._bridge_neighbor_surface_rows(water,solid,capacity,rate_scale)
        pressure_moved=self._equalize_floating_ice_pressure(water,solid,rate_scale)

        # V0.7.7.2: geometry-flow must be conservative. Rain/infiltration already
        # happened before `old` was copied, and evaporation happens after commit,
        # so any significant mass change inside the NumPy transport pass is a bug.
        # Roll back the transport frame instead of allowing a whole lake/ocean to
        # disappear because of an edge-case in fractional geometry.
        mass_old=float(np.sum(old,dtype=np.float64))
        mass_new=float(np.sum(water,dtype=np.float64))
        mass_tol=max(1e-5, mass_old*1e-5)
        transport_rollback=False
        if (not np.isfinite(mass_new)) or abs(mass_new-mass_old)>mass_tol:
            water=old.copy()
            surface_moved=0.0
            pressure_moved=0.0
            transport_rollback=True

        water[np.abs(water)<max(1e-7,float(WATER_FLOW_EPSILON)*0.01)]=0.0
        np.maximum(water,0.0,out=water);np.minimum(water,capacity,out=water);water[solid]=0.0
        changed=np.argwhere(np.abs(water-old)>1e-6)
        for ly,lx in changed:
            tx=self.origin_x+int(lx);ty=self.origin_y+int(ly)
            self.env.set_water(tx,ty,float(water[int(ly),int(lx)]))
        self.water_grid[:,:]=water
        # V0.7.5.3: vectorized flow used to bypass WaterSystem's exposed
        # evaporation logic entirely. Run a sparse active-water evaporation
        # pass after geometry flow; env.set_water callbacks patch this grid.
        evaporated = parent._fast_evaporation_pass(dt)
        active_cells=int(np.count_nonzero(self.water_grid>float(WATER_FLOW_EPSILON)))
        return {
            "rain_added":float(rain_added),"infiltrated":float(infiltrated),"evaporated":float(evaporated),
            "surface_level_moved":float(surface_moved),"ice_pressure_moved":float(pressure_moved),"pressure_released":int(pressure_released),"transport_rollback":bool(transport_rollback),
            "active_cells":active_cells,
            "solver_cells":int(self.water_grid.size),"world_cells":len(self.env.water),
            "window":self.window_bounds,
        }
