# -*- coding: utf-8 -*-
from array import array
from config import CHUNK_SIZE


class Chunk:
    """Fixed-size tile chunk with a local revision counter.

    V0.5.9: renderers can now invalidate only the chunk that changed instead
    of rebuilding the whole visible tile window after every mined block.
    """

    __slots__ = (
        "cx",
        "cy",
        "_tiles",
        "revision",
        "non_air_count",
        "dirty_indices",
    )

    def __init__(self, cx, cy):
        self.cx = int(cx)
        self.cy = int(cy)
        self._tiles = array("H", [0]) * (CHUNK_SIZE * CHUNK_SIZE)
        self.revision = 0
        self.non_air_count = 0
        # Renderer consumes these local tile indices to patch only changed
        # terrain slots instead of rebuilding/reallocating a whole chunk.
        self.dirty_indices = set()

    @staticmethod
    def _index(lx, ly):
        return ly * CHUNK_SIZE + lx

    def get(self, lx, ly):
        return self._tiles[self._index(lx, ly)]

    def set(self, lx, ly, tile_id):
        index = self._index(lx, ly)
        old = int(self._tiles[index])
        new = int(tile_id)

        if old == new:
            return False

        self._tiles[index] = new
        self.dirty_indices.add(index)

        if old == 0 and new != 0:
            self.non_air_count += 1
        elif old != 0 and new == 0:
            self.non_air_count = max(0, self.non_air_count - 1)

        self.revision += 1
        return True
