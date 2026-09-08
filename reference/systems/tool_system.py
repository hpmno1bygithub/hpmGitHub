# -*- coding: utf-8 -*-
"""Basic tool/mining system for V0.5.8.

Tool use is routed by GameApp's action mode. The right stick selects
direction only; the 動作 button decides when to use the selected mode.
"""
import math

from systems.grapple_system import GrappleSystem

from config import (
    TILE_SIZE,
    TOOL_REACH_TILES,
    TOOL_HIT_COOLDOWN,
    TOOL_SHOVEL_POWER,
    TOOL_PICKAXE_POWER,
    TOOL_AXE_POWER,
    AXE_FRONT_REACH_TILES,
)
from world.soil_layers import (
    SOIL_LAYER_FULL, next_layer_for_downward, next_layer_for_upward,
    soil_capacity_ratio, water_distribution,
)
from world.tile_registry import (
    AIR,
    DIRT,
    GRASS_DIRT,
    MUD,
    SWAMP_SOIL,
    SAND,
    SEA_SAND,
    LAYERED_GROUND_TILES,
    LAYERED_SOLID_TILES,
    JUNGLE_SOIL,
    SNOW_DIRT,
    VILLAGE_PATH,
    ASH,
    STONE,
    COPPER_ORE,
    IRON_ORE,
    GOLD_ORE,
    MARBLE,
    LIMESTONE,
    IGNEOUS_ROCK,
    ICE,
    WOOD,
    LADDER,
    RAINFOREST_TRUNK,
    RAINFOREST_BRANCH,
    RAINFOREST_VINE,
    RAINFOREST_CANOPY,
    RAINFOREST_ROOT,
    RAINFOREST_TREE_TILES,
    HONEYCOMB,
    tile_def,
)


class ToolSystem:
    TOOL_SHOVEL = "shovel"
    TOOL_PICKAXE = "pickaxe"
    TOOL_AXE = "axe"
    TOOL_GRAPPLE = "grapple"

    # V0.7.2.1: shovel functionality is merged into the pickaxe.  Keep the
    # legacy constant so old saves/scripts do not break, but it is no longer a
    # selectable tool.
    ORDER = (
        TOOL_PICKAXE,
        TOOL_AXE,
        TOOL_GRAPPLE,
    )

    NAMES = {
        TOOL_SHOVEL: "鎬",
        TOOL_PICKAXE: "鎬",
        TOOL_AXE: "斧頭",
        TOOL_GRAPPLE: "鉤爪",
    }

    SOIL_TILES = {
        DIRT,
        GRASS_DIRT,
        MUD,
        SWAMP_SOIL,
        SAND,
        SEA_SAND,
        JUNGLE_SOIL,
        SNOW_DIRT,
        VILLAGE_PATH,
        ASH,
    }

    ROCK_TILES = {
        STONE,
        COPPER_ORE,
        IRON_ORE,
        GOLD_ORE,
        MARBLE,
        LIMESTONE,
        IGNEOUS_ROCK,
        ICE,
        HONEYCOMB,
    }

    WOOD_TILES = {
        WOOD,
        LADDER,
        RAINFOREST_TRUNK,
        RAINFOREST_BRANCH,
        RAINFOREST_VINE,
        RAINFOREST_CANOPY,
        RAINFOREST_ROOT,
    }

    # Tool-hit durability. This is gameplay durability, not material mass.
    HARDNESS = {
        DIRT: 1.0,
        GRASS_DIRT: 1.0,
        MUD: 0.8,
        SWAMP_SOIL: 1.1,
        SAND: 0.65,
        SEA_SAND: 0.60,
        JUNGLE_SOIL: 1.05,
        SNOW_DIRT: 1.15,
        VILLAGE_PATH: 1.25,
        ASH: 0.6,
        STONE: 2.0,
        COPPER_ORE: 2.2,
        IRON_ORE: 2.6,
        GOLD_ORE: 2.8,
        MARBLE: 2.5,
        LIMESTONE: 2.1,
        IGNEOUS_ROCK: 2.7,
        ICE: 1.4,
        HONEYCOMB: 1.25,
        WOOD: 1.8,
        LADDER: 1.0,
        RAINFOREST_ROOT: 3.0,
        RAINFOREST_TRUNK: 2.4,
        RAINFOREST_BRANCH: 1.8,
        RAINFOREST_VINE: 0.8,
        RAINFOREST_CANOPY: 0.7,
    }

    @property
    def events(self):
        return self._events

    @events.setter
    def events(self, value):
        # GameApp swaps EventBus ownership after portal/new-game object
        # adoption.  Keep the child hook on that same semantic event channel.
        self._events = value
        child = getattr(self, "grapple", None)
        if child is not None:
            child.events = value

    def __init__(
        self,
        player,
        world,
        environment,
        events,
        target_provider,
        drop_callback=None,
    ):
        self.player = player
        self.world = world
        self.environment = environment
        self.events = events
        self.target_provider = target_provider
        self.drop_callback = drop_callback
        self.last_drop_result = None

        self.selected_tool = self.TOOL_PICKAXE
        self.damage = {}
        self.inventory = {}
        self.cooldown = 0.0
        self.last_result = ""

        # FIX71: one retained hook slot; no projectile list and no per-frame
        # rope objects.  GameApp can render ``grapple_render_state()`` and use
        # the release/mode hooks below without knowing the hook state machine.
        self.grapple = GrappleSystem(
            self.player,
            self.world,
            events=self.events,
        )

        # V0.6.5: GameApp routes the 動作 trigger directly according to the
        # selected action mode.  ToolSystem no longer subscribes globally to
        # every attack event.
        self.action_messages_enabled = False

    @property
    def selected_name(self):
        return self.NAMES.get(
            self.selected_tool,
            "工具",
        )

    @property
    def selected_short_name(self):
        return {
            self.TOOL_SHOVEL: "鎬",
            self.TOOL_PICKAXE: "鎬",
            self.TOOL_AXE: "斧",
            self.TOOL_GRAPPLE: "鉤",
        }.get(
            self.selected_tool,
            "具",
        )

    def toggle_tool(self, steps=1):
        # A rope never survives switching to a mining/chopping tool.
        if self.selected_tool == self.TOOL_GRAPPLE:
            self.grapple.detach("tool_switch", retract=False)
        try:
            index = self.ORDER.index(
                self.selected_tool
            )
        except ValueError:
            index = 0

        try:
            step_count = max(1, int(steps))
        except Exception:
            step_count = 1
        self.selected_tool = self.ORDER[
            (index + step_count) % len(self.ORDER)
        ]

        self.events.emit(
            "message",
            text=(
                "工具切換："
                + self.selected_name
            ),
        )
        return self.selected_name

    def update(self, dt):
        self.cooldown = max(
            0.0,
            self.cooldown - max(0.0, float(dt)),
        )
        self.grapple.update(dt)

    def release_action(self):
        """Hook for the TOOL-mode action-button release edge."""
        if self.selected_tool != self.TOOL_GRAPPLE:
            return False
        return self.grapple.release_action()

    def set_action_mode_active(self, active):
        """Detach on tool-mode exit; call from GameApp.toggle_action_mode()."""
        return self.grapple.set_enabled(bool(active))

    def reset_transients(self):
        """Clear mining damage/cooldown and all in-flight tool presentation."""
        self.damage.clear()
        self.cooldown = 0.0
        self.grapple.reset()

    def grapple_render_state(self, out=None):
        """Bounded rope packet for Metal or another retained renderer."""
        return self.grapple.render_state(out=out)

    def rebind_runtime(
        self,
        player=None,
        world=None,
        environment=None,
        events=None,
        target_provider=None,
    ):
        """Optional stable-owner hook for a future in-place map adoption."""
        if player is not None:
            self.player = player
        if world is not None:
            self.world = world
        if environment is not None:
            self.environment = environment
        if events is not None:
            self.events = events
        if target_provider is not None:
            self.target_provider = target_provider
        self.grapple.rebind(
            player=self.player,
            world=self.world,
            events=self.events,
        )
        return True

    def _on_attack(self, payload):
        if self.cooldown > 0.0 and self.selected_tool != self.TOOL_GRAPPLE:
            return

        target_x, target_y = self.target_provider()
        self.use_at(
            target_x,
            target_y,
        )

    def _distance_to_target(self, tx, ty):
        target_x = (
            tx + 0.5
        ) * TILE_SIZE
        target_y = (
            ty + 0.5
        ) * TILE_SIZE

        player_x = float(self.player.x)
        player_y = (
            float(self.player.y)
            - self.player.height() * 0.45
        )

        return math.hypot(
            target_x - player_x,
            target_y - player_y,
        )

    def _tool_power_for_tile(self, tile_id):
        # Pickaxe now owns both former shovel (soil) and pickaxe (rock/ore)
        # behavior. This removes an unnecessary tool-switch during digging.
        if (
            self.selected_tool in (self.TOOL_PICKAXE, self.TOOL_SHOVEL)
            and tile_id in (self.SOIL_TILES | self.ROCK_TILES)
        ):
            return TOOL_PICKAXE_POWER

        if (
            self.selected_tool == self.TOOL_AXE
            and tile_id in self.WOOD_TILES
        ):
            return TOOL_AXE_POWER

        return 0.0

    def _plant_at_tile(self, tx, ty):
        return self.environment.state.plants.get(
            (int(tx), int(ty))
        )

    def _plant_collision_rect(self, tx, ty, plant):
        """Return the same approximate visible footprint used by magic hits.

        Map vegetation is keyed at its ground/root tile, but tall grass and
        trees extend upward (and tree canopies sideways).  Tool targeting must
        therefore hit the visible plant, not only the root dictionary cell.
        Water, soil moisture and rain intentionally do not participate here.
        """
        if plant is None:
            return None

        species = str(getattr(plant, "species", ""))
        stage = max(1, int(getattr(plant, "stage", 1)))
        center_x = tx * TILE_SIZE + TILE_SIZE * 0.5
        try:
            ground_y = float(self.world.plant_anchor_y(tx, ty, species))
        except Exception:
            ground_y = float(ty * TILE_SIZE)

        if species == "map_grass":
            level = max(1, min(3, stage))
            width = {1: 7.0, 2: 10.0, 3: 13.0}[level]
            height = {1: 11.0, 2: 21.0, 3: 32.0}[level]
            return (
                center_x - width * 0.5,
                ground_y - height,
                center_x + width * 0.5,
                ground_y,
            )

        if species in ("map_tree", "map_jungle_tree", "map_pine"):
            level = max(1, min(3, stage))
            trunk_h = {1: 28.0, 2: 46.0, 3: 68.0}[level]
            canopy_w = {1: 22.0, 2: 34.0, 3: 48.0}[level]
            canopy_h = {1: 15.0, 2: 22.0, 3: 30.0}[level]
            top = ground_y - trunk_h - canopy_h * 0.20
            return (
                center_x - canopy_w * 0.5,
                top,
                center_x + canopy_w * 0.5,
                ground_y,
            )

        if species == "succession":
            if stage <= 1:
                width, height = 7.0, 12.0
            elif stage == 2:
                width, height = 14.0, 24.0
            else:
                width, height = 40.0, 54.0
            return (
                center_x - width * 0.5,
                ground_y - height,
                center_x + width * 0.5,
                ground_y,
            )

        # Generic vegetation fallback.
        return (
            center_x - 6.0,
            ground_y - 16.0,
            center_x + 6.0,
            ground_y,
        )

    def _plant_hit_at_world_point(self, x, y, padding=3.0):
        """Find a visible plant footprint under one sampled ray point."""
        center_tx = int(float(x) // TILE_SIZE)
        center_ty = int(float(y) // TILE_SIZE)
        pad = max(0.0, float(padding))

        # A level-3 tree can reach more than two tiles above its root and one
        # neighboring column sideways, so inspect root cells below/around the
        # sampled point. Sparse dict lookups keep this inexpensive.
        for base_ty in range(center_ty - 1, center_ty + 4):
            for base_tx in range(center_tx - 1, center_tx + 2):
                if not (
                    0 <= base_tx < self.world.width_tiles
                    and 0 <= base_ty < self.world.height_tiles
                ):
                    continue
                plant = self._plant_at_tile(base_tx, base_ty)
                if plant is None:
                    continue
                rect = self._plant_collision_rect(base_tx, base_ty, plant)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if (
                    x1 - pad <= x <= x2 + pad
                    and y1 - pad <= y <= y2 + pad
                ):
                    return base_tx, base_ty
        return None

    def _clear_tile_environment(self, tx, ty, old_tile_id):
        env = self.environment.state
        key = (
            int(tx),
            int(ty),
        )

        # Soil water does not disappear when the containing soil is excavated.
        soil = env.soil.pop(
            key,
            None,
        )
        if soil is not None:
            released = max(
                0.0,
                float(
                    getattr(
                        soil,
                        "moisture",
                        0.0,
                    )
                ),
            )
            if released > 1e-6:
                self.environment.water.deposit(
                    tx,
                    ty,
                    released,
                )

        # Ice water-equivalent is also returned to free water when mined.
        if old_tile_id == ICE:
            ice_mass = max(
                0.0,
                float(
                    env.ice_mass.pop(
                        key,
                        0.0,
                    )
                ),
            )
            if ice_mass > 1e-6:
                self.environment.water.deposit(
                    tx,
                    ty,
                    ice_mass,
                )

        # Dynamic states attached to the removed cell cannot remain floating.
        for table_name in (
            "fire",
            "temperature",
            "water_temperature",
            "ice_melt_progress",
            "water_freeze_progress",
            "ash_wet_time",
            "swamp_wet_time",
            "swamp_dry_time",
            "electric_charge",
            "snow_cover",
            "soil_layers",
        ):
            table = getattr(
                env,
                table_name,
                None,
            )
            if table is not None:
                table.pop(
                    key,
                    None,
                )

    def _soil_layer_exposed(self, tx, ty):
        key = (int(tx), int(ty))
        if key in getattr(self.environment.state, "soil_layers", {}):
            return True
        above_air = int(ty) <= 0 or self.world.get_tile(int(tx), int(ty)-1) == AIR
        below_air = int(ty)+1 >= self.world.height_tiles or self.world.get_tile(int(tx), int(ty)+1) == AIR
        return bool(above_air or below_air)

    def _soil_dig_axis(self, tx, ty):
        px = float(self.player.x)
        py = float(self.player.y) - self.player.height()*0.45
        cx = (int(tx)+0.5)*TILE_SIZE
        cy = (int(ty)+0.5)*TILE_SIZE
        dx, dy = cx-px, cy-py
        if abs(dy) > abs(dx)*0.88:
            return "down" if dy > 0.0 else "up"
        return "horizontal"

    def _release_soil_layer_water(self, tx, ty, amount):
        amount = max(0.0, float(amount))
        if amount <= 1e-12:
            return 0.0
        below_y = int(ty)+1
        if below_y < self.world.height_tiles:
            below_td = tile_def(self.world.get_tile(int(tx), below_y))
            if below_td.solid and below_td.water_absorption > 0.0:
                from world.environment_state import SoilCell
                target = self.environment.state.soil.setdefault(
                    (int(tx), below_y),
                    SoilCell(moisture=0.0, fertility=max(0.0, below_td.fertility)),
                )
                capacity = max(0.0, 1.0-float(target.moisture))
                moved = min(amount, capacity)
                target.moisture += moved
                amount -= moved
            elif not below_td.solid:
                deposited = self.environment.water.deposit(int(tx), below_y, amount)
                amount = max(0.0, amount-float(deposited))
        # FIX36: never bypass an impermeable solid by jumping straight to the
        # underground-waterway reservoir.  If the removed layer cannot drain
        # into the cell immediately below, return it to visible free water
        # above when possible.
        if amount > 1e-12 and int(ty) > 0 and self.world.get_tile(int(tx), int(ty)-1) == AIR:
            self.environment.water.deposit(int(tx), int(ty)-1, amount)
            amount = 0.0
        return amount

    def _mine_exposed_soil_layer(self, tx, ty, tile_id, direction):
        if int(tile_id) not in LAYERED_SOLID_TILES:
            return False
        if direction not in ("up", "down") or not self._soil_layer_exposed(tx, ty):
            return False
        env = self.environment.state
        key = (int(tx), int(ty))
        mask = int(env.soil_layers.get(key, SOIL_LAYER_FULL)) & SOIL_LAYER_FULL
        # Capture the untouched fractional surface before a geometry-only layer
        # edit changes soil_layers. This keeps the back wall hidden behind the
        # original 1/3 or 2/3 foreground until that layer is actually mined.
        try:
            self.world.remember_backdrop_surface(int(tx))
        except Exception:
            pass
        bit = next_layer_for_downward(mask) if direction == "down" else next_layer_for_upward(mask)
        if not bit:
            return False

        soil = env.soil.get(key)
        if soil is not None:
            dist = water_distribution(float(getattr(soil, "moisture", 0.0)), mask, saturation=1.0)
            released = float(dist.get(bit, 0.0))
            soil.moisture = max(0.0, float(soil.moisture)-released)
            if released > 1e-12:
                self._release_soil_layer_water(tx, ty, released)

        new_mask = mask & ~bit
        if new_mask:
            env.soil_layers[key] = int(new_mask)
            # Removing the top grass segment exposes ordinary dirt beneath it.
            if int(tile_id) == GRASS_DIRT and bit == 4:
                self.world.set_tile(int(tx), int(ty), DIRT, track_change=True)
                tile_id = DIRT
            else:
                self.world.mark_tile_geometry_dirty(int(tx), int(ty))
            if soil is not None:
                cap = max(0.0, soil_capacity_ratio(new_mask))
                if float(soil.moisture) > cap:
                    excess = float(soil.moisture)-cap
                    soil.moisture = cap
                    self._release_soil_layer_water(tx, ty, excess)
            self.cooldown = TOOL_HIT_COOLDOWN
            if self.action_messages_enabled:
                self.events.emit("message", text="分層挖掘：移除一層圖塊")
            return True

        env.soil_layers.pop(key, None)
        self._collect_material(tile_id, tx, ty)
        self.world.set_tile(int(tx), int(ty), AIR, track_change=True)
        self._clear_tile_environment(tx, ty, tile_id)
        # Underground mining creates a dry air pocket. If this exact opening is
        # already touching free water, it floods normally; otherwise preserve
        # the trapped-air cavity instead of letting the ocean teleport through
        # the newly excavated volume (Terraria-style cave behavior).
        env_state = self.environment.state
        touching_water = False
        for nx, ny in ((tx-1,ty),(tx+1,ty),(tx,ty-1),(tx,ty+1)):
            if 0 <= nx < self.world.width_tiles and 0 <= ny < self.world.height_tiles:
                if float(env_state.water_amount(nx, ny)) > 0.02:
                    touching_water = True
                    break
        if hasattr(self.world, "mark_pressurized_air"):
            if touching_water:
                # A real breach joins the whole excavated cavity to the water
                # body. Release any previously pressure-locked neighboring air
                # cells so left/right water can actually merge.
                try:
                    self.world.release_pressurized_air_component(tx, ty)
                except Exception:
                    pass
                self.world.mark_pressurized_air(tx, ty, False)
            else:
                self.world.mark_pressurized_air(tx, ty, True)
        self.cooldown = TOOL_HIT_COOLDOWN
        return True

    def _spawn_drop(self, item_id, name, count, x, y):
        if int(count) <= 0:
            return None
        if self.drop_callback is not None:
            result = self.drop_callback(
                str(item_id), str(name), int(count), float(x), float(y)
            )
            # FIX70 keeps the explicit accepted/rejected report reachable for
            # diagnostics without coupling ToolSystem to a concrete drop type.
            self.last_drop_result = result
            return result
        # Legacy fallback used only by headless tests that do not install the
        # world-drop bridge.
        self.inventory[str(name)] = int(self.inventory.get(str(name), 0)) + int(count)
        return None

    def _collect_material(self, tile_id, tx, ty):
        drops = {
            DIRT: ("soil", "土壤", 1),
            GRASS_DIRT: ("soil", "土壤", 1),
            MUD: ("mud", "淤泥", 1),
            SWAMP_SOIL: ("swamp_soil", "沼澤土", 1),
            SAND: ("sand", "沙", 1),
            SEA_SAND: ("sea_sand", "海沙", 1),
            JUNGLE_SOIL: ("jungle_soil", "雨林土", 1),
            SNOW_DIRT: ("snow_soil", "雪地土", 1),
            VILLAGE_PATH: ("packed_dirt", "夯土", 1),
            ASH: ("ash", "灰燼", 1),
            STONE: ("stone", "石塊", 1),
            COPPER_ORE: ("copper_ore", "銅礦石", 1),
            IRON_ORE: ("iron_ore", "鐵礦石", 1),
            GOLD_ORE: ("gold_ore", "金礦石", 1),
            MARBLE: ("marble", "大理石", 1),
            LIMESTONE: ("limestone", "灰岩", 1),
            IGNEOUS_ROCK: ("igneous_rock", "火成岩", 1),
            ICE: ("ice_chunk", "冰塊", 1),
            HONEYCOMB: ("honeycomb", "蜂巢", 1),
            WOOD: ("wood", "木材", 1),
            LADDER: ("wood", "木材", 1),
            RAINFOREST_ROOT: ("wood", "木材", 3),
            RAINFOREST_TRUNK: ("wood", "木材", 2),
            RAINFOREST_BRANCH: ("wood", "木材", 1),
            RAINFOREST_VINE: ("vine_fiber", "藤纖維", 1),
            RAINFOREST_CANOPY: ("leaf", "樹葉", 1),
        }
        item_id, name, count = drops.get(
            int(tile_id),
            ("material_%d" % int(tile_id), tile_def(tile_id).name, 1),
        )
        self._spawn_drop(
            item_id, name, count,
            (int(tx) + 0.5) * TILE_SIZE,
            (int(ty) + 0.45) * TILE_SIZE,
        )

    def _chop_rainforest_tree_segment(self, tx, ty):
        """Chop one structural segment and safely collapse its dependants.

        TileWorld returns the sparse support subtree. Drops are aggregated into
        no more than two world items so cutting a tall tree cannot create a
        burst of dozens of UIKit/physics objects on iPhone.
        """
        try:
            removed = self.world.remove_rainforest_tree_segment(
                int(tx), int(ty), track_change=True
            )
        except Exception:
            removed = tuple()
        if not removed:
            return False

        wood_count = sum(max(0, int(row.get("wood_yield", 0))) for row in removed)
        leaf_count = sum(max(0, int(row.get("leaf_yield", 0))) for row in removed)
        for row in removed:
            key = (int(row.get("x", tx)), int(row.get("y", ty)))
            self.damage.pop(key, None)
            self._clear_tile_environment(key[0], key[1], int(row.get("tile_id", AIR)))

        drop_x = (int(tx) + 0.5) * TILE_SIZE
        drop_y = (int(ty) + 0.35) * TILE_SIZE
        if wood_count:
            self._spawn_drop("wood", "木材", wood_count, drop_x, drop_y)
        if leaf_count:
            self._spawn_drop("leaf", "樹葉", leaf_count, drop_x + 7.0, drop_y - 3.0)
        self.cooldown = TOOL_HIT_COOLDOWN
        if self.action_messages_enabled:
            self.events.emit(
                "message",
                text=f"砍除巨木分段：{len(removed)} 段失去支撐",
            )
        return True

    def _remove_plant(self, tx, ty, force_axe=False):
        plant = self._plant_at_tile(tx, ty)
        if plant is None:
            return False

        species = str(getattr(plant, "species", ""))
        stage = max(1, int(getattr(plant, "stage", 1)))
        is_tree = (species in ("map_tree", "map_jungle_tree", "map_pine") or (species == "succession" and stage >= 3))

        allowed = bool(force_axe) or (
            self.selected_tool == self.TOOL_AXE
            if is_tree
            else self.selected_tool in (self.TOOL_PICKAXE, self.TOOL_SHOVEL, self.TOOL_AXE)
        )
        if not allowed:
            return False

        # V0.7.2.4: plants become physical world drops, not hidden tool inventory.
        # Some stage-2 surface vegetation is deterministically treated as a
        # berry bush so the same map coordinates always yield the same forage.
        fruit = max(0, int(getattr(plant, "fruit", 0)))
        berry_bush = (
            not is_tree
            and species != "moss"
            and stage >= 2
            and (((int(tx) * 92821) ^ (int(ty) * 68917)) % 7 == 0)
        )
        drop_x = (int(tx) + 0.5) * TILE_SIZE
        drop_y = int(ty) * TILE_SIZE - 2.0
        if is_tree:
            self._spawn_drop("wood", "木材", max(2, stage * 2), drop_x, drop_y)
            self._spawn_drop("leaf", "樹葉", max(1, stage - 1), drop_x + 6.0, drop_y)
            if species == "map_pine":
                self._spawn_drop("pine_cone", "松果", max(1, stage - 1), drop_x - 5.0, drop_y)
            if fruit > 0:
                self._spawn_drop("fruit", "果實", fruit, drop_x - 6.0, drop_y)
        elif species == "map_cactus":
            self._spawn_drop("cactus_flesh", "仙人掌肉", max(1, stage), drop_x, drop_y)
        elif species == "map_reed":
            self._spawn_drop("reed", "蘆葦", max(1, stage), drop_x, drop_y)
        elif species == "map_fern":
            self._spawn_drop("leaf", "樹葉", max(1, stage), drop_x, drop_y)
        elif species == "moss":
            self._spawn_drop("moss", "青苔", 1, drop_x, drop_y)
        else:
            self._spawn_drop("leaf", "樹葉", 1, drop_x, drop_y)
            if berry_bush or fruit > 0:
                self._spawn_drop("berry", "莓果", max(1, fruit), drop_x + 5.0, drop_y)

        self.environment.state.plants.pop((int(tx), int(ty)), None)
        self.cooldown = TOOL_HIT_COOLDOWN
        if self.action_messages_enabled:
            self.events.emit(
                "message",
                text=("砍除樹木" if is_tree else "清除植物"),
            )
        return True

    @staticmethod
    def _rect_overlap(a, b):
        return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

    def _axe_melee_target(self):
        """Nearest chop target exactly one tile in front of the player.

        The aiming reticle is not consulted at all.  Reach begins at the front
        edge of the character body and ends one TILE_SIZE farther forward.
        This is the same directional melee convention used by swords.
        """
        p = self.player
        facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
        body_front = p.x + facing * (p.width() * 0.5)
        reach_px = TILE_SIZE * float(AXE_FRONT_REACH_TILES)
        far_edge = body_front + facing * reach_px
        hitbox = (
            min(body_front, far_edge),
            p.y - p.height() * 0.92,
            max(body_front, far_edge),
            p.y + 2.0,
        )

        candidates = []
        for (tx, ty), plant in tuple(self.environment.state.plants.items()):
            root_x = (int(tx) + 0.5) * TILE_SIZE
            if (root_x - p.x) * facing <= 0.0:
                continue
            species = str(getattr(plant, "species", ""))
            stage = max(1, int(getattr(plant, "stage", 1)))
            is_tree = (species in ("map_tree", "map_jungle_tree", "map_pine") or (species == "succession" and stage >= 3))
            if is_tree:
                ground_y = int(ty) * TILE_SIZE
                trunk_h = (
                    {1: 28.0, 2: 46.0, 3: 68.0}.get(min(3, stage), 68.0)
                    if species in ("map_tree", "map_jungle_tree", "map_pine") else 48.0
                )
                # Trees are only hittable through the narrow trunk.  Canopy
                # width never extends axe reach.
                rect = (root_x - 7.0, ground_y - trunk_h, root_x + 7.0, ground_y)
            else:
                rect = self._plant_collision_rect(int(tx), int(ty), plant)
            if rect is not None and self._rect_overlap(hitbox, rect):
                candidates.append((abs(root_x - p.x), int(tx), int(ty)))

        if candidates:
            candidates.sort(key=lambda row: row[0])
            return candidates[0][1], candidates[0][2]

        min_tx = max(0, int(hitbox[0] // TILE_SIZE) - 1)
        max_tx = min(self.world.width_tiles - 1, int(hitbox[2] // TILE_SIZE) + 1)
        min_ty = max(0, int(hitbox[1] // TILE_SIZE) - 1)
        max_ty = min(self.world.height_tiles - 1, int(hitbox[3] // TILE_SIZE) + 1)
        tile_candidates = []
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                tile_id = self.world.get_tile(tx, ty)
                if tile_id not in self.WOOD_TILES:
                    continue
                rect = (tx*TILE_SIZE, ty*TILE_SIZE, (tx+1)*TILE_SIZE, (ty+1)*TILE_SIZE)
                if not self._rect_overlap(hitbox, rect):
                    continue
                cx = (tx + 0.5) * TILE_SIZE
                if (cx - p.x) * facing <= 0.0:
                    continue
                tile_candidates.append((abs(cx - p.x), tx, ty))
        if tile_candidates:
            tile_candidates.sort(key=lambda row: row[0])
            return tile_candidates[0][1], tile_candidates[0][2]
        return None

    def heavy_axe_clear_plants(self, hitbox):
        """Heavy axe: clear every tree/grass plant touched by the horizontal arc."""
        removed = 0
        for (tx, ty), plant in tuple(self.environment.state.plants.items()):
            try:
                rect = self._plant_collision_rect(int(tx), int(ty), plant)
            except Exception:
                rect = None
            if rect is None:
                # Trees intentionally use a narrow trunk collision for normal
                # chopping; heavy cleave is allowed to touch the whole visible
                # root column without needing the currently selected tool.
                root_x = (int(tx) + 0.5) * TILE_SIZE
                stage = max(1, int(getattr(plant, "stage", 1)))
                species = str(getattr(plant, "species", ""))
                if species in ("map_tree", "map_jungle_tree", "map_pine") or (species == "succession" and stage >= 3):
                    ground_y = int(ty) * TILE_SIZE
                    trunk_h = {1: 28.0, 2: 46.0, 3: 68.0}.get(min(3, stage), 68.0)
                    rect = (root_x - 8.0, ground_y - trunk_h, root_x + 8.0, ground_y)
            if rect is not None and self._rect_overlap(hitbox, rect):
                if self._remove_plant(int(tx), int(ty), force_axe=True):
                    removed += 1
        return removed

    def heavy_hammer_remove_ground_layer(self, tx, approx_ty):
        """Heavy hammer removes exactly one exposed terrain third at impact."""
        tx = int(tx)
        approx_ty = int(approx_ty)
        if tx < 0 or tx >= self.world.width_tiles:
            return False
        # Search around the player's feet and choose the first sky/cavity-facing
        # layered solid.  This makes the effect stable on LOW/MID/HIGH slopes.
        candidates = []
        for ty in range(max(0, approx_ty - 2), min(self.world.height_tiles, approx_ty + 4)):
            tile_id = self.world.get_tile(tx, ty)
            if tile_id not in LAYERED_SOLID_TILES:
                continue
            if not self._soil_layer_exposed(tx, ty):
                continue
            candidates.append((abs(ty - approx_ty), ty, tile_id))
        if not candidates:
            return False
        _dist, ty, tile_id = min(candidates, key=lambda row: (row[0], row[1]))
        # Ground slam always attacks the top third, independent of reticle axis.
        return bool(self._mine_exposed_soil_layer(tx, ty, tile_id, "down"))

    def blast_remove_tiles(self, center_tx, center_ty, width=1):
        """Destroy the requested TNT terrain footprint without mining drops.

        Normal TNT targets exactly one storage cell. Heavy TNT targets the full
        3x3 square centred on impact (nine cells).  Dynamic water/lava mass is
        not deleted: opening the solid cell lets the existing environment
        simulation flow into it.  Soil/ice/fire metadata attached to the former
        solid is cleaned through the same path as ordinary mining.
        """
        try:
            width = int(width)
        except Exception:
            width = 1
        width = 3 if width >= 3 else 1
        center_tx = int(center_tx)
        center_ty = int(center_ty)
        radius = 1 if width == 3 else 0
        pattern = []
        removed_tiles = 0
        removed_plants = 0
        removed_cells = []

        for ty in range(center_ty - radius, center_ty + radius + 1):
            for tx in range(center_tx - radius, center_tx + radius + 1):
                if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
                    continue
                pattern.append((int(tx), int(ty)))

                # Root-keyed vegetation is removed without generating a large
                # burst of item drops.  Tile-backed rainforest segments still
                # use TileWorld's structural-prune rule when their support is
                # destroyed, which prevents floating collision branches.
                try:
                    if self.environment.state.plants.pop((int(tx), int(ty)), None) is not None:
                        removed_plants += 1
                except Exception:
                    pass

                tile_id = int(self.world.get_tile(tx, ty))
                # Only authored solid terrain counts as a blastable "tile".
                # Water/lava/air are sparse environment mass rather than wall
                # blocks; deleting those here would violate conservation and
                # make TNT silently erase fluids instead of opening a cavity.
                if tile_id == AIR or not bool(tile_def(tile_id).solid):
                    continue
                self.damage.pop((int(tx), int(ty)), None)
                self.world.set_tile(tx, ty, AIR, track_change=True)
                if tile_id == HONEYCOMB:
                    try:
                        self.environment.honey.deposit(tx, ty, 0.82)
                    except Exception:
                        pass
                self._clear_tile_environment(tx, ty, tile_id)
                # A freshly blasted opening is connected to the local air/water
                # simulation rather than remaining a stale pressure-locked cell.
                try:
                    self.world.mark_pressurized_air(tx, ty, False)
                except Exception:
                    pass
                removed_tiles += 1
                removed_cells.append((int(tx), int(ty), tile_id))

        if removed_tiles or removed_plants:
            try:
                self.events.emit(
                    "terrain_blast",
                    center_tx=center_tx,
                    center_ty=center_ty,
                    width=width,
                    removed_tiles=removed_tiles,
                    removed_plants=removed_plants,
                )
            except Exception:
                pass
        return {
            "width": width,
            "pattern_cells": tuple(pattern),
            "removed_tiles": int(removed_tiles),
            "removed_plants": int(removed_plants),
            "removed_cells": tuple(removed_cells),
        }

    def _ray_target_tile(self, world_x, world_y):
        start_x = float(self.player.x)
        start_y = (
            float(self.player.y)
            - self.player.height() * 0.45
        )
        dx = float(world_x) - start_x
        dy = float(world_y) - start_y
        length = math.hypot(dx, dy)

        if length < 1e-6:
            dx = float(self.player.facing or 1)
            dy = 0.0
            length = 1.0

        nx = dx / length
        ny = dy / length
        reach = (
            TOOL_REACH_TILES
            * TILE_SIZE
        )

        # V0.5.8.1:
        # the crosshair is direction-only, so its visible radius must not
        # shorten tool reach. Always cast the tool ray through the full allowed
        # reach and stop at the first plant/solid tile.
        travel = reach

        step = max(
            3.0,
            TILE_SIZE * 0.12,
        )

        last = None
        # Start outside the player's own body/feet so the ray cannot select
        # the floor tile the player is standing in front of.
        distance = min(
            travel,
            max(
                self.player.width() * 0.70,
                TILE_SIZE * 0.62,
            ),
        )
        while distance <= travel + 1e-6:
            x = start_x + nx * distance
            y = start_y + ny * distance
            tx = int(x // TILE_SIZE)
            ty = int(y // TILE_SIZE)
            key = (tx, ty)

            # Plant visuals can extend far outside their root tile. Test the
            # visible footprint on every ray sample before testing solid terrain.
            plant_hit = self._plant_hit_at_world_point(x, y)
            if plant_hit is not None:
                return plant_hit

            if key != last:
                last = key
                if (
                    0 <= tx < self.world.width_tiles
                    and 0 <= ty < self.world.height_tiles
                ):
                    if self.world.get_tile(tx, ty) != AIR:
                        return tx, ty

            distance += step

        end_x = start_x + nx * travel
        end_y = start_y + ny * travel
        return (
            int(end_x // TILE_SIZE),
            int(end_y // TILE_SIZE),
        )

    def _emit_audio_hit(self, tile_id=None, material=None):
        if material is None:
            if tile_id in self.SOIL_TILES:
                material = "soil"
            elif tile_id in self.WOOD_TILES:
                material = "wood"
            else:
                material = "rock"
        try:
            self.events.emit(
                "audio_tool_hit",
                tool=str(self.selected_tool),
                material=str(material),
                tile_id=(-1 if tile_id is None else int(tile_id)),
            )
        except Exception:
            pass

    def use_at(self, world_x, world_y):
        # Grappling is a traversal action, not a mining hit.  It intentionally
        # ignores a leftover pickaxe/axe cooldown after the user switches tools.
        if self.selected_tool == self.TOOL_GRAPPLE:
            return self.grapple.trigger(world_x, world_y)

        if self.cooldown > 0.0:
            return False

        # V0.7.2.4 split: pickaxe/mining keeps reticle-direction reach, while
        # axe/chopping is a genuine close-range action tied to player.facing.
        if self.selected_tool == self.TOOL_AXE:
            target = self._axe_melee_target()
            if target is None:
                self.cooldown = TOOL_HIT_COOLDOWN
                return False
            tx, ty = target
        else:
            tx, ty = self._ray_target_tile(world_x, world_y)

        if (
            tx < 0
            or ty < 0
            or tx >= self.world.width_tiles
            or ty >= self.world.height_tiles
        ):
            return False

        # A plant at the target gets first tool contact before the underlying
        # terrain, matching the projectile first-contact principle.
        if self._remove_plant(
            tx,
            ty,
        ):
            self._emit_audio_hit(material="wood" if self.selected_tool == self.TOOL_AXE else "soil")
            return True

        tile_id = self.world.get_tile(
            tx,
            ty,
        )

        if tile_id == AIR:
            return False

        # FIX46: landmarks use ordinary foreground tiles and obey the same
        # mining rules as natural terrain.  Older FIX45 maps/saves may still
        # carry an ``indestructible_cells`` metadata set, so deliberately do
        # not reject those cells here; this keeps pyramid entrances mineable
        # without requiring the player to discard an existing save.

        power = self._tool_power_for_tile(
            tile_id
        )

        if power <= 0.0:
            if self.action_messages_enabled:
                self.events.emit(
                    "message",
                    text=(
                        f"{self.selected_name}不適合 "
                        f"{tile_def(tile_id).name}"
                    ),
                )
            self.cooldown = TOOL_HIT_COOLDOWN
            return False

        key = (
            tx,
            ty,
        )
        progress = (
            float(
                self.damage.get(
                    key,
                    0.0,
                )
            )
            + float(power)
        )

        hardness = float(
            self.HARDNESS.get(
                tile_id,
                1.0,
            )
        )

        self.cooldown = TOOL_HIT_COOLDOWN
        self._emit_audio_hit(tile_id=tile_id)

        if progress + 1e-9 < hardness:
            self.damage[
                key
            ] = progress
            if self.action_messages_enabled:
                self.events.emit(
                    "message",
                    text=(
                        f"{self.selected_name}："
                        f"{progress:.1f}/{hardness:.1f}"
                    ),
                )
            return True

        self.damage.pop(
            key,
            None,
        )

        if (
            int(tile_id) in RAINFOREST_TREE_TILES
            and self._chop_rainforest_tree_segment(tx, ty)
        ):
            return True

        soil_direction = self._soil_dig_axis(tx, ty)
        if (
            tile_id in LAYERED_SOLID_TILES
            and soil_direction in ("up", "down")
            and self._mine_exposed_soil_layer(tx, ty, tile_id, soil_direction)
        ):
            return True

        self._collect_material(
            tile_id, tx, ty
        )

        self.world.set_tile(
            tx,
            ty,
            AIR,
            track_change=True,
        )

        if int(tile_id) == HONEYCOMB:
            try:
                released = self.environment.honey.deposit(tx, ty, 0.82)
                self.events.emit("message", text="蜂巢破裂：流出蜂蜜 %.2f" % float(released))
            except Exception as exc:
                print("HONEY release warning:", repr(exc))

        self._clear_tile_environment(
            tx,
            ty,
            tile_id,
        )

        # FIX131 burrow habitat trigger.  Layered soil/rock calls reach this
        # point only after the whole cell becomes AIR, so a creature hidden in
        # the block cannot wake on the first 1/3 or 2/3 mining step.
        try:
            self.events.emit("tile_fully_mined", tx=int(tx), ty=int(ty), tile_id=int(tile_id))
        except Exception:
            pass

        if self.action_messages_enabled:
            self.events.emit(
                "message",
                text=(
                    f"挖除 {tile_def(tile_id).name}"
                ),
            )
        return True
