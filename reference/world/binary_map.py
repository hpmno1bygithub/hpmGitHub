# -*- coding: utf-8 -*-
"""Indexed binary terrain format for large PytoRPG worlds.

The JSON map remains small metadata + sparse overlays. Dense terrain is stored
in a companion .ptw file. Each non-empty 16x16 terrain chunk is independently
zlib-compressed and indexed, allowing runtime random access without loading the
whole world into Python objects.
"""
from array import array
import os
import struct
import tempfile
import zlib


MAGIC = b"PTRPGW71"
FORMAT_VERSION = 1
_HEADER = struct.Struct("<8sHHIIHHII")
_INDEX = struct.Struct("<iiQIIH2x")


class BinaryMapError(Exception):
    pass


def _to_little_u16_bytes(values):
    packed = array("H", values)
    if packed.itemsize != 2:
        raise BinaryMapError("目前平台 uint16 大小不正確")
    import sys
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def _from_little_u16_bytes(raw):
    packed = array("H")
    packed.frombytes(raw)
    import sys
    if sys.byteorder != "little":
        packed.byteswap()
    return packed


def _surface_rows_for_layer(layer, width, height):
    width = int(width)
    height = int(height)
    rows = array("i", [-1]) * width
    cs = int(layer.chunk_size)
    for cx, cy, chunk, non_air in layer.iter_chunks():
        if non_air <= 0:
            continue
        start_x = int(cx) * cs
        start_y = int(cy) * cs
        for index, raw in enumerate(chunk):
            if int(raw) == 0:
                continue
            tx = start_x + (index % cs)
            ty = start_y + (index // cs)
            if tx < 0 or tx >= width or ty < 0 or ty >= height:
                continue
            current = int(rows[tx])
            if current < 0 or ty < current:
                rows[tx] = ty
    import sys
    if sys.byteorder != "little":
        rows.byteswap()
    return rows.tobytes()


def write_binary_terrain(path, layer, width, height):
    """Atomically write an indexed companion terrain file."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    palette = [str(v) for v in layer.palette]
    if not palette or palette[0] != "air":
        raise BinaryMapError("terrain palette[0] 必須是 air")

    palette_blob_parts = []
    for name in palette:
        raw = name.encode("utf-8")
        if len(raw) > 65535:
            raise BinaryMapError("terrain 名稱過長")
        palette_blob_parts.append(struct.pack("<H", len(raw)))
        palette_blob_parts.append(raw)
    palette_blob = b"".join(palette_blob_parts)

    surface_raw = _surface_rows_for_layer(layer, width, height)
    surface_blob = zlib.compress(surface_raw, 6)

    payload_fd, payload_path = tempfile.mkstemp(prefix="ptw_payload_", suffix=".tmp", dir=os.path.dirname(path))
    os.close(payload_fd)
    entries = []
    payload_relative = 0
    try:
        with open(payload_path, "wb") as payload_fh:
            for cx, cy, codes, non_air in layer.iter_chunks():
                raw = _to_little_u16_bytes(codes)
                comp = zlib.compress(raw, 6)
                payload_fh.write(comp)
                entries.append((int(cx), int(cy), payload_relative, len(comp), len(raw), int(non_air)))
                payload_relative += len(comp)

        header_size = _HEADER.size
        index_size = _INDEX.size * len(entries)
        payload_start = header_size + len(palette_blob) + len(surface_blob) + index_size

        tmp = path + ".tmp"
        with open(tmp, "wb") as out:
            out.write(_HEADER.pack(
                MAGIC,
                FORMAT_VERSION,
                int(layer.chunk_size),
                int(width),
                int(height),
                len(palette),
                0,
                len(entries),
                len(surface_blob),
            ))
            out.write(palette_blob)
            out.write(surface_blob)
            for cx, cy, rel, comp_size, raw_size, non_air in entries:
                out.write(_INDEX.pack(
                    cx,
                    cy,
                    payload_start + rel,
                    comp_size,
                    raw_size,
                    min(65535, non_air),
                ))
            with open(payload_path, "rb") as payload_fh:
                while True:
                    block = payload_fh.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass

    return {
        "type": "pyto_chunk_binary",
        "version": FORMAT_VERSION,
        "path": os.path.basename(path),
        "chunk_size": int(layer.chunk_size),
        "palette_count": len(palette),
        "non_empty_chunks": len(entries),
        "non_air_tiles": int(len(layer)),
        "bytes": int(os.path.getsize(path)),
    }


class BinaryTerrainReader:
    """Random-access reader. Only the index and surface cache stay in RAM."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.version = 0
        self.chunk_size = 0
        self.width = 0
        self.height = 0
        self.palette = []
        self.index = {}
        self.surface_rows = []
        self._read_header()

    def _read_header(self):
        with open(self.path, "rb") as fh:
            raw = fh.read(_HEADER.size)
            if len(raw) != _HEADER.size:
                raise BinaryMapError("terrain binary header 不完整")
            magic, version, chunk_size, width, height, palette_count, _flags, chunk_count, surface_size = _HEADER.unpack(raw)
            if magic != MAGIC:
                raise BinaryMapError("terrain binary magic 不正確")
            if int(version) != FORMAT_VERSION:
                raise BinaryMapError(f"terrain binary version 不支援：{version}")
            self.version = int(version)
            self.chunk_size = int(chunk_size)
            self.width = int(width)
            self.height = int(height)

            palette = []
            for _ in range(int(palette_count)):
                length_raw = fh.read(2)
                if len(length_raw) != 2:
                    raise BinaryMapError("terrain palette 不完整")
                length = struct.unpack("<H", length_raw)[0]
                name_raw = fh.read(length)
                if len(name_raw) != length:
                    raise BinaryMapError("terrain palette text 不完整")
                palette.append(name_raw.decode("utf-8"))
            self.palette = palette

            surface_blob = fh.read(int(surface_size))
            if len(surface_blob) != int(surface_size):
                raise BinaryMapError("surface cache 不完整")
            surface_raw = zlib.decompress(surface_blob) if surface_blob else b""
            rows = array("i")
            if surface_raw:
                rows.frombytes(surface_raw)
                import sys
                if sys.byteorder != "little":
                    rows.byteswap()
            self.surface_rows = [int(v) for v in rows]
            if len(self.surface_rows) != self.width:
                self.surface_rows = [-1] * self.width

            index = {}
            for _ in range(int(chunk_count)):
                entry_raw = fh.read(_INDEX.size)
                if len(entry_raw) != _INDEX.size:
                    raise BinaryMapError("terrain chunk index 不完整")
                cx, cy, offset, comp_size, raw_size, non_air = _INDEX.unpack(entry_raw)
                index[(int(cx), int(cy))] = (
                    int(offset), int(comp_size), int(raw_size), int(non_air)
                )
            self.index = index

    @property
    def non_empty_chunk_count(self):
        return len(self.index)

    @property
    def non_air_tile_count(self):
        return sum(int(v[3]) for v in self.index.values())

    def has_chunk(self, cx, cy):
        return (int(cx), int(cy)) in self.index

    def read_chunk_codes(self, cx, cy):
        info = self.index.get((int(cx), int(cy)))
        if info is None:
            return None
        offset, comp_size, raw_size, _non_air = info
        with open(self.path, "rb") as fh:
            fh.seek(offset)
            comp = fh.read(comp_size)
        raw = zlib.decompress(comp)
        if len(raw) != raw_size:
            raise BinaryMapError("terrain chunk 解壓後大小不正確")
        codes = _from_little_u16_bytes(raw)
        expected = self.chunk_size * self.chunk_size
        if len(codes) != expected:
            raise BinaryMapError("terrain chunk 格數不正確")
        return codes

    def iter_chunk_keys(self):
        return iter(self.index.keys())

    def load_into_editor_layer(self, layer):
        if int(layer.chunk_size) != self.chunk_size:
            raise BinaryMapError("editor/runtime CHUNK_SIZE 與地圖不一致")
        for cx, cy in sorted(self.index.keys()):
            codes = self.read_chunk_codes(cx, cy)
            if codes is not None:
                layer.import_chunk(cx, cy, codes, self.palette)
