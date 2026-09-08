# -*- coding: utf-8 -*-
from engine.math2d import rects_overlap
from config import PHYSICS_COLLISION_SUBSTEP_PX, TILE_SIZE
from world.tile_registry import ICE, tile_def
from world.ice_layers import ice_height_ratio_for_mass


class TilePhysics:
    """Small AABB-vs-tile physics layer.

    V0.7.4.2 treats low/mid ice as a one-way top platform.  Partial ice has
    no side wall and does not block a rising jump, preventing a player from
    being trapped between neighboring ice thicknesses. Full ice remains a
    normal solid block.
    """

    def __init__(self, world):
        self.world = world

    def _ice_ratio(self, tx, ty, tile_id):
        if tile_id != ICE:
            return 1.0
        return float(ice_height_ratio_for_mass(self.world.ice_mass_at(tx, ty)))

    def _is_partial_ice(self, tx, ty, tile_id):
        return tile_id == ICE and self._ice_ratio(tx, ty, tile_id) < 0.999

    def _is_one_way_platform(self, tx, ty, tile_id):
        return (
            self._is_partial_ice(tx, ty, tile_id)
            or bool(getattr(tile_def(tile_id), "one_way_platform", False))
        )

    def move_horizontal(self, body, dx):
        dx = float(dx)
        if dx == 0.0:
            return False
        limit = max(1.0, float(PHYSICS_COLLISION_SUBSTEP_PX))
        steps = max(1, int(abs(dx) / limit) + (1 if abs(dx) % limit > 1e-9 else 0))
        step_dx = dx / steps
        collided = False
        for _ in range(steps):
            if self._move_horizontal_once(body, step_dx):
                collided = True
                break
        return collided

    def _move_horizontal_once(self, body, dx):
        old_x = float(body.x)
        old_y = float(body.y)
        new_x = old_x + float(dx)
        test = body.bbox(x=new_x)
        collided = False

        for tx, ty, tile_id, rect in self.world.solid_cells_in_rect(test, padding=1):
            # Thin and medium ice remain side-passable one-way platforms.
            if self._is_one_way_platform(tx, ty, tile_id):
                continue
            if not rects_overlap(test, rect):
                continue

            # FIX32 keeps universal 1/3-tile step-up. FIX131 MapEditor land
            # ecology may opt a placed creature into terrain traversal up to
            # two full tiles.  We test bounded discrete rises and never disable
            # collision: the whole body box must fit at the candidate height.
            if bool(getattr(body, "grounded", False)):
                max_step = float(TILE_SIZE) / 3.0 + 1.75
                try:
                    authored_tiles=max(0.0,min(2.0,float(getattr(body,"editor_step_height_tiles",0.0) or 0.0)))
                except Exception:
                    authored_tiles=0.0
                if authored_tiles>0.0:
                    max_step=max(max_step,authored_tiles*float(TILE_SIZE)+1.75)
                rises=[]
                direct_rise=old_y-float(rect[1])
                if 0.50<direct_rise<=max_step:rises.append(direct_rise)
                if authored_tiles>0.0:
                    for frac in (1.0/3.0,2.0/3.0,1.0,4.0/3.0,5.0/3.0,2.0):
                        rise=frac*float(TILE_SIZE)
                        if rise<=max_step and all(abs(rise-r)>0.25 for r in rises):rises.append(rise)
                for step_rise in sorted(rises):
                    candidate_y=old_y-float(step_rise)
                    candidate=body.bbox(x=new_x,y=candidate_y)
                    blocked=False
                    for _sx,_sy,_sid,_srect in self.world.solid_cells_in_rect(candidate,padding=1):
                        if self._is_one_way_platform(_sx,_sy,_sid):continue
                        if rects_overlap(candidate,_srect):
                            blocked=True;break
                    if not blocked:
                        body.x=new_x;body.y=candidate_y;body.grounded=True
                        return False

            collided = True
            w = body.width()
            if new_x > old_x:
                new_x = min(new_x, rect[0] - w / 2)
            else:
                new_x = max(new_x, rect[2] + w / 2)
            test = body.bbox(x=new_x)

        body.x = new_x
        return collided

    def move_vertical(self, body, dy):
        dy = float(dy)
        if dy == 0.0:
            return False, False
        limit = max(1.0, float(PHYSICS_COLLISION_SUBSTEP_PX))
        steps = max(1, int(abs(dy) / limit) + (1 if abs(dy) % limit > 1e-9 else 0))
        step_dy = dy / steps
        collided = False
        grounded = False
        for _ in range(steps):
            hit, on_ground = self._move_vertical_once(body, step_dy)
            if hit:
                collided = True
                grounded = grounded or on_ground
                break
        return collided, grounded

    def _move_vertical_once(self, body, dy):
        old_y = body.y
        new_y = body.y + dy
        old_box = body.bbox(y=old_y)
        test = body.bbox(y=new_y)
        collided = False
        grounded = False

        for tx, ty, tile_id, rect in self.world.solid_cells_in_rect(test, padding=1):
            one_way = self._is_one_way_platform(tx, ty, tile_id)
            if one_way:
                # Partial ice and FIX68 tree branches are one-way platforms:
                # rising motion passes through; falling motion lands only on
                # the authored top surface.
                if dy < 0.0:
                    continue
                horizontal_overlap = not (test[2] <= rect[0] or test[0] >= rect[2])
                if not horizontal_overlap:
                    continue
                top = float(rect[1])
                old_feet = float(old_box[3])
                new_feet = float(test[3])
                crossed_top = old_feet <= top + 0.75 and new_feet >= top
                # If an ice layer thickened underneath a grounded body between
                # thermal ticks, lift the body to the new top instead of
                # embedding it or letting it fall through.
                grew_under_body = (
                    bool(getattr(body, "grounded", False))
                    and old_feet > top
                    and old_feet <= float(rect[3]) + 0.75
                )
                if not (crossed_top or grew_under_body):
                    continue
                new_y = min(new_y, top)
                test = body.bbox(y=new_y)
                collided = True
                grounded = True
                continue

            if not rects_overlap(test, rect):
                continue
            collided = True
            h = body.height()
            if new_y > old_y:
                new_y = min(new_y, rect[1])
                grounded = True
            else:
                new_y = max(new_y, rect[3] + h)
            test = body.bbox(y=new_y)

        body.y = new_y
        return collided, grounded

    def grounded(self, body):
        box = body.bbox()
        probe = (box[0] + 2, box[3], box[2] - 2, box[3] + 3)
        for tx, ty, tile_id, rect in self.world.solid_cells_in_rect(probe, padding=1):
            if self._is_one_way_platform(tx, ty, tile_id):
                horizontal_overlap = not (probe[2] <= rect[0] or probe[0] >= rect[2])
                if horizontal_overlap and abs(float(box[3]) - float(rect[1])) <= 3.25:
                    return True
                continue
            if rects_overlap(probe, rect):
                return True
        return False
