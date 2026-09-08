# -*- coding: utf-8 -*-
import math

from config import (
    TILE_SIZE,
    CHUNK_SIZE,
    ACTIVE_CHUNK_RADIUS_X,
    ACTIVE_CHUNK_RADIUS_Y,
)


class ChunkStreamer:
    """Tracks the chunks that should be fully active around the player.

    V0.3 does not delete far-away world data yet. Instead it separates:

        loaded chunks  = tile data present in memory
        active chunks  = chunks allowed to run high-frequency systems

    Future water / fire / plant systems should iterate active_chunks,
    not the whole world.
    """

    def __init__(
        self,
        world,
        radius_x=ACTIVE_CHUNK_RADIUS_X,
        radius_y=ACTIVE_CHUNK_RADIUS_Y,
    ):
        self.world = world
        self.radius_x = int(radius_x)
        self.radius_y = int(radius_y)

        self.active_chunks = set()
        self.activated_last_update = set()
        self.deactivated_last_update = set()

        self.player_chunk = (0, 0)
        self._has_player_chunk = False
        self._world_size_key = None

    @staticmethod
    def _world_to_chunk(x, y):
        size = TILE_SIZE * CHUNK_SIZE

        return (
            int(math.floor(x / size)),
            int(math.floor(y / size)),
        )

    def update(self, x, y):
        center_cx, center_cy = self._world_to_chunk(x, y)

        world_size_key = (
            int(self.world.width_tiles),
            int(self.world.height_tiles),
            int(self.radius_x),
            int(self.radius_y),
        )

        # V0.5.8: GameApp calls update before and after every 60 Hz player
        # step. Rebuilding the same active-chunk set hundreds of times per
        # second creates needless Python allocations. If the player remains in
        # the same chunk and the world size did not change, the active set is
        # identical and can be kept as-is.
        if (
            self._has_player_chunk
            and self.player_chunk == (center_cx, center_cy)
            and self._world_size_key == world_size_key
        ):
            self.activated_last_update = set()
            self.deactivated_last_update = set()
            return False

        self.player_chunk = (center_cx, center_cy)
        self._has_player_chunk = True
        self._world_size_key = world_size_key

        max_cx = max(
            0,
            (self.world.width_tiles - 1) // CHUNK_SIZE,
        )
        max_cy = max(
            0,
            (self.world.height_tiles - 1) // CHUNK_SIZE,
        )

        desired = set()

        for cy in range(
            center_cy - self.radius_y,
            center_cy + self.radius_y + 1,
        ):
            if cy < 0 or cy > max_cy:
                continue

            for cx in range(
                center_cx - self.radius_x,
                center_cx + self.radius_x + 1,
            ):
                if cx < 0 or cx > max_cx:
                    continue

                desired.add((cx, cy))

        self.activated_last_update = (
            desired - self.active_chunks
        )
        self.deactivated_last_update = (
            self.active_chunks - desired
        )

        self.active_chunks = desired

        # V0.7.1 binary maps: warm newly active terrain chunks before systems
        # touch them, then keep only a small cache ring in Python RAM. World
        # size may be 20M+ tiles, but loaded terrain remains local to player.
        preload = getattr(self.world, "preload_chunk", None)
        if callable(preload):
            for cx, cy in self.activated_last_update:
                preload(cx, cy)

        if getattr(self.world, "backing_store", None) is not None:
            keep_rx = self.radius_x + 2
            keep_ry = self.radius_y + 2
            unload = getattr(self.world, "unload_chunk", None)
            if callable(unload):
                for cx, cy in tuple(self.world.chunks.keys()):
                    if (
                        abs(int(cx) - center_cx) > keep_rx
                        or abs(int(cy) - center_cy) > keep_ry
                    ):
                        unload(cx, cy)

        return True


    def iter_active_tile_coords(self):
        """Yield tile coordinates belonging to currently active chunks.

        Keeps high-frequency/gameplay systems bounded by the player's active
        region even when the stored world becomes very large.
        """
        for cx, cy in self.active_chunks:
            start_tx = int(cx) * CHUNK_SIZE
            start_ty = int(cy) * CHUNK_SIZE
            end_tx = min(
                self.world.width_tiles,
                start_tx + CHUNK_SIZE,
            )
            end_ty = min(
                self.world.height_tiles,
                start_ty + CHUNK_SIZE,
            )

            for ty in range(start_ty, end_ty):
                for tx in range(start_tx, end_tx):
                    yield tx, ty

    def is_active(self, cx, cy):
        return (cx, cy) in self.active_chunks

    @property
    def active_count(self):
        return len(self.active_chunks)

    @property
    def loaded_count(self):
        return len(self.world.chunks)
