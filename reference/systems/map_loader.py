# -*- coding: utf-8 -*-
import json
import os

from world.binary_map import BinaryTerrainReader, BinaryMapError

from config import (
    TILE_SIZE,
    CHUNK_SIZE,
    SWAMP_DAMAGE_PER_SECOND,
    MUD_PLAYER_SPEED_MULTIPLIER,
    SWAMP_SOIL_DAMAGE_PER_SECOND,
    LAVA_CONTACT_DAMAGE_PER_SECOND,
    LAVA_BURN_DAMAGE_PER_SECOND,
    LAVA_BURN_SECONDS,
    WORLD_GEN_SURFACE_ROW,
    WORLD_GEN_LAKE_SURFACE_ROW,
)
from world.tile_registry import (
    AIR,
    DIRT,
    GRASS_DIRT,
    WOOD,
    LADDER,
    ICE,
    ASH,
    STONE,
    COPPER_ORE,
    IRON_ORE,
    GOLD_ORE,
    MARBLE,
    LIMESTONE,
    MUD,
    SWAMP_SOIL,
    SAND,
    JUNGLE_SOIL,
    SNOW_DIRT,
    VILLAGE_PATH,
    SEA_SAND,
    IGNEOUS_ROCK,
    HONEYCOMB,
    RAINFOREST_TRUNK,
    RAINFOREST_BRANCH,
    RAINFOREST_VINE,
    RAINFOREST_CANOPY,
    RAINFOREST_ROOT,
    LAYERED_GROUND_TILES,
    LAYERED_SOLID_TILES,
    tile_def,
)
from world.environment_state import (
    FireCell,
    PlantCell,
    SoilCell,
)
from world.soil_layers import SOIL_LAYER_FULL, normalize_soil_mask, soil_capacity_ratio
from world.biomes import (
    normalize_biome_spans,
    biome_at_tile_x,
    BIOME_CLIMATE_PROFILE,
)


TERRAIN_NAME_TO_ID = {
    "air": AIR,
    "dirt": DIRT,
    "grass_dirt": GRASS_DIRT,
    "wood": WOOD,
    "ladder": LADDER,
    "ice": ICE,
    "ash": ASH,
    "stone": STONE,
    "copper_ore": COPPER_ORE,
    "iron_ore": IRON_ORE,
    "gold_ore": GOLD_ORE,
    "marble": MARBLE,
    "limestone": LIMESTONE,
    "mud": MUD,
    "swamp_soil": SWAMP_SOIL,
    "sand": SAND,
    "jungle_soil": JUNGLE_SOIL,
    "snow_dirt": SNOW_DIRT,
    "village_path": VILLAGE_PATH,
    "sea_sand": SEA_SAND,
    "igneous_rock": IGNEOUS_ROCK,
    "honeycomb": HONEYCOMB,
    "rainforest_trunk": RAINFOREST_TRUNK,
    "rainforest_branch": RAINFOREST_BRANCH,
    "rainforest_vine": RAINFOREST_VINE,
    "rainforest_canopy": RAINFOREST_CANOPY,
    "rainforest_root": RAINFOREST_ROOT,
}


class MapRuntimeState:
    def __init__(self):
        self.hazards = {}
        self.decorations = {}
        self.custom_map_cells = {}
        self.custom_map_meta = {}
        self.source_path = ""
        self.map_name = ""
        # FIX77 keeps only active and currently-burning creatures in the lava
        # hazard working set. This avoids a second global chunk scan after
        # CreatureSystem already selected the active population for the frame.
        self._lava_burning_creatures = {}
        self._lava_registry_seeded = False

    def clear(self):
        self.hazards.clear()
        self.decorations.clear()
        self.custom_map_cells.clear()
        self.custom_map_meta.clear()
        self._lava_burning_creatures.clear()
        self._lava_registry_seeded = False

    def hazard_at(
        self,
        tx,
        ty,
    ):
        return self.hazards.get(
            (
                int(tx),
                int(ty),
            )
        )

    def update_player(
        self,
        game,
        dt,
    ):
        player = game.player

        foot_tx = int(
            player.x
            // TILE_SIZE
        )

        foot_ty = int(
            (
                player.y
                + 1.0
            )
            // TILE_SIZE
        )

        player.map_speed_multiplier = 1.0

        if (
            game.world.get_tile(
                foot_tx,
                foot_ty,
            )
            == MUD
        ):
            player.map_speed_multiplier = (
                MUD_PLAYER_SPEED_MULTIPLIER
            )

        hazard = self.hazard_at(
            foot_tx,
            foot_ty,
        )

        on_dynamic_swamp = (
            game.world.get_tile(
                foot_tx,
                foot_ty,
            )
            == SWAMP_SOIL
        )

        if (
            hazard == "swamp"
            or on_dynamic_swamp
        ):
            # FIX31: do not apply terrain damage during the short startup
            # presentation grace period. Movement effects still apply.
            if float(getattr(player, "startup_damage_grace", 0.0)) > 0.0:
                return
            damage_rate = (
                SWAMP_SOIL_DAMAGE_PER_SECOND
                if on_dynamic_swamp
                else SWAMP_DAMAGE_PER_SECOND
            )

            damage = damage_rate * max(0.0, float(dt))
            taker = getattr(player, "take_damage", None)
            if callable(taker):
                taker(damage, source_x=player.x, kind="swamp")
            else:
                player.hp = max(0.0, float(player.hp) - damage)

        # FIX37 lava damage is evaluated *after* Player.update so contact uses
        # the current-frame position and the same fractional lava geometry as
        # rendering/flow. See update_player_lava().

    @staticmethod
    def _actor_touches_dynamic_lava(game, actor):
        try:
            lava=game.environment.lava
            bx1,by1,bx2,by2=actor.bbox()
            xs=(
                (float(bx1)+float(bx2))*0.5,
                float(bx1)+(float(bx2)-float(bx1))*0.28,
                float(bx2)-(float(bx2)-float(bx1))*0.28,
            )
            ys=(
                float(by2)-0.5,
                float(by2)-max(4.0,(float(by2)-float(by1))*0.28),
                float(by2)-max(8.0,(float(by2)-float(by1))*0.48),
            )
            # FIX40 broad phase: most creatures are nowhere near magma.  The
            # old path still ran 9 depth queries per active creature per frame;
            # each depth query scanned a vertical lava column and tile capacity.
            # Check the sparse authoritative lava table first, including the
            # row above because depth_at_world accepts that boundary geometry.
            lava_map=getattr(game.environment.state,"lava",{})
            candidate=False
            for px in xs:
                tx=int(float(px)//TILE_SIZE)
                for py in ys:
                    ty=int(float(py)//TILE_SIZE)
                    if float(lava_map.get((tx,ty),0.0))>1e-9 or float(lava_map.get((tx,ty-1),0.0))>1e-9:
                        candidate=True; break
                if candidate: break
            if not candidate:
                return False
            return any(float(lava.depth_at_world(px,py))>0.50 for px in xs for py in ys)
        except Exception:
            try:
                tx=int(float(actor.x)//TILE_SIZE);ty=int((float(actor.y)-0.5)//TILE_SIZE)
                return game.environment.state.lava_amount(tx,ty)>0.08
            except Exception:
                return False

    def update_player_lava(self, game, dt):
        player=game.player
        burn=max(0.0,float(getattr(player,"lava_burn_timer",0.0)))
        try:
            foot_tx=int(float(player.x)//TILE_SIZE)
            foot_ty=int((float(player.y)-0.5)//TILE_SIZE)
            legacy=(self.hazard_at(foot_tx,foot_ty)=="lava")
        except Exception:
            legacy=False
        touching=legacy or self._actor_touches_dynamic_lava(game,player)
        if touching:
            burn=float(LAVA_BURN_SECONDS)
            if float(getattr(player,"startup_damage_grace",0.0))<=0.0:
                damage=float(LAVA_CONTACT_DAMAGE_PER_SECOND)*max(0.0,float(dt))
                taker=getattr(player,"take_damage",None)
                if callable(taker):taker(damage,source_x=player.x,kind="lava_contact")
                else:player.hp=max(0.0,float(player.hp)-damage)
        elif burn>0.0:
            if float(getattr(player,"startup_damage_grace",0.0))<=0.0:
                damage=float(LAVA_BURN_DAMAGE_PER_SECOND)*max(0.0,float(dt))
                taker=getattr(player,"take_damage",None)
                if callable(taker):taker(damage,source_x=player.x,kind="lava_burn")
                else:player.hp=max(0.0,float(player.hp)-damage)
            burn=max(0.0,burn-max(0.0,float(dt)))
        player.lava_burn_timer=burn

    def update_creatures(self, game, dt):
        active = getattr(getattr(game, "creature_system", None), "active_population", None)
        streamed = isinstance(active, (list, tuple))
        active_rows = list(active) if streamed else list(getattr(game, "creatures", ()))
        active_ids = {id(c) for c in active_rows} if streamed else None

        # Seed restored afterburn state once. Afterwards every newly burning
        # creature is registered by this method, so sleeping non-burning actors
        # never need to be revisited.
        burning = dict(getattr(self, "_lava_burning_creatures", {}) or {})
        if not bool(getattr(self, "_lava_registry_seeded", False)):
            for c in getattr(game, "creatures", ()):
                if bool(getattr(c, "active", False)) and float(getattr(c, "lava_burn_timer", 0.0)) > 0.0:
                    burning[id(c)] = c
            self._lava_registry_seeded = True

        candidates = active_rows
        if burning:
            seen = {id(c) for c in candidates}
            for key, c in tuple(burning.items()):
                if not bool(getattr(c, "active", False)):
                    burning.pop(key, None)
                    continue
                if id(c) not in seen:
                    candidates.append(c)
                    seen.add(id(c))

        next_burning = {}
        frame_dt = max(0.0, float(dt))
        for c in candidates:
            if getattr(c,"background_only",False): continue
            if not bool(getattr(c, "active", False)):
                continue
            burn = max(0.0, float(getattr(c, "lava_burn_timer", 0.0)))
            if streamed:
                chunk_active = id(c) in active_ids
            else:
                try:
                    ccx = int(float(c.x) // (TILE_SIZE * CHUNK_SIZE))
                    ccy = int(float(c.y) // (TILE_SIZE * CHUNK_SIZE))
                    chunk_active = bool(game.chunk_streamer.is_active(ccx, ccy))
                except Exception:
                    chunk_active = True
            if not chunk_active:
                if burn > 0.0:
                    c.hp = max(
                        0.0,
                        float(c.hp) - float(LAVA_BURN_DAMAGE_PER_SECOND) * 0.72 * frame_dt,
                    )
                    burn = max(0.0, burn - frame_dt)
                    c.lava_burn_timer = burn
                    if burn > 0.0:
                        next_burning[id(c)] = c
                continue

            tx = int(float(c.x) // TILE_SIZE)
            ty = int((float(c.y) - 0.5) // TILE_SIZE)
            try:
                touching = (
                    self.hazard_at(tx, ty) == "lava"
                    or self._actor_touches_dynamic_lava(game, c)
                )
            except Exception:
                touching = self.hazard_at(tx, ty) == "lava"
            if touching:
                burn = float(LAVA_BURN_SECONDS)
                c.hp = max(
                    0.0,
                    float(c.hp) - float(LAVA_CONTACT_DAMAGE_PER_SECOND) * 0.72 * frame_dt,
                )
            elif burn > 0.0:
                c.hp = max(
                    0.0,
                    float(c.hp) - float(LAVA_BURN_DAMAGE_PER_SECOND) * 0.72 * frame_dt,
                )
                burn = max(0.0, burn - frame_dt)
            c.lava_burn_timer = burn
            if burn > 0.0:
                next_burning[id(c)] = c
        self._lava_burning_creatures = next_burning



class MapLoader:
    def __init__(
        self,
        path,
    ):
        self.path = os.path.abspath(
            path
        )

        self.payload = None
        self.terrain_reader = None

    def exists(self):
        return os.path.isfile(
            self.path
        )

    def read(self):
        with open(
            self.path,
            "r",
            encoding="utf-8",
        ) as fh:
            self.payload = json.load(
                fh
            )

        return self.payload

    def _terrain_storage(self):
        if self.payload is None:
            self.read()
        storage = self.payload.get("terrain_storage")
        return storage if isinstance(storage, dict) else None

    def _binary_terrain_path(self):
        storage = self._terrain_storage()
        if not storage or storage.get("type") != "pyto_chunk_binary":
            return None
        rel = str(storage.get("path", "")).strip()
        if not rel:
            return None
        return rel if os.path.isabs(rel) else os.path.join(os.path.dirname(self.path), rel)

    def _get_terrain_reader(self):
        path = self._binary_terrain_path()
        if path is None:
            return None
        if self.terrain_reader is None or self.terrain_reader.path != os.path.abspath(path):
            self.terrain_reader = BinaryTerrainReader(path)
        return self.terrain_reader

    def _layers(self):
        if self.payload is None:
            self.read()

        return self.payload.get(
            "layers",
            {},
        )

    def validate_basic(self):
        """Return (ok, message) for a playable editor map payload.

        This catches the common stale/empty export case before the game starts
        with a sky-only world. It intentionally keeps validation conservative:
        custom sparse maps are allowed as long as at least one terrain tile
        exists and the declared size is usable.
        """
        if self.payload is None:
            self.read()

        if self.payload.get("format") != "pyto_rpg_map":
            return False, "map format 不正確"

        size = self.payload.get("size", [])
        if not isinstance(size, (list, tuple)) or len(size) < 2:
            return False, "map size 缺失"
        try:
            width = int(size[0])
            height = int(size[1])
        except Exception:
            return False, "map size 無法解析"

        if width < 8 or height < 8:
            return False, f"map size 太小：{width}x{height}"

        # V0.7.1 prefers indexed binary terrain. JSON-only V0.7.0 maps stay
        # fully backward compatible and migrate on the next editor export.
        storage = self._terrain_storage()
        if storage and storage.get("type") == "pyto_chunk_binary":
            path = self._binary_terrain_path()
            if path is None or not os.path.isfile(path):
                return False, f"缺少 terrain binary：{path}"
            try:
                reader = self._get_terrain_reader()
            except Exception as exc:
                return False, f"terrain binary 無法讀取：{exc}"
            if reader.width != width or reader.height != height:
                return False, (
                    f"terrain binary size {reader.width}x{reader.height} "
                    f"與 map size {width}x{height} 不一致"
                )
            if reader.non_air_tile_count <= 0:
                return False, "terrain binary 是空的"
            return True, (
                f"{width}x{height}, terrain={reader.non_air_tile_count}, "
                f"chunks={reader.non_empty_chunk_count}"
            )

        terrain = self._layers().get("terrain", [])
        if not isinstance(terrain, list) or len(terrain) <= 0:
            return False, "terrain layer 是空的"

        in_bounds = 0
        for item in terrain:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                tx = int(item[0])
                ty = int(item[1])
            except Exception:
                continue
            if 0 <= tx < width and 0 <= ty < height:
                in_bounds += 1
                break
        if in_bounds <= 0:
            return False, "terrain 有資料但全部超出 map size 範圍"

        return True, f"{width}x{height}, terrain={len(terrain)}"

    def resolve_spawn_tile(self, world):
        """Return a safe feet-on-surface spawn tile for the loaded world."""
        if self.payload is None:
            self.read()

        raw = self.payload.get("player_spawn", [4, 12])
        try:
            wanted_x = int(raw[0])
            wanted_y = int(raw[1])
        except Exception:
            wanted_x, wanted_y = 4, 12

        wanted_x = max(0, min(int(world.width_tiles) - 1, wanted_x))
        wanted_y = max(2, min(int(world.height_tiles) - 1, wanted_y))

        def standable(tx, ty):
            if not (0 <= tx < world.width_tiles and 2 <= ty < world.height_tiles):
                return False
            if not tile_def(world.get_tile(tx, ty)).solid:
                return False
            # Player is 54 px high while tiles are 40 px; reserve two rows of
            # head room so a malformed spawn cannot begin inside terrain.
            return (
                not tile_def(world.get_tile(tx, ty - 1)).solid
                and not tile_def(world.get_tile(tx, ty - 2)).solid
            )

        if standable(wanted_x, wanted_y):
            return wanted_x, wanted_y

        # Prefer nearby columns around the authored spawn. first_solid_row() is
        # cached by TileWorld, so this is cheap even for a deep generated map.
        max_radius = max(world.width_tiles, 1)
        for radius in range(max_radius):
            xs = (wanted_x,) if radius == 0 else (wanted_x - radius, wanted_x + radius)
            for tx in xs:
                if tx < 0 or tx >= world.width_tiles:
                    continue
                surface = world.first_solid_row(tx)
                if surface is not None and standable(tx, int(surface)):
                    return int(tx), int(surface)

        # Last-resort bounded scan. This should only happen on unusual maps.
        for tx in range(world.width_tiles):
            for ty in range(2, world.height_tiles):
                if standable(tx, ty):
                    return int(tx), int(ty)

        # No standable tile exists; retain a bounded coordinate. GameApp will
        # reject truly empty terrain earlier via validate_basic().
        return wanted_x, wanted_y

    def map_summary(self):
        if self.payload is None:
            self.read()
        size = self.payload.get("size", [0, 0])
        layers = self._layers()
        meta = self.payload.get("metadata", {})
        reader = None
        try:
            reader = self._get_terrain_reader()
        except Exception:
            reader = None
        terrain_count = (
            int(reader.non_air_tile_count)
            if reader is not None
            else len(layers.get("terrain", []))
        )
        return {
            "name": str(meta.get("name", "editor_map")),
            "generated": bool(meta.get("generated", False)),
            "seed": meta.get("seed"),
            "width": int(size[0]) if len(size) > 0 else 0,
            "height": int(size[1]) if len(size) > 1 else 0,
            "terrain": terrain_count,
            "terrain_chunks": 0 if reader is None else int(reader.non_empty_chunk_count),
            "binary": reader is not None,
            "water": len(layers.get("water", [])),
            "lava": len(layers.get("lava", [])),
            "honey": len(layers.get("honey", [])),
            "custom_map": len(layers.get("custom_map", [])),
            "biomes": len(meta.get("biome_spans", [])) if isinstance(meta.get("biome_spans", []), list) else 0,
        }

    def apply_terrain(
        self,
        world,
    ):
        if self.payload is None:
            self.read()

        size = self.payload.get(
            "size",
            [world.width_tiles, world.height_tiles],
        )
        width = int(size[0])
        height = int(size[1])

        reader = self._get_terrain_reader()
        if reader is not None:
            # Runtime palette mapping is created once. Every subsequent chunk
            # activation converts packed storage codes directly to runtime IDs.
            code_to_tile = [
                TERRAIN_NAME_TO_ID.get(str(name), AIR)
                for name in reader.palette
            ]
            world.reset()
            world.attach_binary_backing(reader, code_to_tile)
            try:
                world.configure_rainforest_tree_system(
                    (self.payload.get("metadata", {}) or {}).get(
                        "rainforest_tree_system", {}
                    )
                )
            except Exception:
                world.configure_rainforest_tree_system({})
            world.configure_cloud_islands(self.payload.get("metadata", {}))
            return

        # Legacy JSON-only map path.
        world.width_tiles = width
        world.height_tiles = height
        world.reset()
        world.width_tiles = width
        world.height_tiles = height
        world._ensure_surface_cache()

        for item in self._layers().get("terrain", []):
            if len(item) < 3:
                continue
            tx, ty, name = item[:3]
            world.set_tile(
                int(tx),
                int(ty),
                TERRAIN_NAME_TO_ID.get(str(name), AIR),
                track_change=False,
            )

        try:
            world.configure_rainforest_tree_system(
                (self.payload.get("metadata", {}) or {}).get(
                    "rainforest_tree_system", {}
                )
            )
        except Exception:
            world.configure_rainforest_tree_system({})
        world.configure_cloud_islands(self.payload.get("metadata", {}))

    def player_spawn_world(
        self,
        world=None,
    ):
        if self.payload is None:
            self.read()

        if world is not None:
            spawn = self.resolve_spawn_tile(world)
        else:
            spawn = self.payload.get(
                "player_spawn",
                [4, 12],
            )

        return (
            (float(spawn[0]) + 0.5) * TILE_SIZE,
            float(spawn[1]) * TILE_SIZE,
        )

    def _stabilize_authored_water_substrate(self, game, layers, env):
        """Seed established map water into hydrologic equilibrium at load.

        Only water explicitly authored in the map participates.  Runtime rain,
        waterballs and later floods still infiltrate normally.  Saturation walks
        downward through contiguous porous ground and stops immediately at air,
        any non-soil solid, or FIX36 impermeable rock/ore/igneous material.
        """
        authored=set()
        for item in layers.get("water",[]) or ():
            if not isinstance(item,(list,tuple)) or len(item)<3:
                continue
            try:
                tx=int(item[0]);ty=int(item[1]);amount=float(item[2])
            except Exception:
                continue
            if amount>1e-6:
                authored.add((tx,ty))
        seeded=0
        h=int(game.world.height_tiles)
        for tx,ty in tuple(authored):
            # Only the bottom cell of each authored standing-water column owns
            # the substrate below it.
            if (tx,ty+1) in authored:
                continue
            sy=ty+1
            while 0<=sy<h:
                tile_id=int(game.world.get_tile(tx,sy))
                if tile_id not in LAYERED_GROUND_TILES:
                    break
                mask=int(env.soil_layers.get((tx,sy),SOIL_LAYER_FULL))
                capacity=max(0.0,min(1.0,soil_capacity_ratio(mask)))
                if capacity<=1e-9:
                    break
                old=env.soil.get((tx,sy))
                fertility=float(getattr(old,"fertility",tile_def(tile_id).fertility))
                plant_cap=int(getattr(old,"plant_cap",3))
                before=float(getattr(old,"moisture",0.0))
                if before+1e-9<capacity:
                    env.soil[(tx,sy)]=SoilCell(
                        moisture=capacity,
                        fertility=max(0.0,fertility),
                        plant_cap=max(1,plant_cap),
                    )
                    seeded+=1
                sy+=1
        try:
            game.authored_water_saturated_cells=int(seeded)
        except Exception:
            pass
        return seeded

    def apply_environment(
        self,
        game,
    ):
        layers = self._layers()
        env = game.environment.state

        # --------------------------------------------------------------
        # Water
        # --------------------------------------------------------------
        for item in layers.get(
            "water",
            [],
        ):
            if len(
                item
            ) < 3:
                continue

            tx, ty, amount = item[:3]

            env.set_water(
                int(
                    tx
                ),
                int(
                    ty
                ),
                max(
                    0.0,
                    min(
                        1.0,
                        float(
                            amount
                        ),
                    ),
                ),
            )

        # --------------------------------------------------------------
        # FIX35 dynamic lava. New maps store a dedicated liquid layer; legacy
        # FIX32-34 hazard=lava entries are migrated below.
        # --------------------------------------------------------------
        for item in layers.get("lava", []):
            if len(item) < 3: continue
            try: env.set_lava(int(item[0]),int(item[1]),max(0.0,min(1.0,float(item[2]))))
            except Exception: pass

        # Honey is a separate persistent liquid and intentionally has no
        # temperature/phase metadata.
        for item in layers.get("honey", []):
            if len(item) < 3: continue
            try: env.set_honey(int(item[0]),int(item[1]),max(0.0,min(1.0,float(item[2]))))
            except Exception: pass

        # --------------------------------------------------------------
        # Fire
        # --------------------------------------------------------------
        for item in layers.get(
            "fire",
            [],
        ):
            if len(
                item
            ) < 3:
                continue

            tx, ty, level = item[:3]
            level = max(
                1,
                min(
                    3,
                    int(
                        level
                    ),
                ),
            )

            intensity = (
                0.46
                if level == 1
                else (
                    0.73
                    if level == 2
                    else 1.0
                )
            )

            temp_c = (
                560.0
                if level == 1
                else (
                    680.0
                    if level == 2
                    else 790.0
                )
            )

            env.fire[
                (
                    int(
                        tx
                    ),
                    int(
                        ty
                    ),
                )
            ] = FireCell(
                intensity=intensity,
                fuel=1.0,
                temperature=1.0,
                temperature_c=temp_c,
                source="map_editor",
                stack_level=level,
            )

        # --------------------------------------------------------------
        # Vegetation
        # --------------------------------------------------------------
        for item in layers.get(
            "vegetation",
            [],
        ):
            if len(
                item
            ) < 4:
                continue

            tx, ty, kind, level = item[:4]
            level = max(
                1,
                min(
                    3,
                    int(
                        level
                    ),
                ),
            )

            kind_name = str(kind)
            species = {
                "tree": "map_tree",
                "grass": "map_grass",
                "cactus": "map_cactus",
                "reed": "map_reed",
                "jungle_tree": "map_jungle_tree",
                "fern": "map_fern",
                "pine": "map_pine",
            }.get(kind_name, "map_grass")

            tree_like = species in (
                "map_tree", "map_jungle_tree", "map_pine"
            )
            biomass = (0.16 + 0.09 * level) if tree_like else (
                0.10 if species == "map_cactus" else 0.05
            )

            env.plants[
                (
                    int(
                        tx
                    ),
                    int(
                        ty
                    ),
                )
            ] = PlantCell(
                species=species,
                growth=0.0,
                stage=level,
                fruit=0,
                alive=True,
                biomass=biomass,
                max_stage=level,
                state="normal",
            )

        # --------------------------------------------------------------
        # Biome -> climate map
        # --------------------------------------------------------------
        meta = self.payload.get("metadata", {}) if isinstance(self.payload, dict) else {}

        # FIX45 map-level physical contracts. These sets point at ordinary
        # terrain cells, so rendering, collision, climbing and mining all read
        # one authored geometry. A map must opt in explicitly to world wrap.
        try:
            game.world.indestructible_cells = {
                (int(row[0]), int(row[1]))
                for row in meta.get("indestructible_terrain_cells", ())
                if isinstance(row, (list, tuple)) and len(row) >= 2
            }
            game.world.no_climb_cells = {
                (int(row[0]), int(row[1]))
                for row in meta.get("no_climb_terrain_cells", ())
                if isinstance(row, (list, tuple)) and len(row) >= 2
            }
            game.world.horizontal_wrap_enabled = bool(meta.get("horizontal_wrap", False))
        except Exception:
            game.world.indestructible_cells = set()
            game.world.no_climb_cells = set()
            game.world.horizontal_wrap_enabled = False

        # V0.7.7.5 explicit editor fractional-geometry layer. Manual maps can
        # now author LOW/MID/FULL for every ordinary layered solid, matching
        # the gameplay renderer/collision/hydrology geometry instead of only
        # generated soil/sand metadata.
        explicit_layers = layers.get("ground_layer", [])
        explicit_keys = set()
        for item in explicit_layers:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            tx, ty, mask = int(item[0]), int(item[1]), normalize_soil_mask(item[2])
            if mask <= 0:
                continue
            try:
                tile_id = int(game.world.get_tile(tx, ty))
            except Exception:
                continue
            if tile_id in LAYERED_SOLID_TILES:
                env.soil_layers[(tx, ty)] = int(mask)
                explicit_keys.add((tx, ty))

        # V0.7.6.0 authored partial dirt/sand surface geometry. The map keeps
        # dense terrain compact while storing only non-full top masks here.
        for item in meta.get("initial_ground_layers", ()):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            tx, ty, mask = int(item[0]), int(item[1]), normalize_soil_mask(item[2])
            if mask <= 0:
                continue
            try:
                tile_id = int(game.world.get_tile(tx, ty))
            except Exception:
                continue
            if (tx, ty) in explicit_keys:
                continue
            if tile_id in LAYERED_SOLID_TILES:
                env.soil_layers[(tx, ty)] = int(mask)

        # Bottom free-water conduit. Porous layers may recharge this row only
        # vertically; horizontal subsurface movement is performed by the normal
        # liquid solver inside this open channel, never through soil moisture.
        waterway_row = meta.get("underground_waterway_row")
        try:
            waterway_row = int(waterway_row)
        except Exception:
            waterway_row = None
        if waterway_row is not None and 0 <= waterway_row < int(game.world.height_tiles):
            game.world.underground_waterway_row = waterway_row
        else:
            game.world.underground_waterway_row = None

        spans = normalize_biome_spans(
            meta.get("biome_spans"),
            int(game.world.width_tiles),
        )
        game.biome_spans = spans
        chunk_cols = (int(game.world.width_tiles) + CHUNK_SIZE - 1) // CHUNK_SIZE
        chunk_rows = (int(game.world.height_tiles) + CHUNK_SIZE - 1) // CHUNK_SIZE
        for cx in range(chunk_cols):
            sample_tx = min(
                int(game.world.width_tiles) - 1,
                cx * CHUNK_SIZE + CHUNK_SIZE // 2,
            )
            biome_id = biome_at_tile_x(sample_tx, spans, int(game.world.width_tiles))
            profile_id = BIOME_CLIMATE_PROFILE.get(biome_id, "temperate")
            for cy in range(chunk_rows):
                env.climate[(cx, cy)] = profile_id
                env.biome[(cx, cy)] = biome_id

        # V0.7.6.0 marine sand saturation baseline. Sea sand below mean sea level is
        # already saturated at world load; additional seawater therefore stays
        # above the seabed instead of slowly draining the whole ocean. Beach
        # sand above sea level remains unsaturated and can still absorb rain.
        sea_level = max(3, min(int(game.world.height_tiles) - 4, int(WORLD_GEN_SURFACE_ROW)))
        for start, end, biome_id in spans:
            if str(biome_id) != "ocean":
                continue
            for tx in range(max(0, int(start)), min(int(game.world.width_tiles), int(end))):
                for ty in range(sea_level, int(game.world.height_tiles)):
                    if game.world.get_tile(tx, ty) != SEA_SAND:
                        continue
                    mask = int(env.soil_layers.get((tx, ty), SOIL_LAYER_FULL))
                    env.soil[(tx, ty)] = SoilCell(
                        moisture=max(0.0, min(1.0, soil_capacity_ratio(mask))),
                        fertility=0.0,
                        plant_cap=1,
                    )

        # V0.7.7.3 freshwater lake-bed baseline.  Generated lakes use normal
        # dirt rather than an impermeable stone bathtub.  Because the authored
        # lake is an established water body, its submerged soil begins at pore
        # saturation; subsequent rain/digging still follows the ordinary rule
        # that pore water moves vertically only and free water spreads only
        # after saturation.
        lake_level = max(3, min(int(game.world.height_tiles) - 4, int(WORLD_GEN_LAKE_SURFACE_ROW)))
        for start, end, biome_id in spans:
            if str(biome_id) != "lake":
                continue
            for tx in range(max(0, int(start)), min(int(game.world.width_tiles), int(end))):
                # Only seed columns that actually belong to the authored lake.
                has_lake_water = any(
                    env.water_amount(tx, wy) > 1e-6
                    for wy in range(max(0, lake_level - 1), min(int(game.world.height_tiles), lake_level + 3))
                )
                if not has_lake_water:
                    continue
                for ty in range(lake_level, int(game.world.height_tiles)):
                    if game.world.get_tile(tx, ty) != DIRT:
                        continue
                    mask = int(env.soil_layers.get((tx, ty), SOIL_LAYER_FULL))
                    capacity = max(0.0, min(1.0, soil_capacity_ratio(mask)))
                    env.soil[(tx, ty)] = SoilCell(
                        moisture=capacity,
                        fertility=max(0.0, tile_def(DIRT).fertility),
                        plant_cap=2,
                    )

        # FIX37 generic startup hydrology baseline. Ocean/lake-specific rules
        # above remain for compatibility, while swamp puddles and manually
        # authored standing water now start with their directly connected porous
        # substrate already saturated instead of visibly draining at t=0.
        self._stabilize_authored_water_substrate(game,layers,env)

        # --------------------------------------------------------------
        # Runtime-only overlays
        # --------------------------------------------------------------
        game.map_runtime.clear()
        try: game.world.custom_solid_cells.clear()
        except Exception: pass

        # AssetEditor-authored map assets are external project metadata so the
        # MapEditor can consume them immediately without rebuilding runtime.zip.
        custom_meta={}
        try:
            project_root=os.path.dirname(os.path.dirname(self.path))
            cpath=os.path.join(project_root,"assets","custom_map_assets.json")
            with open(cpath,"r",encoding="utf-8") as source_file:
                raw=json.load(source_file)
            custom_meta=raw.get("assets",{}) if isinstance(raw,dict) else {}
            if not isinstance(custom_meta,dict): custom_meta={}
        except Exception: custom_meta={}
        game.map_runtime.custom_map_meta=custom_meta
        for item in layers.get("custom_map", []):
            if not isinstance(item,(list,tuple)) or len(item)<3:continue
            tx,ty,aid=int(item[0]),int(item[1]),str(item[2])
            game.map_runtime.custom_map_cells[(tx,ty)]=aid
            meta=custom_meta.get(aid,{}) if isinstance(custom_meta,dict) else {}
        game.world.configure_custom_map_cells(game.map_runtime.custom_map_cells, custom_meta)

        # Continuous magma vents are authored in metadata.
        try:
            map_meta=self.payload.get("metadata",{}) if isinstance(self.payload,dict) else {}
            game.environment.lava.set_sources(map_meta.get("lava_sources",()))
        except Exception: pass

        for item in layers.get(
            "hazard",
            [],
        ):
            if len(
                item
            ) >= 3:
                _hx=int(item[0]); _hy=int(item[1]); _hv=str(item[2])
                if _hv == "lava":
                    # Backward compatibility: old static magma becomes real
                    # liquid the first time the map is loaded in FIX35.
                    env.set_lava(_hx,_hy,max(env.lava_amount(_hx,_hy),1.0))
                else:
                    game.map_runtime.hazards[(_hx,_hy)] = _hv

        for item in layers.get(
            "decoration",
            [],
        ):
            if len(
                item
            ) >= 4:
                game.map_runtime.decorations[
                    (
                        int(
                            item[0]
                        ),
                        int(
                            item[1]
                        ),
                    )
                ] = (
                    str(
                        item[2]
                    ),
                    int(
                        item[3]
                    ),
                )

        game.map_runtime.source_path = self.path
        game.map_runtime.map_name = str(
            self.payload.get(
                "metadata",
                {},
            ).get(
                "name",
                "editor_map",
            )
        )

        # FIX35 collision/render synchronisation. Ground-layer metadata is
        # applied after terrain chunks load; invalidate any renderer geometry
        # cached before those masks/custom collisions were attached. This also
        # removes the rare invisible-wall mismatch seen after cave transitions.
        try:
            game.world.cache_epoch += 1
            game.world.revision += 1
        except Exception: pass
