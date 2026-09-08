# -*- coding: utf-8 -*-
"""Compact chunked terrain storage used by the map editor.

V0.7.1 removes the editor's tuple->string dict for dense terrain.  Terrain is
kept as 16x16 uint16 chunks with a stable string palette, so a large authored
world costs roughly two bytes per material cell plus chunk bookkeeping instead
of one Python dict/tuple/string reference per tile.
"""
from array import array
from config import CHUNK_SIZE


DEFAULT_TERRAIN_PALETTE = (
    "air",
    "dirt",
    "grass_dirt",
    "ash",
    "stone",
    "copper_ore",
    "iron_ore",
    "gold_ore",
    "marble",
    "limestone",
    "mud",
    "swamp_soil",
    "wood",
    "ladder",
    "ice",
)


class TerrainChunkLayer:
    """Mapping-like sparse collection backed by packed uint16 chunk arrays."""

    def __init__(self, chunk_size=CHUNK_SIZE, palette=None):
        self.chunk_size = int(chunk_size)
        names = list(palette or DEFAULT_TERRAIN_PALETTE)
        if not names or names[0] != "air":
            names = ["air"] + [n for n in names if n != "air"]
        self.palette = [str(v) for v in names]
        self._name_to_code = {name: i for i, name in enumerate(self.palette)}
        self.chunks = {}
        self.chunk_non_air = {}
        self.total_non_air = 0

    def _coords(self, key):
        tx, ty = key
        tx = int(tx)
        ty = int(ty)
        cs = self.chunk_size
        return tx // cs, ty // cs, tx % cs, ty % cs

    def _index(self, lx, ly):
        return int(ly) * self.chunk_size + int(lx)

    def _code_for_name(self, value):
        if value is None:
            return 0
        name = str(value)
        if name == "air":
            return 0
        code = self._name_to_code.get(name)
        if code is None:
            if len(self.palette) >= 65535:
                raise ValueError("terrain palette 超過 uint16 可表示範圍")
            code = len(self.palette)
            self.palette.append(name)
            self._name_to_code[name] = code
        return int(code)

    def _name_for_code(self, code, default=None):
        code = int(code)
        if code <= 0:
            return default
        if code >= len(self.palette):
            return default
        return self.palette[code]

    def _ensure_chunk(self, cx, cy):
        key = (int(cx), int(cy))
        chunk = self.chunks.get(key)
        if chunk is None:
            chunk = array("H", [0]) * (self.chunk_size * self.chunk_size)
            self.chunks[key] = chunk
            self.chunk_non_air[key] = 0
        return chunk

    def get(self, key, default=None):
        cx, cy, lx, ly = self._coords(key)
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            return default
        return self._name_for_code(chunk[self._index(lx, ly)], default)

    def __getitem__(self, key):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        cx, cy, lx, ly = self._coords(key)
        ckey = (cx, cy)
        index = self._index(lx, ly)
        new_code = self._code_for_name(value)
        chunk = self.chunks.get(ckey)
        old_code = 0 if chunk is None else int(chunk[index])
        if old_code == new_code:
            return
        if chunk is None:
            if new_code == 0:
                return
            chunk = self._ensure_chunk(cx, cy)
        chunk[index] = new_code
        old_non_air = old_code != 0
        new_non_air = new_code != 0
        if old_non_air != new_non_air:
            delta = 1 if new_non_air else -1
            self.total_non_air += delta
            self.chunk_non_air[ckey] = int(self.chunk_non_air.get(ckey, 0)) + delta
        if int(self.chunk_non_air.get(ckey, 0)) <= 0:
            self.chunks.pop(ckey, None)
            self.chunk_non_air.pop(ckey, None)

    def pop(self, key, default=None):
        sentinel = object()
        old = self.get(key, sentinel)
        if old is sentinel:
            return default
        self[key] = None
        return old

    def __contains__(self, key):
        return self.get(key, None) is not None

    def __len__(self):
        return int(self.total_non_air)

    def clear(self):
        self.chunks.clear()
        self.chunk_non_air.clear()
        self.total_non_air = 0

    def items(self):
        cs = self.chunk_size
        for (cx, cy), chunk in tuple(self.chunks.items()):
            start_x = cx * cs
            start_y = cy * cs
            for index, raw in enumerate(chunk):
                code = int(raw)
                if code == 0:
                    continue
                tx = start_x + (index % cs)
                ty = start_y + (index // cs)
                yield (tx, ty), self.palette[code]

    def keys(self):
        for key, _value in self.items():
            yield key

    def values(self):
        for _key, value in self.items():
            yield value

    def __iter__(self):
        return self.keys()

    def iter_chunks(self):
        """Yield (cx, cy, packed_codes, non_air_count) for non-empty chunks."""
        for (cx, cy), chunk in sorted(self.chunks.items()):
            non_air = int(self.chunk_non_air.get((cx, cy), 0))
            if non_air > 0:
                yield int(cx), int(cy), chunk, non_air

    def import_chunk(self, cx, cy, codes, source_palette):
        """Import one binary chunk while remapping its palette once."""
        src_palette = [str(v) for v in source_palette]
        remap = [0] * len(src_palette)
        for i, name in enumerate(src_palette):
            remap[i] = self._code_for_name(name)
        expected = self.chunk_size * self.chunk_size
        if len(codes) != expected:
            raise ValueError("terrain chunk cell count 不正確")
        packed = array("H", [0]) * expected
        non_air = 0
        for i, raw in enumerate(codes):
            code = int(raw)
            mapped = remap[code] if 0 <= code < len(remap) else 0
            packed[i] = mapped
            if mapped != 0:
                non_air += 1
        key = (int(cx), int(cy))
        if non_air > 0:
            old = int(self.chunk_non_air.get(key, 0))
            self.total_non_air += non_air - old
            self.chunks[key] = packed
            self.chunk_non_air[key] = non_air
        else:
            old = int(self.chunk_non_air.pop(key, 0))
            self.total_non_air -= old
            self.chunks.pop(key, None)

    def clone_compact(self):
        other = TerrainChunkLayer(self.chunk_size, self.palette)
        other.chunks = {key: array("H", chunk) for key, chunk in self.chunks.items()}
        other.chunk_non_air = dict(self.chunk_non_air)
        other.total_non_air = int(self.total_non_air)
        return other
