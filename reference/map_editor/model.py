# -*- coding: utf-8 -*-
import copy
import json
import os

from map_editor.terrain_store import TerrainChunkLayer
from world.binary_map import BinaryTerrainReader, write_binary_terrain

from config import (
    WORLD_WIDTH_TILES,
    WORLD_HEIGHT_TILES,
    GROUND_ROW,
    MAP_FORMAT_VERSION,
    MAP_EDITOR_DEFAULT_WIDTH,
    MAP_EDITOR_DEFAULT_HEIGHT,
    MAP_EDITOR_SURFACE_ROW,
)


class MapDocument:
    """Layered editor map.

    Layers intentionally use stable string IDs instead of runtime tile IDs so
    maps remain compatible if numeric TileDef IDs change later.
    """

    def __init__(
        self,
        width=MAP_EDITOR_DEFAULT_WIDTH,
        height=MAP_EDITOR_DEFAULT_HEIGHT,
    ):
        self.width = int(width)
        self.height = int(height)

        surface_row = max(
            2,
            min(
                self.height - 2,
                MAP_EDITOR_SURFACE_ROW,
            ),
        )

        self.player_spawn = [
            min(
                4,
                max(0, self.width - 1),
            ),
            surface_row,
        ]

        self.layers = {
            # V0.7.1: dense terrain is packed into 16x16 uint16 chunks instead
            # of a Python {(x,y): "tile_name"} dictionary. Sparse overlays
            # remain dicts because their population is intentionally small.
            "terrain": TerrainChunkLayer(),
            # Sparse LOW/MID/HIGH geometry mask for manual map editing.
            # 1=LOW, 3=LOW|MID, 7=FULL. Runtime still keeps the historical
            # env.soil_layers name, but this layer applies to every ordinary
            # layered solid tile, not only porous soil.
            "ground_layer": {},
            "water": {},
            "lava": {},
            "honey": {},
            # FIX35 author-created map assets. Value is the full AssetRegistry
            # ID (normally tile.<slug>); collision/label live in
            # assets/custom_map_assets.json.
            "custom_map": {},
            "fire": {},
            "vegetation": {},
            "hazard": {},
            "decoration": {},
        }

        self.metadata = {
            "name": "editor_map",
            "notes": "",
        }

    @staticmethod
    def _key(
        tx,
        ty,
    ):
        return (
            int(tx),
            int(ty),
        )

    def in_bounds(
        self,
        tx,
        ty,
    ):
        return (
            0 <= int(tx) < self.width
            and 0 <= int(ty) < self.height
        )

    def get(
        self,
        layer,
        tx,
        ty,
        default=None,
    ):
        return self.layers[
            layer
        ].get(
            self._key(
                tx,
                ty,
            ),
            default,
        )

    def set(
        self,
        layer,
        tx,
        ty,
        value,
    ):
        if not self.in_bounds(
            tx,
            ty,
        ):
            return

        key = self._key(
            tx,
            ty,
        )

        self.layers[
            layer
        ][
            key
        ] = copy.deepcopy(
            value
        )

    def erase_layer(
        self,
        layer,
        tx,
        ty,
    ):
        self.layers[
            layer
        ].pop(
            self._key(
                tx,
                ty,
            ),
            None,
        )

    def erase_all(
        self,
        tx,
        ty,
    ):
        key = self._key(
            tx,
            ty,
        )

        for layer in self.layers.values():
            layer.pop(
                key,
                None,
            )

    def flood_fill(
        self,
        layer,
        tx,
        ty,
        new_value,
    ):
        if not self.in_bounds(
            tx,
            ty,
        ):
            return

        target = self.get(
            layer,
            tx,
            ty,
            None,
        )

        if target == new_value:
            return

        stack = [
            (
                int(tx),
                int(ty),
            )
        ]

        visited = set()
        changes = []

        while stack:
            x, y = stack.pop()

            if (
                x,
                y,
            ) in visited:
                continue

            visited.add(
                (
                    x,
                    y,
                )
            )

            if self.get(
                layer,
                x,
                y,
                None,
            ) != target:
                continue

            changes.append((int(x), int(y), copy.deepcopy(target)))
            self.set(
                layer,
                x,
                y,
                new_value,
            )

            for nx, ny in (
                (
                    x - 1,
                    y,
                ),
                (
                    x + 1,
                    y,
                ),
                (
                    x,
                    y - 1,
                ),
                (
                    x,
                    y + 1,
                ),
            ):
                if self.in_bounds(
                    nx,
                    ny,
                ):
                    stack.append(
                        (
                            nx,
                            ny,
                        )
                    )

        return changes

    def build_flat_template(self):
        surface_row = max(
            2,
            min(
                self.height - 2,
                MAP_EDITOR_SURFACE_ROW,
            ),
        )

        self.player_spawn = [
            min(
                4,
                max(0, self.width - 1),
            ),
            surface_row,
        ]

        for tx in range(
            self.width
        ):
            self.set(
                "terrain",
                tx,
                surface_row,
                "grass_dirt",
            )

            for ty in range(
                surface_row + 1,
                self.height,
            ):
                depth = ty - surface_row
                material = (
                    "dirt"
                    if depth <= 5
                    else "stone"
                )

                self.set(
                    "terrain",
                    tx,
                    ty,
                    material,
                )


    def clone_compact(self):
        """Copy document without expanding packed terrain to Python rows."""
        other = MapDocument(self.width, self.height)
        other.player_spawn = list(self.player_spawn)
        other.metadata = copy.deepcopy(self.metadata)
        other.layers["terrain"] = self.layers["terrain"].clone_compact()
        for name in ("ground_layer", "water", "lava", "honey", "custom_map", "fire", "vegetation", "hazard", "decoration"):
            other.layers[name] = copy.deepcopy(self.layers[name])
        return other

    def cell_state(self, tx, ty):
        key = self._key(tx, ty)
        return {
            name: copy.deepcopy(layer.get(key, None))
            for name, layer in self.layers.items()
        }

    def restore_cell_state(self, tx, ty, state):
        key = self._key(tx, ty)
        for name, layer in self.layers.items():
            value = copy.deepcopy(state.get(name))
            if value is None:
                layer.pop(key, None)
            else:
                layer[key] = value

    def resize(
        self,
        width,
        height,
    ):
        """Resize the document without destroying data on expansion."""
        width = max(
            8,
            int(width),
        )
        height = max(
            8,
            int(height),
        )

        shrinking = (
            width < self.width
            or height < self.height
        )

        self.width = width
        self.height = height

        if shrinking:
            for layer in self.layers.values():
                for key in list(layer.keys()):
                    tx, ty = key
                    if (
                        tx >= width
                        or ty >= height
                    ):
                        layer.pop(
                            key,
                            None,
                        )

        self.player_spawn[0] = max(
            0,
            min(
                self.width - 1,
                int(
                    self.player_spawn[0]
                ),
            ),
        )

        self.player_spawn[1] = max(
            0,
            min(
                self.height - 1,
                int(
                    self.player_spawn[1]
                ),
            ),
        )

        # Keep authored metadata synchronized with the actual document size.
        # Older generated maps could say size=256x144 while metadata.height=96
        # after expanding the underground depth, which made diagnostics and
        # downstream tools disagree about the same map.
        self.metadata["width"] = int(self.width)
        self.metadata["height"] = int(self.height)

    def to_dict(self, include_terrain=True):
        layers = {}

        for name, data in self.layers.items():
            rows = []

            # Binary saves keep dense terrain out of JSON. Legacy export and
            # compact undo helpers may still request a terrain list explicitly.
            if name == "terrain" and not include_terrain:
                layers[name] = rows
                continue

            for (
                tx,
                ty
            ), value in sorted(
                data.items()
            ):
                if name == "terrain":
                    rows.append(
                        [
                            tx,
                            ty,
                            value,
                        ]
                    )

                elif name == "ground_layer":
                    rows.append([tx, ty, int(value)])

                elif name in ("water", "lava", "honey"):
                    rows.append([tx, ty, float(value)])

                elif name == "custom_map":
                    rows.append([tx, ty, str(value)])

                elif name == "fire":
                    rows.append(
                        [
                            tx,
                            ty,
                            int(
                                value
                            ),
                        ]
                    )

                elif name == "vegetation":
                    kind, level = value

                    rows.append(
                        [
                            tx,
                            ty,
                            str(
                                kind
                            ),
                            int(
                                level
                            ),
                        ]
                    )

                elif name == "hazard":
                    rows.append(
                        [
                            tx,
                            ty,
                            str(
                                value
                            ),
                        ]
                    )

                elif name == "decoration":
                    kind, size = value

                    rows.append(
                        [
                            tx,
                            ty,
                            str(
                                kind
                            ),
                            int(
                                size
                            ),
                        ]
                    )

            layers[
                name
            ] = rows

        return {
            "format": "pyto_rpg_map",
            "format_version": MAP_FORMAT_VERSION,
            "size": [
                self.width,
                self.height,
            ],
            "player_spawn": list(
                self.player_spawn
            ),
            "metadata": copy.deepcopy(
                self.metadata
            ),
            "layers": layers,
        }

    @classmethod
    def from_dict(
        cls,
        payload,
    ):
        size = payload.get(
            "size",
            [
                WORLD_WIDTH_TILES,
                WORLD_HEIGHT_TILES,
            ],
        )

        doc = cls(
            int(
                size[0]
            ),
            int(
                size[1]
            ),
        )

        spawn = payload.get(
            "player_spawn",
            [
                4,
                GROUND_ROW,
            ],
        )

        doc.player_spawn = [
            int(
                spawn[0]
            ),
            int(
                spawn[1]
            ),
        ]

        doc.metadata.update(
            payload.get(
                "metadata",
                {},
            )
        )

        layers = payload.get(
            "layers",
            {},
        )

        for item in layers.get(
            "terrain",
            [],
        ):
            if len(
                item
            ) >= 3:
                doc.set(
                    "terrain",
                    item[0],
                    item[1],
                    str(
                        item[2]
                    ),
                )

        for item in layers.get(
            "ground_layer",
            [],
        ):
            if len(item) >= 3:
                doc.set(
                    "ground_layer",
                    item[0],
                    item[1],
                    int(item[2]),
                )

        for item in layers.get(
            "water",
            [],
        ):
            if len(
                item
            ) >= 3:
                doc.set(
                    "water",
                    item[0],
                    item[1],
                    float(
                        item[2]
                    ),
                )

        for item in layers.get("lava", []):
            if len(item) >= 3:
                doc.set("lava", item[0], item[1], float(item[2]))

        for item in layers.get("honey", []):
            if len(item) >= 3:
                doc.set("honey", item[0], item[1], float(item[2]))

        for item in layers.get("custom_map", []):
            if len(item) >= 3:
                doc.set("custom_map", item[0], item[1], str(item[2]))

        for item in layers.get(
            "fire",
            [],
        ):
            if len(
                item
            ) >= 3:
                doc.set(
                    "fire",
                    item[0],
                    item[1],
                    int(
                        item[2]
                    ),
                )

        for item in layers.get(
            "vegetation",
            [],
        ):
            if len(
                item
            ) >= 4:
                doc.set(
                    "vegetation",
                    item[0],
                    item[1],
                    (
                        str(
                            item[2]
                        ),
                        int(
                            item[3]
                        ),
                    ),
                )

        for item in layers.get(
            "hazard",
            [],
        ):
            if len(
                item
            ) >= 3:
                doc.set(
                    "hazard",
                    item[0],
                    item[1],
                    str(
                        item[2]
                    ),
                )

        for item in layers.get(
            "decoration",
            [],
        ):
            if len(
                item
            ) >= 4:
                doc.set(
                    "decoration",
                    item[0],
                    item[1],
                    (
                        str(
                            item[2]
                        ),
                        int(
                            item[3]
                        ),
                    ),
                )

        return doc

    @staticmethod
    def _binary_path_for_json(path):
        base, _ext = os.path.splitext(os.path.abspath(path))
        return base + ".ptw"

    def save(
        self,
        path,
    ):
        """Save small JSON metadata + independently indexed binary terrain.

        Existing V0.7.0 JSON-only maps are still readable; the next save
        automatically migrates them to V0.7.1 without changing authored data.
        """
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        binary_path = self._binary_path_for_json(path)
        storage = write_binary_terrain(
            binary_path,
            self.layers["terrain"],
            self.width,
            self.height,
        )

        payload = self.to_dict(include_terrain=False)
        payload["format_version"] = max(2, int(MAP_FORMAT_VERSION))
        payload["terrain_storage"] = storage
        payload.setdefault("metadata", {})["terrain_non_air"] = int(len(self.layers["terrain"]))
        payload["metadata"]["terrain_chunks"] = int(storage.get("non_empty_chunks", 0))
        # Backward-compatible mirror for older runtimes that only understood
        # the metadata form of fractional terrain.
        payload["metadata"]["initial_ground_layers"] = [
            [int(tx), int(ty), int(mask)]
            for (tx, ty), mask in sorted(self.layers.get("ground_layer", {}).items())
            if int(mask) not in (0, 7)
        ]

        tmp = path + ".tmp"
        with open(
            tmp,
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                payload,
                fh,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp, path)

    @classmethod
    def load(
        cls,
        path,
    ):
        path = os.path.abspath(path)
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as fh:
            payload = json.load(
                fh
            )

        doc = cls.from_dict(
            payload
        )

        storage = payload.get("terrain_storage")
        if isinstance(storage, dict) and storage.get("type") == "pyto_chunk_binary":
            rel = str(storage.get("path", "")).strip()
            binary_path = rel if os.path.isabs(rel) else os.path.join(os.path.dirname(path), rel)
            if os.path.isfile(binary_path):
                # Binary terrain replaces the intentionally empty JSON terrain
                # layer.  This path reads one compact chunk at a time.
                doc.layers["terrain"].clear()
                reader = BinaryTerrainReader(binary_path)
                if reader.width != doc.width or reader.height != doc.height:
                    raise ValueError(
                        f"terrain binary size {reader.width}x{reader.height} 與 JSON {doc.width}x{doc.height} 不一致"
                    )
                reader.load_into_editor_layer(doc.layers["terrain"])
            elif len(doc.layers["terrain"]) <= 0:
                raise FileNotFoundError(f"缺少地形資料檔：{binary_path}")

        return doc
