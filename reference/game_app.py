# -*- coding: utf-8 -*-
import os
import math
import random
import threading
import time

from config import (
    TILE_SIZE, CHUNK_SIZE,
    GROUND_ROW,
    CAMERA_VERTICAL_UPPER,
    CAMERA_VERTICAL_LOWER,
    CAMERA_VERTICAL_FOLLOW_SPEED,
    EDITOR_MAP_RELATIVE_PATH,
    MAX_MANA,
    MAX_HP,
    MANA_RECOVERY_PER_SECOND,
    PLAYER_HP_REGEN_DELAY_SECONDS,
    PLAYER_HP_REGEN_PER_SECOND,
    PLAYER_STARTUP_DAMAGE_GRACE_SECONDS,
    PERF_ENVIRONMENT_HZ,
    PERF_ENVIRONMENT_MAX_STEPS,
    AIM_RETICLE_RADIUS,
    AIM_MAGIC_DIRECTION_DISTANCE,
    AIM_DIRECTION_DEADZONE,
    AIM_SOFT_SNAP_TURN_SPEED_DEG,
    AIM_SOFT_SNAP_LOCK_DEG,
    AIM_SOFT_SNAP_EPSILON,
    MAGIC_CHARGE_LEVEL3_MAX,
    ENGINEERING_HUD_ENABLED,
)
from engine.events import EventBus
from engine.game_command_queue import GameCommandQueue
from engine.input_manager import InputManager
from engine.physics import TilePhysics
from engine.scene import Scene
from engine.math2d import clamp
from world.tile_world import TileWorld
from world.tile_registry import tile_def
from world.chunk_streamer import ChunkStreamer
from player.player import Player
from entities.npc import NPC
from entities.item import Item
from entities.chest import Chest
from entities.creature import Creature
from entities.physics_object import PhysicsObject
from systems.magic_system import MagicSystem
from systems.tool_system import ToolSystem
from systems.interaction import InteractionSystem
from systems.save_manager import SaveManager
from systems.environment_manager import EnvironmentManager
from systems.npc_ai_system import NPCAISystem
from systems.toxic_system import ToxicSystem
from systems.physics_object_system import PhysicsObjectSystem
from systems.map_loader import MapLoader, MapRuntimeState
from systems.game_time_system import GameTimeSystem
from systems.inventory_system import InventorySystem
from systems.equipment_system import EquipmentSystem, EQUIPMENT_DEFS, is_equipment_item
from systems.melee_system import MeleeSystem
from systems.drone_system import DroneSystem
from systems.boss_weapon_system import BossWeaponSystem
from systems.weapon_catalog import (
    WEAPON_DEFS, CHEST_WEAPON_ORDER, chest_weapon_for_index,
    weapon_from_item, weapon_item,
)
from systems.lava_spit_system import LavaSpitSystem
from systems.pyramid_trap_system import PyramidTrapSystem
from systems.creature_system import CreatureSystem
from systems.item_drop_system import ItemDropSystem, clean_drop_heavy_uses
from systems.biome_system import BiomeSystem
from systems.asset_registry import AssetRegistry
from systems.custom_creatures import load_custom_creatures
from systems.secondary_world import SecondaryWorldRuntime
from systems.world_topology import WorldTopology
from systems.game_settings import GameSettings
from systems.study_challenge import StudyChallenge
from systems.study_profiles import profile_summary
from systems.playtime_guard import PlaytimeGuard, LIMIT_SECONDS
from systems.portal_geometry import contains_player as portal_contains_player
from systems.portal_geometry import floor_entry_allowed as portal_floor_entry_allowed
from engine.sprites import (
    SpriteRegistry,
    SpriteDef,
    AnimationClip,
    AnimationPlayer,
)


class GameApp:
    """Platform-independent gameplay application."""

    def __init__(
        self,
        use_editor_map=False,
        map_path=None,
        skip_auto_load=False,
        game_settings=None,
    ):
        self.events = EventBus()
        self.input = InputManager()

        # FIX60: UIKit tap/joystick callbacks publish intent only.  Save/load,
        # selection toggles and aim state are consumed by update() on the
        # authoritative gameplay thread instead of mutating GameApp from the
        # iOS main thread.
        self.ui_commands = GameCommandQueue(max_pending=128)

        # UI callbacks run on the iOS main thread while simulation runs on the
        # game thread. Cross-thread spell creation is queued and consumed only
        # by GameApp.update(), avoiding list mutation during MagicSystem.update.
        self._command_lock = threading.RLock()
        self._magic_cast_queue = []

        self.map_runtime = MapRuntimeState()
        self.map_loader = None
        self.custom_map_loaded = False

        self.world = TileWorld()

        if use_editor_map:
            if map_path is None:
                project_root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "").strip()
                if not project_root:
                    project_root = os.path.dirname(os.path.abspath(__file__))
                map_path = None if skip_auto_load else SaveManager.preferred_saved_map_path(project_root)
                if not map_path:
                    map_path = os.path.join(project_root, EDITOR_MAP_RELATIVE_PATH)

            candidate = MapLoader(
                map_path
            )

            if candidate.exists():
                candidate.read()
                map_ok, map_info = candidate.validate_basic()
                if map_ok:
                    candidate.apply_terrain(
                        self.world
                    )
                    self.map_loader = candidate
                    self.custom_map_loaded = True
                    print("MAP LOAD OK:", candidate.map_summary())
                else:
                    print("MAP LOAD REJECTED:", map_info, "path=", candidate.path)

        if not self.custom_map_loaded:
            self.world.build_test_world()

        self.physics = TilePhysics(self.world)

        self.chunk_streamer = ChunkStreamer(
            self.world
        )

        self.scene = Scene("test_world")

        # External asset bindings live outside rpg_runtime.zip so artists can
        # replace PNG/metadata without modifying engine code. Renderer keeps
        # geometric fallbacks when an asset has not been bound yet.
        project_root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "").strip()
        if not project_root:
            project_root = os.path.dirname(os.path.abspath(__file__))
        self.game_settings = (
            game_settings
            if isinstance(game_settings, GameSettings)
            else GameSettings.from_project_root(project_root)
        )
        self.assets = AssetRegistry.from_project_root(project_root)
        # FIX69 secondary maps bind their background, population, actions and
        # attack presentation through one project-local catalog.  The object is
        # pure Python data and is carried naturally by the existing portal
        # GameApp adoption; no UIKit or Metal object is retained here.
        self.secondary_world = SecondaryWorldRuntime.from_map_loader(
            project_root, self.map_loader
        )
        try:
            _map_metadata = dict((self.map_loader.payload or {}).get("metadata", {}) or {}) if self.map_loader else {}
        except Exception:
            _map_metadata = {}
        self.prototype_entities_enabled = bool(
            _map_metadata.get(
                "prototype_entities",
                not bool(getattr(self.secondary_world, "active", False)),
            )
        )
        # FIX32: assets created in the editor can become live creatures without engine edits.
        try:
            from systems.biome_system import CREATURE_ARCHETYPES
            CREATURE_ARCHETYPES.update(load_custom_creatures(project_root))
            self.secondary_world.install_archetypes(CREATURE_ARCHETYPES)
        except Exception as exc:
            print("CUSTOM CREATURE PROFILE warning:", repr(exc))

        # Renderer-neutral sprite / animation registry.
        # V0.7.3 keeps this logical animation registry separate from external PNG bindings.
        self.sprites = SpriteRegistry()

        self.sprites.register_sprite(
            SpriteDef(
                "player_debug",
                frame_width=32,
                frame_height=56,
            )
        )

        for animation_id, fps in (
            ("player_idle", 4.0),
            ("player_walk", 8.0),
            ("player_run", 12.0),
            # FIX15: posture is part of the animation state. Collision height
            # still comes from Player.state, but visuals no longer borrow the
            # ordinary standing walk/run clips while crouched.
            ("player_crouch", 4.0),
            ("player_crouch_walk", 8.0),
            ("player_crouch_run", 11.0),
            ("player_prone", 4.0),
            ("player_crawl", 7.0),
            ("player_roll", 10.0),
            ("player_jump", 6.0),
            ("player_fall", 6.0),
            ("player_climb", 8.0),
        ):
            self.sprites.register_animation(
                AnimationClip(
                    animation_id,
                    frames=(
                        f"{animation_id}:0",
                        f"{animation_id}:1",
                    ),
                    fps=fps,
                    loop=(animation_id not in ("player_jump", "player_roll")),
                )
            )

        self.player_animation = AnimationPlayer(
            self.sprites
        )
        self.player_animation.play(
            "player_idle"
        )

        if self.custom_map_loaded:
            spawn_x, spawn_y = (
                self.map_loader.player_spawn_world(self.world)
            )
        else:
            spawn_x = (
                4.0
                * TILE_SIZE
            )
            spawn_y = (
                GROUND_ROW
                * TILE_SIZE
            )

        # Authoritative respawn point. Custom maps must never respawn at the
        # prototype's hard-coded (4, GROUND_ROW) coordinates.
        self.respawn_x = float(spawn_x)
        self.respawn_y = float(spawn_y)

        self.player = Player(
            x=spawn_x,
            y=spawn_y,
        )
        # FIX87: bind before auto-load, map adoption or the first damage tick.
        self.player.configure_death_handling(self.game_settings.death_handling_enabled)
        self._seen_world_wrap_serial = int(getattr(self.player, "world_wrap_serial", 0))
        self._configure_player_animation_events()

        # Prototype entities used to stay at fixed test-world coordinates. On
        # a generated 256x96 map that could place them kilometres away from the
        # player or inside rock. Keep them near the custom-map spawn surface.
        if self.custom_map_loaded:
            spawn_tx = int(spawn_x // TILE_SIZE)

            def _surface_pos(offset_tiles):
                tx = max(0, min(self.world.width_tiles - 1, spawn_tx + int(offset_tiles)))
                row = self.world.fauna_surface_row(tx)
                if row is None:
                    return (tx + 0.5) * TILE_SIZE, spawn_y
                return (tx + 0.5) * TILE_SIZE, float(row) * TILE_SIZE

            npc_x, npc_y = _surface_pos(8)
            item_x, item_y = _surface_pos(12)
            crate1_x, crate1_y = _surface_pos(-6)
            crate2_x, crate2_y = _surface_pos(-4)
            slime_x, slime_y = _surface_pos(16)
            boar_x, boar_y = _surface_pos(-12)
        else:
            npc_x, npc_y = 44.5 * TILE_SIZE, GROUND_ROW * TILE_SIZE
            item_x, item_y = 49.0 * TILE_SIZE, GROUND_ROW * TILE_SIZE
            crate1_x, crate1_y = 36.0 * TILE_SIZE, GROUND_ROW * TILE_SIZE
            crate2_x, crate2_y = 38.0 * TILE_SIZE, GROUND_ROW * TILE_SIZE
            slime_x, slime_y = 54.0 * TILE_SIZE, GROUND_ROW * TILE_SIZE
            boar_x, boar_y = 31.0 * TILE_SIZE, GROUND_ROW * TILE_SIZE

        self.npc = NPC(
                entity_id="npc_test",
                x=npc_x,
                y=npc_y,
                name="測試 NPC",
                dialogue="你好，我現在會巡邏、觀察與跟隨玩家。",
                home_x=npc_x,
                target_x=npc_x,
                patrol_radius=150.0,
                speed=62.0,
        )
        if self.prototype_entities_enabled:
            self.scene.add_entity(self.npc)
        else:
            self.npc.active = False

        self.item = Item(
                entity_id="item_test",
                x=item_x,
                y=item_y,
                item_id="fruit",
                name="測試果實",
                count=1,
        )
        if self.prototype_entities_enabled:
            self.scene.add_entity(self.item)
        else:
            self.item.active = False
            self.item.picked = True

        # V0.7.2.4 backpack is the authoritative item store. World items stay
        # physical until the player presses 互動 near them.
        self.inventory = InventorySystem(self.events)
        # FIX39 equipment never consumes the inventory stack. The starter sword
        # therefore lives in the same backpack as treasure weapons and remains
        # available after swapping to another weapon.
        self.inventory.add("weapon_sword", "鐵劍", 1, max_stack=self.inventory.WEAPON_MAX_STACK)
        # FIX73 exposes the already-functional tool as an owned starter item.
        # It stays in the backpack across map travel and fresh-game saves.
        self.inventory.add("tool_grapple", "鉤爪", 1, max_stack=1)
        # Wearables remain ordinary stacked inventory items. EquipmentSystem
        # stores only three selected IDs and never creates UIKit objects.
        self.equipment = EquipmentSystem(self.inventory, self.events)
        if self.game_settings.starter_items_enabled:
            self.ensure_equipment_inventory()
        self.equipment.sync_player(self.player)
        self.opened_chest_ids = set()
        # FIX76: every durability-destroyed weapon gets one persistent recovery
        # chest record. Records are global to the play session/save and are
        # materialized only while their authored map is active.
        self.recovery_chest_records = []
        self._recovery_chest_serial = 0
        self._drop_serial = 0
        self.interactions = InteractionSystem(
            self.scene,
            self.events,
            pickup_callback=self._pickup_world_item,
            chest_callback=self._open_chest,
        )

        self.save_manager = SaveManager()
        # Format-18 SaveManager.load() runs later in this constructor and may
        # populate visited-map snapshots during automatic startup restore.
        # Initialise before that call so the decoded cache is never erased by
        # post-load portal setup.
        self._map_session_states = {}

        # Absolute deterministic world time. All long-term ecology/weather/
        # energy systems share this clock.
        self.time = GameTimeSystem()

        # V0.7.0: activate the player's local simulation window before
        # environment bootstrap. This lets atmosphere/soil/thermal seed only
        # nearby chunks instead of allocating state for the entire map.
        self.chunk_streamer.update(self.player.x, self.player.y)

        # V0.4 environment simulation
        self.environment = EnvironmentManager(
            self.world,
            self.chunk_streamer,
            self.events,
            self.time,
        )
        if self.custom_map_loaded:
            self.environment.initialize_from_world()
            self.map_loader.apply_environment(
                self
            )
        else:
            self.environment.build_test_environment()

        # V0.7.3: climate/biome-aware targetable population. The generated map
        # carries biome spans in metadata. Older maps fall back to the same
        # deterministic span layout so this remains backward compatible.
        self.biomes = BiomeSystem(
            self.world, self.map_loader,
            env=self.environment.state, water_system=self.environment.water
        )
        # FIX90 authored objects share the real scene, inventory and respawn
        # systems; these are not editor-only pins.
        from systems.editor_objects import objects_from_metadata, spawn_editor_creatures, spawn_editor_chests
        _editor_meta = dict(self.map_loader.payload.get("metadata",{})) if self.map_loader else {}
        _editor_rows = objects_from_metadata(_editor_meta)
        self.editor_visuals = {}
        for _row in _editor_rows:
            if _row.get("kind")=="visual" and _row.get("enabled",True):
                _key=(int(_row.get("x",0))//CHUNK_SIZE,int(_row.get("y",0))//CHUNK_SIZE)
                self.editor_visuals.setdefault(_key,[]).append(dict(_row))
        if _editor_rows:
            from systems.editor_catalog import all_archetypes
            CREATURE_ARCHETYPES.update(all_archetypes(project_root))
        self.creatures = [] if _editor_meta.get("editor_population_mode")=="authored_only" else self.secondary_world.build_creatures(
            self.biomes, self.scene, spawn_y, avoid_world_x=self.player.x
        )
        # FIX93: automatic creatures are visible/editable from the map editor.
        # Individual automatic actors can be suppressed and replaced by an
        # authored editor spawn without disabling the rest of the ecosystem.
        _suppressed_auto=set(str(v) for v in (_editor_meta.get('editor_suppressed_auto_spawns',[]) or []) if v)
        if _suppressed_auto:
            _removed_ids={str(getattr(c,'entity_id','')) for c in self.creatures if str(getattr(c,'entity_id','')) in _suppressed_auto}
            if _removed_ids:
                self.creatures=[c for c in self.creatures if str(getattr(c,'entity_id','')) not in _removed_ids]
                self.scene.entities[:]=[e for e in self.scene.entities if str(getattr(e,'entity_id','')) not in _removed_ids]
        try:
            from systems.editor_objects import write_runtime_population_snapshot
            write_runtime_population_snapshot(project_root,self._map_key(),self.creatures,_editor_meta.get('editor_revision',''))
        except Exception as _snapshot_exc:
            print('EDITOR POPULATION SNAPSHOT:',str(_snapshot_exc)[:180])
        _editor_creatures,_editor_warnings=spawn_editor_creatures(self,_editor_rows)
        self.creatures.extend(_editor_creatures)
        self.chests = self._build_world_chests() if _editor_meta.get("editor_auto_chests",True) else []
        _editor_chests,_chest_warnings=spawn_editor_chests(self,_editor_rows)
        self.chests.extend(_editor_chests)
        self.editor_placement_warnings=_editor_warnings+_chest_warnings
        for _warning in self.editor_placement_warnings[:8]:print("EDITOR PLACEMENT:",_warning)
        self.slime = next((c for c in self.creatures if c.species == "slime"), None)
        self.boar = next((c for c in self.creatures if c.species == "boar"), None)

        self.physics_objects = [
            PhysicsObject(
                entity_id="crate_01",
                x=crate1_x,
                y=crate1_y,
                width_px=32.0,
                height_px=32.0,
                mass=1.0,
                restitution=0.06,
                friction=0.90,
            ),
            PhysicsObject(
                entity_id="crate_02",
                x=crate2_x,
                y=crate2_y,
                width_px=26.0,
                height_px=26.0,
                mass=0.65,
                restitution=0.12,
                friction=0.86,
            ),
        ] if self.prototype_entities_enabled else []

        self.npc_ai = NPCAISystem(
            self.scene, self.physics, self.chunk_streamer, self.player
        )
        self.toxic = ToxicSystem(
            self.environment.state, self.player, self.scene
        )
        self.physics_object_system = PhysicsObjectSystem(
            self.physics_objects,
            self.physics,
            self.chunk_streamer,
            self.player,
            self.environment,
        )
        self.item_drop_system = ItemDropSystem(
            self.scene, self.physics, self.chunk_streamer, player=self.player
        )
        self.creature_system = CreatureSystem(
            self.scene, self.physics, self.chunk_streamer, self.player,
            self.spawn_world_item_result, events=self.events,
            environment=self.environment, biome_system=self.biomes,
            asset_registry=self.assets,
            secondary_world=self.secondary_world,
        )
        self.creature_system.respawn.set_visibility_callback(
            self._creature_respawn_spawn_visible
        )

        # V0.6.0: this collection is stable for the current prototype.  Keep a
        # single tuple instead of rebuilding it every environment tick.
        self._environment_objects = (
            *self.physics_objects,
            *((self.npc, self.item) if self.prototype_entities_enabled else ()),
            *(c for c in self.creatures if not getattr(c,"background_only",False)),
        )

        self.magic = MagicSystem(
            self.player, self.world, self.environment, self.events, scene=self.scene
        )

        # Tool system. V0.6.5 routes tool/magic/weapon through a separate
        # action-mode switch; the right analog control provides direction only.
        self.tools = ToolSystem(
            self.player,
            self.world,
            self.environment,
            self.events,
            self.aim_world_target,
            drop_callback=self.spawn_world_item_result,
        )
        self._pending_broken_weapon_fallback = None
        self.melee = MeleeSystem(
            self.player, self.scene, self.events,
            world=self.world, tool_system=self.tools, asset_registry=self.assets,
            aim_provider=self.aim_direction,
            heavy_use_callback=self._commit_weapon_heavy_use,
        )
        self.drone = DroneSystem(self)
        self.boss_weapons = BossWeaponSystem(self)
        self.melee.boss_weapon_system = self.boss_weapons
        self.creature_system.boss_weapon_system = self.boss_weapons
        self.lava_spit = LavaSpitSystem(self)
        self.pyramid_traps = PyramidTrapSystem(self)

        # V0.6.5 action routing. The right stick selects DIRECTION only.
        # The large right-side button is a pure trigger; this mode decides
        # whether that trigger uses the current tool, current magic, or weapon.
        self.ACTION_TOOL = "tool"
        self.ACTION_MAGIC = "magic"
        self.ACTION_WEAPON = "weapon"
        self.action_mode = self.ACTION_TOOL
        self.action_debug = {
            "mode": self.action_mode,
            "last_ms": 0.0,
            "count": 0,
        }

        # Expensive ecology/thermal/water entry is decoupled from 60 Hz
        # player/projectile physics. Individual environment systems still keep
        # their own lower fixed-rate schedulers.
        self._environment_accumulator = 0.0
        self._environment_step = 1.0 / max(1.0, PERF_ENVIRONMENT_HZ)

        # V0.7.0: whole-world conservation audits are engineering diagnostics,
        # not gameplay. Never scan a multi-million-tile world when the HUD is off.
        self._audit_timer = 0.0
        if ENGINEERING_HUD_ENABLED:
            self.environment.matter.capture_baseline(self)
            self.environment.energy.capture_baseline()
            self.debug_audit = {
                "matter": self.environment.matter.audit(self),
                "energy": self.environment.energy.snapshot(),
            }
        else:
            self.debug_audit = {"matter": {}, "energy": {}}

        self.camera_x = 0.0
        self.camera_y = 0.0
        self.prev_camera_x = 0.0
        self.prev_camera_y = 0.0
        self.render_alpha = 0.0

        self.viewport_w = 852.0
        self.viewport_h = 393.0

        # V0.6.2.4: custom maps can spawn thousands of pixels away from the
        # prototype origin. Start the camera at the player immediately instead
        # of easing from world (0, 0) for the first second of gameplay.
        if self.custom_map_loaded:
            world_w = self.world.width_tiles * TILE_SIZE
            world_h = self.world.height_tiles * TILE_SIZE
            self.camera_x = clamp(
                self.player.x - self.viewport_w * 0.50,
                0.0,
                max(0.0, world_w - self.viewport_w),
            )
            self.camera_y = clamp(
                self.player.y - self.viewport_h * CAMERA_VERTICAL_LOWER,
                0.0,
                max(0.0, world_h - self.viewport_h),
            )
            self.prev_camera_x = float(self.camera_x)
            self.prev_camera_y = float(self.camera_y)

        # Fixed-radius directional reticle.
        #
        # The joystick supplies direction only. Its displacement magnitude and
        # how long the reticle has been moving do not change target distance.
        self.aim_axis_x = 0.0
        self.aim_axis_y = 0.0

        # V0.6.5.2 soft absolute aim. UIKit publishes the stick's absolute
        # direction as a TARGET. The game thread rotates the existing reticle
        # toward that target over several 60 Hz frames, eliminating the visible
        # jump when stick direction and current crosshair direction differ.
        self._aim_target_x = 0.0
        self._aim_target_y = 0.0
        self._aim_target_valid = False

        self.aim_dir_x = float(
            self.player.facing
            if self.player.facing != 0
            else 1
        )
        self.aim_dir_y = 0.0

        # Kept as derived screen-space values for diagnostics / compatibility.
        self.aim_screen_x = 0.0
        self.aim_screen_y = 0.0

        self.aim_active = False
        self.magic_aim_charge_seconds = 0.0
        # FIX85: fire ownership belongs to one RIGHT-stick gesture, never the
        # movement axes. These are transient and are deliberately not saved.
        self._aim_shot_signature = None
        self._aim_has_direction = False
        self._aim_hold_seconds = 0.0
        self._right_stick_shot_this_frame = False
        self._attack_release_consumed = False
        self._input_modal_blocked = False
        # FIX46 weapon mode mirrors magic's hold/release ownership but uses
        # weapon-specific heavy thresholds from weapon_catalog.
        self.weapon_charge_seconds = 0.0

        self._refresh_aim_screen_position()

        self.chunk_streamer.update(
            self.player.x,
            self.player.y,
        )

        # V0.7.1.1 persistent session restore.  Pressing 存 and later
        # relaunching main.py must continue from the saved world without an
        # extra manual 讀 step.  A missing/corrupt save never prevents startup.
        self.auto_loaded_save = False
        self.auto_load_info = ""
        try:
            if (not skip_auto_load) and self.save_manager.exists():
                ok, info = self.save_manager.load(self)
                self.auto_loaded_save = bool(ok)
                self.auto_load_info = str(info)
                if ok:
                    print("AUTO LOAD OK:", info)
                else:
                    print("AUTO LOAD SKIPPED:", info)
        except Exception as exc:
            self.auto_load_info = repr(exc)
            print("AUTO LOAD ERROR:", repr(exc))

        try:
            self.drone.sync_equipment(force=True)
            if hasattr(self, "boss_weapons"):
                self.boss_weapons.sync_equipment()
        except Exception:
            pass

        # Loading may move the player to a different chunk and facing.
        self.chunk_streamer.update(self.player.x, self.player.y)
        self.aim_dir_x = float(self.player.facing if self.player.facing != 0 else 1)
        self.aim_dir_y = 0.0
        self._refresh_aim_screen_position()

        # FIX32 multi-map world graph. Portals live in map metadata and use a
        # Metal fade rather than rebuilding the iOS view hierarchy.
        self.map_transition_alpha = 0.0
        self.map_transition_phase = ""
        self._pending_portal = None
        self._portal_cooldown = 0.75
        # FIX33 destination-gate latch.  After a map switch the player may
        # intentionally spawn at the doorway's floor edge; do not permit the
        # reverse portal until the player has actually left that trigger rect.
        self._portal_requires_exit = False
        # FIX70 discovery prompt latch.  It contains only Python data and is
        # consumed through the existing EventBus ``message`` path, so portal
        # hints do not add another UIKit view or touch the render loop.  A
        # doorway may prompt once per physical entry; walking out clears it.
        self._portal_prompt_latch = None
        self._portal_prompt_status = None
        self._portal_target_validation_cache = {}
        # FIX34 transition safety: landing on a portal by falling is not an
        # intentional "walk through the door" action, and held UIKit input
        # from the old map must never leak into the first frame of the new map.
        self._portal_post_lock = 0.0
        # Gameplay never calls UIKit from its simulation thread.  Incrementing
        # this serial asks PytoHost to force-release native joysticks/holds on
        # the iOS main thread after a load or portal transition.
        self._platform_controls_reset_serial = 0
        self.current_map_path = str(getattr(self.map_runtime, "source_path", "") or map_path or "")
        # FIX69 keeps portal connectivity in one readable data catalog. Map
        # metadata still owns trigger geometry/arrival points; the topology
        # supplies the canonical bidirectional destination at activation time.
        # It is loaded once per GameApp construction, never in the 60 Hz path.
        self.world_topology = WorldTopology.from_project_root(
            getattr(self.save_manager, "project_root", "")
        )
        self.portal_audit = self.world_topology.audit_bidirectional_files()
        if not bool(self.portal_audit.get("ok", False)):
            print("PORTAL GRAPH AUDIT:", " | ".join(self.portal_audit.get("errors", ())[:8]))
        # FIX47/69: portal travel must not rebuild a previously visited map.
        # SaveManager may already have restored this cache above; a fresh or
        # skip-auto-load GameApp retained the empty dictionary initialised next
        # to SaveManager. New Game explicitly clears it during adoption.
        if not isinstance(getattr(self, "_map_session_states", None), dict):
            self._map_session_states = {}

        # FIX68 New Game is a two-step, non-blocking transition. UIKit only
        # posts the command; a worker constructs/saves the pristine overworld
        # while the simulation thread keeps returning frames. The completed
        # GameApp is adopted at a frame boundary without replacing this object
        # identity, so Host/Metal/backpack references remain valid.
        self._new_game_confirm_deadline = 0.0
        self._new_game_in_progress = False
        self._new_game_request_serial = 0
        self._new_game_result_lock = threading.RLock()
        self._new_game_pending_result = None
        self._new_game_worker = None
        self._new_game_last_result = None

        # Title/death menus are platform presentation; this state is pure
        # gameplay so headless tests and other hosts can enforce the same rule.
        self.death_pending = False
        self.death_serial = 0

        # FIX36: dynamic phase changes (water->ice, lava->igneous rock, or any
        # future solid materialisation) may happen after ordinary player
        # collision for the frame. Track terrain revision so we can immediately
        # rescue an embedded player without scanning the world every tick.
        self._solid_resolve_revision = int(getattr(self.world, "revision", 0))
        self._solid_resolve_count = 0
        self._last_solid_resolve = None

        # FIX31: arm no-damage startup grace only after map + optional save are
        # fully restored.  This keeps a saved HP value intact while preventing
        # a swamp/poison/monster tick before the first controllable frame.
        self.player.startup_damage_grace = float(PLAYER_STARTUP_DAMAGE_GRACE_SECONDS)

        # FIX78 learning state is independent of a game-save slot. Loading a
        # world or travelling through a portal must not rewind active playtime.
        self.study = StudyChallenge.from_project_root(
            project_root, save_dir=self.save_manager.save_dir,
            profile=self.game_settings.study_intensity,
        )
        self.playtime_guard = PlaytimeGuard.from_save_dir(self.save_manager.save_dir)

    @staticmethod
    def _rect_overlap_strict(a, b, eps=0.45):
        return not (
            float(a[2]) <= float(b[0]) + eps
            or float(b[2]) <= float(a[0]) + eps
            or float(a[3]) <= float(b[1]) + eps
            or float(b[3]) <= float(a[1]) + eps
        )

    def _player_position_blockers(self, x, y):
        """Return authoritative solid cells overlapping the player at ``x,y``."""
        try:
            box = self.player.bbox(x=float(x), y=float(y))
        except Exception:
            return None
        world_w = float(self.world.width_tiles) * TILE_SIZE
        world_h = float(self.world.height_tiles) * TILE_SIZE
        if box[0] < 0.0 or box[2] > world_w or box[1] < 0.0 or box[3] > world_h:
            return None
        out=[]
        try:
            for tx,ty,tid,rect in self.world.solid_cells_in_rect(box, padding=1):
                if tile_def(tid).one_way_platform:
                    continue
                if self._rect_overlap_strict(box, rect):
                    out.append((int(tx),int(ty),int(tid),rect))
        except Exception:
            return None
        return out

    def _player_position_is_clear(self, x, y):
        """True when the player's current posture fits at ``x,y``."""
        blockers=self._player_position_blockers(x,y)
        return blockers is not None and not blockers

    def _player_escape_path_is_clear(self, x0, y0, x1, y1, allowed_start_cells):
        """Prevent dynamic-solid rescue from tunnelling through a roof/wall.

        The player *starts* embedded in the newly-created ICE/rock, so the path
        is allowed to overlap exactly those starting cells while exiting them.
        Any other solid encountered along the swept path (for example the roof
        above a ladder) rejects the candidate.  The final position itself must
        be completely clear.
        """
        dx=float(x1)-float(x0);dy=float(y1)-float(y0)
        step=max(3.0,float(TILE_SIZE)/8.0)
        count=max(1,int(math.ceil(max(abs(dx),abs(dy))/step)))
        allowed=set((int(a),int(b)) for a,b in allowed_start_cells)
        for i in range(1,count+1):
            q=i/float(count)
            xx=float(x0)+dx*q;yy=float(y0)+dy*q
            blockers=self._player_position_blockers(xx,yy)
            if blockers is None:
                return False
            for tx,ty,_tid,_rect in blockers:
                if (int(tx),int(ty)) not in allowed:
                    return False
        return self._player_position_is_clear(x1,y1)

    def _resolve_player_dynamic_solid_overlap(self, force=False):
        """Move the player out of a solid created around their body.

        Priority requested by FIX36:
        1) upward in one terrain-layer (1/3 tile) increments;
        2) if blocked above, move forward/back to the nearest free space;
        3) bounded mixed search as a final safety net, never downward into rock.

        This runs only when world geometry revision changes, so normal 60 Hz
        walking has effectively zero extra cost.
        """
        revision = int(getattr(self.world, "revision", 0))
        if not force and revision == int(getattr(self, "_solid_resolve_revision", revision)):
            return False
        self._solid_resolve_revision = revision

        p = self.player
        try:
            box = p.bbox()
        except Exception:
            return False
        overlapping = []
        try:
            for tx,ty,tid,rect in self.world.solid_cells_in_rect(box, padding=1):
                if tile_def(tid).one_way_platform:
                    continue
                if self._rect_overlap_strict(box, rect):
                    overlapping.append((tx,ty,tid,rect))
        except Exception:
            return False
        if not overlapping:
            return False

        old_x = float(p.x); old_y = float(p.y)
        layer = float(TILE_SIZE) / 3.0

        candidates = []
        # First choice: rise by the smallest whole terrain-layer increment.
        for units in range(1, 7):
            candidates.append((old_x, old_y - layer * units, "up", units))

        facing = 1 if int(getattr(p, "facing", 1) or 1) >= 0 else -1
        # If a roof blocks upward escape, try forward first, then backward.
        for units in (1, 2, 3, 4, 5, 6):
            dist = layer * units
            candidates.append((old_x + facing * dist, old_y, "forward", units))
            candidates.append((old_x - facing * dist, old_y, "back", units))

        # Tight cave fallback: combine a small rise with horizontal escape.
        for up_units in (1, 2, 3):
            for side_units in (1, 2, 3, 4, 5, 6):
                dist = layer * side_units
                yy = old_y - layer * up_units
                candidates.append((old_x + facing * dist, yy, "forward_up", side_units))
                candidates.append((old_x - facing * dist, yy, "back_up", side_units))

        chosen = None
        starting_cells={(int(tx),int(ty)) for tx,ty,_tid,_rect in overlapping}
        for x,y,mode,units in candidates:
            # FIX41: destination-only tests could "jump" through a ceiling:
            # farther upward candidates above the roof looked clear even though
            # the intervening path crossed a solid tile.  Sweep from the
            # embedded position, permitting only the solid cells that caused
            # the original overlap.
            if self._player_escape_path_is_clear(old_x,old_y,x,y,starting_cells):
                chosen = (float(x),float(y),str(mode),int(units))
                break

        if chosen is None:
            # Do not silently teleport the character far across the map. Keep
            # the overlap visible for debugging if no local safe cell exists.
            self._last_solid_resolve = {
                "ok": False, "revision": revision,
                "x": old_x, "y": old_y, "overlaps": len(overlapping),
            }
            return False

        p.x, p.y = chosen[0], chosen[1]
        p.prev_x, p.prev_y = p.x, p.y
        p.vy = 0.0
        # FIX41 ladder safety: if geometry materialises while climbing, the old
        # ladder controller still owns an absolute center/top exit and would
        # snap a rescued character back on the next frame. Detach after a
        # successful rescue and let ordinary collision settle the player.
        if str(getattr(p,"state", "")) == "climb" or getattr(p,"climb",None) is not None:
            p.climb=None
            p.state="fall"
            p.grounded=False
            try:
                p.jump_preparing=False
                p.jump_prepare_elapsed=0.0
            except Exception:
                pass
        # Horizontal rescue should not instantly drive the player back into the
        # newly created block from a held movement vector.
        if chosen[2] != "up":
            p.vx = 0.0
        try:
            if chosen[2] != "up":
                self.input.reset()
        except Exception:
            pass
        self._solid_resolve_count = int(getattr(self, "_solid_resolve_count", 0)) + 1
        self._last_solid_resolve = {
            "ok": True, "revision": revision, "mode": chosen[2],
            "layers": chosen[3], "from": (old_x,old_y), "to": (p.x,p.y),
        }
        return True

    def _map_session_key(self, path=None):
        """Canonical key for one authored map inside the current new-game session."""
        raw = str(path or getattr(self, "current_map_path", "") or getattr(getattr(self, "map_runtime", None), "source_path", "") or "")
        if not raw:
            return ""
        try:
            return os.path.normcase(os.path.realpath(os.path.abspath(raw)))
        except Exception:
            return raw.replace("\\", "/")

    def _creature_respawn_spawn_visible(self, creature, spawn_x, spawn_y):
        """Match the renderer viewport before allowing a creature to return.

        The callback is rebound after every GameApp adoption so it always
        reads the live camera rather than a discarded portal/new-game shell.
        Unknown camera state is treated as visible, which safely postpones a
        respawn instead of producing an on-screen pop-in.
        """
        try:
            width=max(1.0,float(creature.width()))
            height=max(1.0,float(creature.height()))
            left=float(spawn_x)-width*.5
            right=float(spawn_x)+width*.5
            top=float(spawn_y)-height
            bottom=float(spawn_y)
            margin=48.0
            view_left=float(self.camera_x)-margin
            view_right=float(self.camera_x)+float(self.viewport_w)+margin
            view_top=float(self.camera_y)-margin
            view_bottom=float(self.camera_y)+float(self.viewport_h)+margin
            return not (
                right<view_left or left>view_right or
                bottom<view_top or top>view_bottom
            )
        except Exception:
            return True

    def _capture_current_map_session_state(self):
        """Keep terrain/environment edits while travelling through portals.

        FIX46 created a brand-new GameApp for the destination map.  That was
        safe for map loading but meant returning to the overworld regenerated
        every mined pyramid block.  FIX47 snapshots only serializable gameplay
        state, never renderer/UIKit objects.
        """
        key = self._map_session_key()
        if not key or not bool(getattr(self, "custom_map_loaded", False)):
            return False
        try:
            world_changes = self.world.export_changes()
        except Exception:
            world_changes = []
        try:
            environment = self.environment.state.export_state()
        except Exception:
            environment = None
        try:
            energy = self.environment.energy.export_state()
        except Exception:
            energy = None
        try:
            wet_clock = [
                [int(tx), int(ty), float(value)]
                for (tx, ty), value in sorted(self.environment.plants.topsoil_wet_clock.items())
            ]
        except Exception:
            wet_clock = []
        try:
            creature_rows = [
                {
                    "entity_id": str(c.entity_id),
                    "x": float(c.x), "y": float(c.y),
                    "hp": float(c.hp), "active": bool(c.active),
                    "death_processed": bool(c.death_processed),
                    "behavior_state": str(c.behavior_state),
                    "player_detected": bool(getattr(c,"player_detected",False)),
                    "provoked_by_player": bool(getattr(c,"provoked_by_player",False)),
                }
                for c in tuple(self.creatures)
            ]
            creature_respawn = self.creature_system.respawn.export_state()
        except Exception:
            creature_rows = []
            creature_respawn = {}
        try:
            world_items = self.item_drop_system.export_rows()
        except Exception:
            world_items = []
        try:
            drop_serial = max(0, min(
                1000000000,
                int(getattr(self, "_drop_serial", 0) or 0),
            ))
        except (TypeError, ValueError, OverflowError):
            drop_serial = 0
        self._map_session_states[key] = {
            "world_base": self.save_manager._world_base_signature(self),
            "world_changes": world_changes,
            "environment": environment,
            "energy": energy,
            "topsoil_wet_clock": wet_clock,
            "world_items": world_items,
            "drop_serial": drop_serial,
            "creatures": creature_rows,
            "creature_respawn": creature_respawn,
            # Unlike monotonic(), Unix time remains meaningful after Pyto is
            # terminated and relaunched, so unloaded-map respawn timers keep
            # counting down across process restarts.
            "captured_at_unix": float(time.time()),
        }
        return True

    def _restore_map_session_state(self, fresh, target_path):
        """Apply cached edits to a freshly constructed destination GameApp."""
        key = self._map_session_key(target_path)
        state = (getattr(self, "_map_session_states", {}) or {}).get(key)
        if not isinstance(state, dict):
            return False
        saved_base = state.get("world_base")
        if isinstance(saved_base, dict):
            current_base = self.save_manager._world_base_signature(fresh)
            if not self.save_manager._world_base_compatible(saved_base, current_base):
                # An updated authored map must not receive stale coordinates or
                # deltas from an older base. Keep the clean destination and
                # discard only this incompatible cached map.
                try:self._map_session_states.pop(key, None)
                except Exception:pass
                print("PORTAL map-session base changed; clean map retained:", target_path)
                return False
        try:
            fresh.world.apply_changes(state.get("world_changes", ()) or ())
        except Exception as exc:
            print("PORTAL world-state restore skipped:", repr(exc))
        env_state = state.get("environment")
        if isinstance(env_state, dict):
            try:
                fresh.environment.state.import_state(env_state)
            except Exception as exc:
                print("PORTAL environment-state restore skipped:", repr(exc))
        if isinstance(state.get("energy"), dict):
            try:
                fresh.environment.energy.import_state(state.get("energy") or {})
            except Exception:
                pass
        try:
            clock = fresh.environment.plants.topsoil_wet_clock
            clock.clear()
            for row in state.get("topsoil_wet_clock", ()) or ():
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    clock[(int(row[0]), int(row[1]))] = float(row[2])
        except Exception:
            pass
        try:
            rows = state.get("world_items", ())
            if isinstance(rows, (list, tuple)):
                limited_rows = SaveManager._clean_world_item_rows(
                    rows, strict=True, require_entity_id=True,
                    world_width_px=float(fresh.world.width_tiles) * TILE_SIZE,
                    world_height_px=float(fresh.world.height_tiles) * TILE_SIZE,
                )
                if limited_rows is None:
                    raise ValueError("invalid map-session world_items")
                highest_serial = 0
                for row in limited_rows:
                    candidate = str(row.get("entity_id", "") or "")
                    suffix = candidate[5:] if candidate.startswith("drop_") else ""
                    if suffix.isdigit():
                        highest_serial = max(highest_serial, int(suffix))
                fresh.item_drop_system.clear_dynamic()
                saved_drop_serial = SaveManager._finite_number(
                    state.get("drop_serial", 0), 0.0,
                )
                fresh._drop_serial = max(
                    highest_serial,
                    max(0, min(1000000000, int(saved_drop_serial))),
                )
                for row in limited_rows:
                    result = fresh.spawn_world_item_result(
                        str(row.get("item_id", "item") or "item"),
                        str(row.get("name", "物品") or "物品"),
                        int(row.get("count", 0) or 0),
                        float(row.get("x", fresh.player.x)),
                        float(row.get("y", fresh.player.y)),
                        heavy_uses=row.get("heavy_uses"),
                        preferred_entity_id=str(row.get("entity_id", "") or ""),
                        allow_merge=False,
                        announce_rejection=False,
                    )
                    item = result.item
                    if item is None or int(result.rejected_count) > 0:
                        continue
                    item.vy = float(row.get("vy", 0.0))
                fresh._drop_serial = max(
                    int(getattr(fresh, "_drop_serial", 0) or 0),
                    highest_serial,
                    max(0, min(1000000000, int(saved_drop_serial))),
                )
        except Exception as exc:
            print("PORTAL world-item restore skipped:", repr(exc))
        try:
            by_id={str(c.entity_id):c for c in tuple(fresh.creatures)}
            for row in state.get("creatures",()) or ():
                if not isinstance(row,dict):
                    continue
                creature=by_id.get(str(row.get("entity_id","") or ""))
                if creature is None:
                    continue
                creature.x=float(row.get("x",creature.x))
                creature.y=float(row.get("y",creature.y))
                creature.hp=float(row.get("hp",creature.hp))
                creature.active=bool(row.get("active",creature.active))
                creature.death_processed=bool(row.get("death_processed",creature.death_processed))
                creature.behavior_state=str(row.get("behavior_state",creature.behavior_state))
                creature.provoked_by_player=bool(row.get("provoked_by_player",False))
                can_attack=bool(getattr(creature,"hostile",False) or creature.provoked_by_player)
                creature.player_detected=bool(row.get("player_detected",False)) if can_attack else False
                creature.vx=0.0;creature.vy=0.0
            saved_respawn=state.get("creature_respawn")
            if isinstance(saved_respawn,dict):
                if state.get("captured_at_unix") is not None:
                    captured=float(state.get("captured_at_unix",time.time()))
                    away_seconds=max(0.0,float(time.time())-captured)
                else:
                    # One-process compatibility for a cache captured by FIX68
                    # before a hot reload. Such monotonic values are never
                    # written to the format-18 save.
                    captured=float(state.get("captured_monotonic",time.monotonic()))
                    away_seconds=max(0.0,float(time.monotonic())-captured)
                fresh.creature_system.respawn.import_state(
                    saved_respawn,restore_dead=True,
                    elapsed_seconds=away_seconds,
                )
        except Exception as exc:
            print("PORTAL creature-state restore skipped:",repr(exc))
        return True

    def _portal_rows(self):
        try:
            meta=dict((self.map_loader.payload or {}).get("metadata",{}) or {}) if self.map_loader else {}
        except Exception:
            meta={}
        rows=meta.get("portals",())
        return rows if isinstance(rows,list) else ()

    def _resolved_portal_row(self, row):
        """Apply the FIX69 topology route without mutating authored metadata."""
        if not isinstance(row,dict):
            return None
        topology=getattr(self,"world_topology",None)
        if topology is None:
            return dict(row)
        current=str(
            getattr(self,"current_map_path","")
            or getattr(getattr(self,"map_loader",None),"path","")
            or ""
        )
        try:
            return topology.resolve(current,row)
        except Exception as exc:
            print("PORTAL topology resolve skipped:",repr(exc))
            return dict(row)

    def _portal_target_path(self, target):
        """Resolve a target strictly inside this project's maps directory."""
        topology=getattr(self,"world_topology",None)
        try:
            name=(topology._map_name(target) if topology is not None else os.path.basename(str(target or "")))
        except Exception:
            name=""
        if not name:
            return ""
        project_root=str(
            getattr(getattr(self,"save_manager",None),"project_root","")
            or os.environ.get("PYTO_RPG_PROJECT_ROOT","")
            or os.path.dirname(os.path.abspath(__file__))
        )
        maps_root=os.path.realpath(os.path.join(os.path.realpath(project_root),"maps"))
        candidate=os.path.realpath(os.path.join(maps_root,name))
        try:
            inside=os.path.commonpath((candidate,maps_root))==maps_root
        except Exception:
            inside=False
        return candidate if inside else ""

    def _clear_portal_prompt_latch(self):
        self._portal_prompt_latch=None
        self._portal_prompt_status=None

    def _portal_prompt_key(self, row):
        """Return a stable per-door key without trusting authored paths."""
        topology=getattr(self,"world_topology",None)
        current=str(
            getattr(self,"current_map_path","")
            or getattr(getattr(self,"map_loader",None),"path","")
            or ""
        )
        try:source=topology._map_name(current) if topology is not None else os.path.basename(current)
        except Exception:source=os.path.basename(current)
        return (
            str(source or ""),
            str(row.get("id","") or ""),
            str(row.get("target_map","") or ""),
            str(row.get("target_portal","") or ""),
        )

    def _portal_destination_status(self, row, validate_payload=False):
        """Return target status without map JSON I/O during doorway discovery.

        Entering a doorway performs only path containment and ``isfile``.
        Payload validation happens after an explicit Interact and is cached by
        file identity.  The full switch still repeats authoritative checks.
        """
        target=str(row.get("target_map","") or "").strip() if isinstance(row,dict) else ""
        topology=getattr(self,"world_topology",None)
        try:
            display=str(topology.destination_name(target) or "").strip()
        except Exception:
            display=""
        if not display:
            display="未知目的地"
        target_path=self._portal_target_path(target)
        if not target_path:
            return False,display,"unsafe target"
        if not os.path.isfile(target_path):
            return False,display,"target missing"
        if not bool(validate_payload):
            return True,display,""
        target_id=str(row.get("target_portal","") or "").strip()
        try:
            stat=os.stat(target_path)
            cache_key=(
                os.path.realpath(target_path),target_id,
                int(getattr(stat,"st_mtime_ns",int(stat.st_mtime*1000000000))),
                int(stat.st_size),
            )
            cache=getattr(self,"_portal_target_validation_cache",None)
            if not isinstance(cache,dict):
                cache={};self._portal_target_validation_cache=cache
            cached=cache.get(cache_key)
            if isinstance(cached,tuple) and len(cached)>=2:
                return bool(cached[0]),display,str(cached[1])
            loader=MapLoader(target_path)
            loader.read()
            ok,info=loader.validate_basic()
            if not ok:
                result=(False,str(info or "invalid map"))
            else:
                portals=dict(loader.payload.get("metadata",{}) or {}).get("portals",())
                result=(bool(target_id and isinstance(portals,list) and any(
                isinstance(item,dict) and str(item.get("id","") or "")==target_id
                for item in portals
                )),"")
            if not result[0]:result=(False,"target portal missing")
            if result[0] and topology is not None:
                current=str(
                    getattr(self,"current_map_path","")
                    or getattr(getattr(self,"map_loader",None),"path","")
                    or ""
                )
                reciprocal,reason=topology.validate_transition(current,row)
                if not reciprocal:
                    result=(False,str(reason or "one-way portal"))
            if len(cache)>=32:
                cache.clear()
            cache[cache_key]=result
            return bool(result[0]),display,str(result[1])
        except Exception as exc:
            return False,display,"target invalid: "+repr(exc)

    def _reject_portal_transition(self, diagnostic, message):
        print("PORTAL rejected:",str(diagnostic or "unknown"))
        self._pending_portal=None
        try:self.events.emit("message",text=str(message))
        except Exception:pass
        return False

    def _portal_probe_tile(self):
        return (
            int(float(self.player.x)//TILE_SIZE),
            int((float(self.player.y)-2.0)//TILE_SIZE),
        )

    @staticmethod
    def _portal_contains_probe(row, tx, ty):
        if not isinstance(row,dict):
            return False
        rect=row.get("rect",())
        try:
            x,y,w,h=[int(v) for v in rect[:4]]
        except Exception:
            return False
        return x<=int(tx)<x+w and y<=int(ty)<y+h

    def _portal_entry_intent(self):
        """FIX42: transitions require the interaction button."""
        try:return bool(self.input.just_pressed("interact"))
        except Exception:return False

    def _portal_contains_player(self, row, x=None, y=None):
        return portal_contains_player(self.world, row, self.player, x=x, y=y)

    def _portal_floor_entry_allowed(self, row):
        # FIX81 share real fractional support surfaces with collision/rendering.
        return portal_floor_entry_allowed(self.world, row, self.player, self.physics)

    def _check_map_portal_trigger(self):
        if self.map_transition_phase:
            return False
        rows=self._portal_rows()

        # Keep a prompt latched while the player remains inside its doorway,
        # even if jumping temporarily fails the floor condition.  Only a real
        # exit makes the same doorway eligible to prompt again.
        inside=[row for row in rows if self._portal_contains_player(row)]
        if not inside:
            self._clear_portal_prompt_latch()

        # Destination arrival stays locked until the player has actually left
        # every portal rectangle.  This remains as a second safety layer even
        # though FIX34 now spawns outside the destination trigger.
        if bool(getattr(self,"_portal_requires_exit",False)):
            if inside:
                return False
            self._portal_requires_exit=False
            self._clear_portal_prompt_latch()

        if self._portal_cooldown > 0.0 or float(getattr(self,"_portal_post_lock",0.0)) > 0.0:
            if inside and self._portal_entry_intent():
                self.events.emit("message", text="傳送門準備中，請稍候再按互動。")
                return True
            return False

        for row in inside:
            if not self._portal_floor_entry_allowed(row):
                if self._portal_entry_intent():
                    self.events.emit("message", text="請站在門前的地面上，再按互動。")
                    return True
                continue

            resolved=self._resolved_portal_row(row)
            if not isinstance(resolved,dict):
                continue
            key=self._portal_prompt_key(resolved)
            if getattr(self,"_portal_prompt_latch",None)!=key:
                available,display,diagnostic=self._portal_destination_status(resolved)
                self._portal_prompt_latch=key
                self._portal_prompt_status=(key,bool(available),str(display),str(diagnostic))
                try:
                    self.events.emit(
                        "message",
                        text=(
                            "按互動前往："+str(display)
                            if available else
                            "傳送門目前不可用："+str(display)
                        ),
                    )
                except Exception:
                    pass
            status=getattr(self,"_portal_prompt_status",None)
            available=bool(
                isinstance(status,tuple) and len(status)>=2
                and status[0]==key and status[1]
            )

            # Merely entering a doorway never changes maps.  An unavailable
            # doorway consumes Interact so InteractionSystem cannot overwrite
            # its diagnostic with the generic "nothing nearby" message.
            if not self._portal_entry_intent():
                return False
            if not available:
                return True
            available,display,diagnostic=self._portal_destination_status(
                resolved,validate_payload=True
            )
            if not available:
                self._portal_prompt_status=(key,False,str(display),str(diagnostic))
                try:self.events.emit("message",text="傳送門目前不可用："+str(display))
                except Exception:pass
                return True
            self._pending_portal=resolved
            self.map_transition_phase="fade_out"
            self.map_transition_alpha=0.0
            self.player.vx=0.0;self.player.vy=0.0
            try:self.input.reset()
            except Exception:pass
            self._request_platform_controls_reset()
            return True
        return False

    def _handle_horizontal_world_wrap(self):
        """Synchronize camera/chunks after Player crosses the cylindrical seam."""
        serial = int(getattr(self.player, "world_wrap_serial", 0))
        if serial == int(getattr(self, "_seen_world_wrap_serial", 0)):
            return False
        self._seen_world_wrap_serial = serial
        self._snap_camera_to_player()
        self.chunk_streamer.update(self.player.x, self.player.y)
        self._refresh_aim_screen_position()
        return True

    def _rebind_after_map_adopt(self):
        try:
            self.interactions.pickup_callback=self._pickup_world_item
            self.interactions.chest_callback=self._open_chest
        except Exception:pass
        try:
            self.creature_system.drop_callback=self.spawn_world_item_result
            self.tools.rebind_runtime(
                player=self.player,
                world=self.world,
                environment=self.environment,
                events=self.events,
                target_provider=self.aim_world_target,
            )
            self.tools.drop_callback=self.spawn_world_item_result
            self.tools.set_action_mode_active(self.action_mode == self.ACTION_TOOL)
        except Exception:pass
        try:
            self.lava_spit.game=self
            self.pyramid_traps.rebind(self)
        except Exception:pass
        try:
            self.melee.asset_registry=self.assets
            self.melee.aim_provider=self.aim_direction
            self.melee.heavy_use_callback=self._commit_weapon_heavy_use
            self.creature_system.asset_registry=self.assets
            self.creature_system.creature_vfx_system.event_bus=self.events
            self.creature_system.respawn.set_visibility_callback(
                self._creature_respawn_spawn_visible
            )
        except Exception:pass
        try:self.drone.rebind(self)
        except Exception:pass
        self.boss_weapons.rebind(self)
        self.melee.boss_weapon_system = self.boss_weapons
        self.creature_system.boss_weapon_system = self.boss_weapons
        try:
            self.equipment.inventory=self.inventory
            self.equipment.reconcile_inventory()
            self.equipment.sync_player(self.player)
        except Exception:pass

    def _request_platform_controls_reset(self):
        self._cancel_aim_release()
        self.aim_active = False
        self._attack_release_consumed = False
        self._platform_controls_reset_serial=int(
            getattr(self,"_platform_controls_reset_serial",0)
        )+1

    def _reset_transients_after_manual_load(self):
        """Clear attacks and hazards that are intentionally absent from saves."""
        melee=getattr(self,"melee",None)
        if melee is not None:
            for name in (
                "_pending_world_effect","_pending_attack_hit","_pending_projectile","_pending_vfx",
            ):
                setattr(melee,name,None)
            melee.slash_projectiles=[];melee.active_vfx=[]
            melee.impact_feedback=[];melee.last_impact_feedback=None
            melee.camera_impulse_x=0.0;melee.camera_impulse_y=0.0
            melee._impact_coalesce_remaining=0.0
            melee.swing_timer=0.0;melee.swing_duration=0.0;melee.current_attack_kind="";melee.cooldown=0.0
        creature_system=getattr(self,"creature_system",None)
        if creature_system is not None:
            creature_system.enemy_magic_bolts=[];creature_system.explosion_vfx=[]
            try:creature_system.creature_vfx_system.clear()
            except Exception:pass
            creature_system.startup_grace=1.0
        # Creature positions/HP/behaviour are restored from the save, but an
        # in-flight AI strike is deliberately not serialized.  Clear every
        # action latch so loading cannot let the pre-load timeline finish a
        # hit, projectile, or animation against the restored player.
        for creature in tuple(getattr(self,"creatures",()) or ()):
            creature.vx=0.0;creature.vy=0.0
            for name in (
                "attack_cooldown","hurt_timer","slow_timer","stun_timer","attack_anim_timer",
                "attack_recoil_timer","ai_action_elapsed","ai_action_duration",
                "ai_action_effect_time","out_of_water_time","habitat_damage_clock",
                "flight_timer","rest_timer",
            ):
                if hasattr(creature,name):setattr(creature,name,0.0)
            creature.attack_rearm_required=False
            creature.ai_action_id="";creature.ai_action_hit_done=False
            creature.ai_action_target_x=float(creature.x)
            creature.ai_action_target_y=float(creature.y)
            creature.ai_action_cooldowns={}
            creature.animation_state_override=""
            creature.flight_mode=""
            creature.flight_target_x=float(creature.x)
            creature.flight_target_y=float(creature.y)
            creature.poison_dose=0.0;creature.poisoned=False
        # These queues represent work in progress, not saved world state.
        # Keeping them would let a pre-load spell/tool operation mutate the
        # freshly restored terrain on a later frame.
        magic=getattr(self,"magic",None)
        if magic is not None:
            for name in ("fireballs","waterballs","iceballs","electricballs"):
                queue=getattr(magic,name,None)
                if hasattr(queue,"clear"):queue.clear()
            magic._buoyant_ice={}
        tools=getattr(self,"tools",None)
        if tools is not None:
            try:tools.reset_transients()
            except Exception:
                tools.damage={};tools.cooldown=0.0
        lava_spit=getattr(self,"lava_spit",None)
        if lava_spit is not None:
            lava_spit.spits=[];lava_spit.spawn_clock=.38;lava_spit.serial=0;lava_spit.game=self
        try:self.pyramid_traps.rebind(self)
        except Exception:pass
        try:self.drone.rebind(self)
        except Exception:pass
        self.boss_weapons.rebind(self)
        self.melee.boss_weapon_system = self.boss_weapons
        self.creature_system.boss_weapon_system = self.boss_weapons
        self._pending_broken_weapon_fallback=None
        p=self.player
        for name in (
            "attack_timer","hurt_timer","hurt_invulnerability","roll_timer",
            "attack_charge_seconds","jump_hold_elapsed","jump_hold_target",
            "jump_buffer_remaining",
            "jump_prepare_elapsed","jump_takeoff_delay_seconds",
            "jump_hold_input_offset","shallow_water_jump_boost_time",
            "lava_burn_timer","health_regen_delay_remaining",
        ):
            if hasattr(p,name):setattr(p,name,0.0)
        p.attack_charge_level=1
        p.jump_preparing=False
        p.poison_dose=0.0;p.poisoned=False
        try:p.reset_equipment_motion_state()
        except Exception:pass
        try:
            self.equipment.reset_air_mobility(p, reason="manual_load", rearm=True)
            self.equipment.sync_player(p, player_max_hp=MAX_HP)
        except Exception:pass
        p.startup_damage_grace=float(PLAYER_STARTUP_DAMAGE_GRACE_SECONDS)
        self.weapon_charge_seconds=0.0;self.magic_aim_charge_seconds=0.0

    @staticmethod
    def _same_authored_map(path_a, path_b):
        try:
            return os.path.normcase(os.path.realpath(str(path_a))) == os.path.normcase(os.path.realpath(str(path_b)))
        except Exception:
            return str(path_a or "") == str(path_b or "")

    def _adopt_clean_map(self, target_path):
        """Replace map runtime while preserving Host/control object identity."""
        target_path=os.path.realpath(str(target_path or ""))
        if not target_path or not os.path.isfile(target_path):
            return False
        old_input=self.input
        old_events=self.events
        old_view=(float(self.viewport_w),float(self.viewport_h))
        shared_assets=getattr(self,"assets",None)
        host_names=(
            "physics_debug","input_backend_name","render_alpha",
            "loop_stage","last_render_error","audio_debug","_platform_controls_reset_serial",
            "study", "playtime_guard",
        )
        host_owned={key:getattr(self,key) for key in host_names if hasattr(self,key)}
        fresh=GameApp(
            use_editor_map=True, map_path=target_path, skip_auto_load=True,
            game_settings=self.game_settings,
        )
        if not bool(getattr(fresh,"custom_map_loaded",False)):
            return False
        fresh.input=old_input
        if shared_assets is not None:
            fresh.assets=shared_assets
            try:
                fresh.melee.asset_registry=shared_assets
                fresh.creature_system.asset_registry=shared_assets
            except Exception:
                pass
        fresh.set_viewport(*old_view)
        adopted=dict(fresh.__dict__)
        self.__dict__.clear(); self.__dict__.update(adopted)
        self.input=old_input
        if shared_assets is not None:self.assets=shared_assets
        for key,value in host_owned.items():setattr(self,key,value)
        self._rebind_runtime_event_bus(old_events)
        self.current_map_path=target_path
        self._rebind_after_map_adopt()
        try:self.input.reset()
        except Exception:pass
        return True

    def _default_main_map_path(self):
        """Return the authored overworld used by a true New Game."""
        project_root = str(
            getattr(getattr(self, "save_manager", None), "project_root", "")
            or os.environ.get("PYTO_RPG_PROJECT_ROOT", "")
            or os.path.dirname(os.path.abspath(__file__))
        )
        project_root = os.path.realpath(os.path.abspath(project_root))
        maps_root = os.path.realpath(os.path.join(project_root, "maps"))
        target = os.path.realpath(os.path.join(maps_root, "editor_map.json"))
        try:
            inside = os.path.commonpath((target, maps_root)) == maps_root
        except Exception:
            inside = False
        return target if inside and os.path.isfile(target) else ""

    def _build_fresh_new_game(self, target_path):
        """Factory seam used by the worker and deterministic failure tests."""
        return GameApp(
            use_editor_map=True,
            map_path=target_path,
            skip_auto_load=True,
            game_settings=self.game_settings,
        )

    def _prepare_new_game_worker(self, serial, target_path):
        """Build and persist a pristine session without touching live state."""
        fresh = None
        save_path = ""
        error = None
        try:
            if not target_path:
                raise FileNotFoundError("找不到主地圖 maps/editor_map.json")
            fresh = self._build_fresh_new_game(target_path)
            loaded = str(getattr(getattr(fresh, "map_loader", None), "path", "") or "")
            if (
                not bool(getattr(fresh, "custom_map_loaded", False))
                or not loaded
                or not self._same_authored_map(loaded, target_path)
            ):
                raise RuntimeError("主地圖驗證失敗")

            # Atomic os.replace writes a clean baseline save. This resets the
            # persistent slot without a window containing partial JSON.
            save_path = fresh.save_manager.replace_with_clean_game(fresh)
        except Exception as exc:
            error = repr(exc)

        result = (int(serial), fresh, str(save_path or ""), error)
        try:
            with self._new_game_result_lock:
                if int(getattr(self, "_new_game_request_serial", -1)) == int(serial):
                    self._new_game_pending_result = result
        except Exception as exc:
            print("NEW GAME worker handoff error:", repr(exc))

    def request_new_game(self, now=None, confirmed=False, study_on_start=True):
        """Arm once, then start a clean New Game on the second tap.

        The four-second confirmation window prevents a stray top-row touch
        from deleting progress. Both taps are consumed on the gameplay thread;
        the UIKit callback itself only enqueues ``new_game`` and returns.
        """
        if bool(getattr(self, "_new_game_in_progress", False)):
            self.events.emit("message", text="新遊戲正在建立，請稍候")
            return False

        stamp = time.monotonic() if now is None else float(now)
        deadline = float(getattr(self, "_new_game_confirm_deadline", 0.0))
        if (not bool(confirmed)) and (deadline <= 0.0 or stamp > deadline):
            self._new_game_confirm_deadline = stamp + 4.0
            self.events.emit(
                "message",
                text="警告：新遊戲會重置存檔、物品與世界；4 秒內再按一次「新」確認",
            )
            return False

        self._new_game_confirm_deadline = 0.0
        self._new_game_in_progress = True
        # A death round already paid for the immediately following restart.
        # Ordinary New Game must require a fresh entry round after adoption.
        self._new_game_study_required = bool(study_on_start)
        self._new_game_pending_result = None
        self._new_game_request_serial = int(
            getattr(self, "_new_game_request_serial", 0)
        ) + 1
        serial = int(self._new_game_request_serial)
        target_path = self._default_main_map_path()
        try:
            self.input.reset()
        except Exception:
            pass
        self._request_platform_controls_reset()
        self.events.emit("message", text="正在建立全新的主世界…")

        worker = threading.Thread(
            target=self._prepare_new_game_worker,
            args=(serial, target_path),
            name="PytoRPG-NewGame",
            daemon=True,
        )
        self._new_game_worker = worker
        try:
            worker.start()
        except Exception as exc:
            self._new_game_in_progress = False
            self._new_game_last_result = {"ok": False, "error": repr(exc)}
            self.events.emit("message", text="無法建立新遊戲：" + str(exc))
            return False
        return True

    def _rebind_runtime_event_bus(self, event_bus):
        """Keep Host audio/message subscribers across object adoption."""
        self.events = event_bus
        for name in (
            "inventory", "equipment", "interactions", "environment",
            "magic", "tools", "melee", "creature_system",
        ):
            obj = getattr(self, name, None)
            if obj is not None and hasattr(obj, "events"):
                try:
                    obj.events = event_bus
                except Exception:
                    pass
        # Creature VFX is a presentation child of CreatureSystem and keeps its
        # own EventBus reference.  Rebind it explicitly so a New Game adoption
        # preserves Host audio/feedback subscribers instead of emitting into
        # the discarded worker GameApp bus.
        try:
            self.creature_system.creature_vfx_system.event_bus = event_bus
        except Exception:
            pass

    def _service_new_game_transition(self):
        """Adopt a worker result at an authoritative frame boundary."""
        if not bool(getattr(self, "_new_game_in_progress", False)):
            return False
        result = None
        try:
            with self._new_game_result_lock:
                result = self._new_game_pending_result
                self._new_game_pending_result = None
        except Exception as exc:
            self._new_game_in_progress = False
            self._new_game_last_result = {"ok": False, "error": repr(exc)}
            self.events.emit("message", text="新遊戲切換失敗：" + str(exc))
            return False
        if result is None:
            return False

        serial, fresh, save_path, error = result
        if int(serial) != int(getattr(self, "_new_game_request_serial", -1)):
            return False
        if error or fresh is None:
            self._new_game_in_progress = False
            self._new_game_worker = None
            self._new_game_last_result = {"ok": False, "error": str(error or "未知錯誤")}
            self.events.emit("message", text="建立新遊戲失敗：" + str(error or "未知錯誤"))
            return False

        study_on_start = bool(getattr(self, "_new_game_study_required", True))
        old_input = self.input
        old_events = self.events
        old_view = (float(self.viewport_w), float(self.viewport_h))
        shared_assets = getattr(self, "assets", None)
        host_names = (
            "physics_debug", "input_backend_name", "render_alpha",
            "loop_stage", "last_render_error", "audio_debug",
            "_platform_controls_reset_serial", "study", "playtime_guard",
        )
        host_owned = {
            key: getattr(self, key) for key in host_names if hasattr(self, key)
        }

        fresh.input = old_input
        if shared_assets is not None:
            fresh.assets = shared_assets
        fresh.set_viewport(*old_view)
        adopted = dict(fresh.__dict__)
        self.__dict__.clear()
        self.__dict__.update(adopted)
        self.input = old_input
        if shared_assets is not None:
            self.assets = shared_assets
        for key, value in host_owned.items():
            setattr(self, key, value)

        self._rebind_runtime_event_bus(old_events)
        # Preserve subscribers (AudioManager/UI) but discard notices queued by
        # the erased session so only the New Game completion message remains.
        try:
            old_events._messages.clear()
        except Exception:
            pass
        self._map_session_states = {}
        self.current_map_path = self._default_main_map_path()
        self._rebind_after_map_adopt()
        self._reset_transients_after_manual_load()
        self._finalize_manual_load()
        self.auto_loaded_save = False
        self.auto_load_info = ""
        self._new_game_in_progress = False
        self._new_game_worker = None
        self._new_game_last_result = {
            "ok": True,
            "save_path": str(save_path),
            "map_path": str(self.current_map_path),
        }
        self.study.death_latched = False
        if study_on_start:
            self.study.enter_game(force=True)
            self._cancel_study_input(clear_queue=True)
            self._request_platform_controls_reset()
        self.events.emit("message", text="新遊戲已開始：物品、世界與生物已重置")
        return True

    def _finalize_manual_load(self):
        p=self.player
        self.death_pending=False
        self._sync_player_death_policy()
        # Loading is not confirmation for a previously armed destructive New
        # Game command. Require a fresh two-tap sequence after every load.
        self._new_game_confirm_deadline=0.0
        p.prev_x=float(p.x); p.prev_y=float(p.y)
        p.vx=0.0; p.vy=0.0
        self.map_transition_alpha=0.0; self.map_transition_phase=""
        self._pending_portal=None; self._portal_cooldown=0.75
        self._portal_requires_exit=False; self._portal_post_lock=0.18
        self._clear_portal_prompt_latch()
        try:self.input.reset()
        except Exception:pass
        self._request_platform_controls_reset()
        self._snap_camera_to_player()
        self.chunk_streamer.update(p.x,p.y)
        self.aim_dir_x=float(p.facing if p.facing!=0 else 1); self.aim_dir_y=0.0
        self._refresh_aim_screen_position()

    def _restart_current_map_safely(self):
        """Rebuild the current authored map at its validated spawn point."""
        current=str(
            getattr(self,"current_map_path","")
            or getattr(getattr(self,"map_runtime",None),"source_path","")
            or getattr(getattr(self,"map_loader",None),"path","")
            or ""
        )
        if current and self._adopt_clean_map(current):
            self._finalize_manual_load()
            return True

        # Defensive fallback for the legacy generated test world.  Its
        # authoritative respawn point is already clamped during construction.
        p=self.player
        p.x=float(self.respawn_x); p.y=float(self.respawn_y)
        self.save_manager._repair_player_if_embedded(self)
        self._finalize_manual_load()
        return True

    def _resolve_safe_portal_arrival(self, fresh, target_row, authored_arrival=None):
        """Return a standing point outside the destination portal trigger.

        Older FIX32/FIX33 maps authored ``arrival=[portal_x,floor]``.  Because
        Player.y is the foot coordinate and the trigger probes y-2, that point
        is still inside the doorway's final trigger row.  Resolve a nearby
        horizontal landing with solid floor + two air cells.  This also makes
        legacy/custom maps safe without requiring regeneration.
        """
        try:
            rect=list((target_row or {}).get("rect",()) or ())
            x0,y0,w,h=[int(v) for v in rect[:4]]
            floor=int(y0+h)
        except Exception:
            x0=y0=0;w=h=0
            try:floor=int(authored_arrival[1])
            except Exception:floor=1

        world=fresh.world
        width=max(1,int(getattr(world,"width_tiles",1)))
        height=max(1,int(getattr(world,"height_tiles",1)))

        def solid(tx,ty):
            if tx<0 or tx>=width or ty<0 or ty>=height:
                return True
            try:
                from world.tile_registry import tile_def
                return bool(tile_def(world.get_tile(tx,ty)).solid)
            except Exception:
                try:return bool(world.get_tile(tx,ty))
                except Exception:return True

        def fits(tx,fy):
            return (
                1<=tx<width-1 and 2<=fy<height
                and solid(tx,fy)
                and not solid(tx,fy-1)
                and not solid(tx,fy-2)
            )

        center=int(x0+w//2) if w else None
        candidates=[]
        if center is not None:
            primary=1 if center < width*0.5 else -1
            for side in (primary,-primary):
                edge=(x0+w) if side>0 else (x0-1)
                for extra in (1,2,3,4):
                    candidates.append((edge+side*(extra-1),floor))
        if isinstance(authored_arrival,(list,tuple)) and len(authored_arrival)>=2:
            try:candidates.append((int(authored_arrival[0]),int(authored_arrival[1])))
            except Exception:pass

        expanded=[]
        for tx,fy in candidates:
            for dy in (0,-1,1,-2,2):
                expanded.append((tx,fy+dy))

        try:
            portal_rows=tuple(fresh._portal_rows())
        except Exception:
            portal_rows=(target_row,) if target_row else ()

        def safe(tx,fy):
            tx=int(tx);fy=int(fy)
            if not fits(tx,fy):
                return False
            # A player is wider than a point and taller than one tile. Checking
            # only this column's floor-1/floor-2 cells can select a coordinate
            # whose shoulder intersects a neighbouring wall. Validate the real
            # standing bbox against every nearby solid rectangle; mere contact
            # with the supporting floor edge remains legal.
            try:
                px=(tx+.5)*TILE_SIZE
                py=world.solid_top_y(tx,fy)
                if py is None:
                    return False
                bbox=fresh.player.bbox(x=px,y=py)
                world_w=width*TILE_SIZE;world_h=height*TILE_SIZE
                if bbox[0]<0.0 or bbox[1]<0.0 or bbox[2]>world_w or bbox[3]>world_h:
                    return False
                for _sx,_sy,_tile_id,solid_rect in world.solid_cells_in_rect(bbox,padding=1):
                    if self._rect_overlap_strict(bbox,solid_rect,eps=.05):
                        return False
            except Exception:
                return False
            return not any(
                fresh._portal_contains_player(row,x=px,y=py)
                for row in portal_rows if isinstance(row,dict)
            )

        checked=set()
        for tx,fy in expanded:
            key=(int(tx),int(fy))
            if key in checked:continue
            checked.add(key)
            if safe(*key):return key

        # Legacy/custom maps may place rock immediately outside the authored
        # doorway.  Search a bounded Manhattan ring around both the doorway
        # and the map's validated spawn instead of returning an unchecked tile.
        origins=[]
        if center is not None:origins.append((center,floor))
        if isinstance(authored_arrival,(list,tuple)) and len(authored_arrival)>=2:
            try:origins.append((int(authored_arrival[0]),int(authored_arrival[1])))
            except Exception:pass
        try:
            origins.append((int(float(fresh.respawn_x)//TILE_SIZE),int(round(float(fresh.respawn_y)/TILE_SIZE))))
        except Exception:
            pass
        for ox,oy in origins:
            for radius in range(0,25):
                for dx in range(-radius,radius+1):
                    dy_abs=radius-abs(dx)
                    dys=(0,) if dy_abs==0 else (-dy_abs,dy_abs)
                    for dy in dys:
                        key=(int(ox+dx),int(oy+dy))
                        if key in checked:continue
                        checked.add(key)
                        if safe(*key):return key

        # Session terrain can block both the doorway and the authored respawn.
        # One final O(world) scan is acceptable during a rare map switch and is
        # safer than ever returning an unchecked/embedded coordinate.
        best=None
        scoring_origins=origins or [(center if center is not None else 1,floor)]
        for fy in range(2,height):
            for tx in range(1,width-1):
                if not safe(tx,fy):continue
                score=min(abs(tx-ox)+abs(fy-oy) for ox,oy in scoring_origins)
                candidate=(score,abs(fy-floor),tx,fy)
                if best is None or candidate<best:best=candidate
        return None if best is None else (int(best[2]),int(best[3]))

    def _perform_map_switch(self):
        portal=self._resolved_portal_row(dict(self._pending_portal or {})) or {}
        target=str(portal.get("target_map","") or "").strip()
        if not target:return False
        target_path=self._portal_target_path(target)
        if not target_path:
            return self._reject_portal_transition(
                "unsafe target "+repr(target),
                "傳送門目的地不安全，已留在目前區域",
            )
        if not os.path.isfile(target_path):
            return self._reject_portal_transition(
                "target missing "+target_path,
                "目的地尚未開放，已留在目前區域",
            )
        # Snapshot the map we are leaving before constructing the destination.
        # The same dictionary is carried across every adopted GameApp.
        try:self._capture_current_map_session_state()
        except Exception as exc:print("PORTAL map-state capture skipped:",repr(exc))
        map_session_states=getattr(self,"_map_session_states",{})
        old_input=self.input
        # UIKit/AudioManager subscribe to this exact EventBus object. A portal
        # adopts fresh gameplay state but must not strand those subscribers on
        # the discarded map shell.
        old_events=self.events
        old_view=(float(self.viewport_w),float(self.viewport_h))
        # Keep the exact registry that is already rendering the player's edited
        # pixel art. Replacing it during a portal switch could make the Metal
        # renderer fall back to the yellow debug body on-device.
        shared_assets=getattr(self,"assets",None)
        # FIX33: iOS Host attaches diagnostics/backend state *after* GameApp
        # construction.  FIX32 replaced __dict__ with a fresh GameApp during
        # portal travel and accidentally deleted physics_debug, causing the
        # exact post-portal crash seen on device.  Preserve Host-owned fields
        # across every map adoption.
        _host_owned_names=(
            "physics_debug","input_backend_name","render_alpha",
            "loop_stage","last_render_error","audio_debug","_platform_controls_reset_serial",
            "study", "playtime_guard",
        )
        host_owned={k:getattr(self,k) for k in _host_owned_names if hasattr(self,k)}
        player_state={k:getattr(self.player,k,None) for k in ("hp","stamina","mana","run_mode","facing")}
        inv=self.inventory.export_state()
        gear_state=self.equipment.export_state()
        time_state=self.time.export_state()
        opened_chests=set(getattr(self,"opened_chest_ids",set()) or set())
        recovery_chests=self._export_recovery_chests()
        equipped_weapon=str(getattr(self.melee,"selected_weapon","sword") or "sword")
        selected_magic=str(getattr(self.magic,"selected_magic",getattr(self.magic,"MAGIC_FIRE","fire")))
        selected_tool=str(getattr(self.tools,"selected_tool","") or "")
        old_action=str(getattr(self,"action_mode",self.ACTION_TOOL))
        try:
            fresh=GameApp(
                use_editor_map=True, map_path=target_path, skip_auto_load=True,
                game_settings=self.game_settings,
            )
        except Exception as exc:
            print("PORTAL target rejected:",target_path,repr(exc))
            return self._reject_portal_transition(
                repr(exc),"目的地地圖損壞，已留在目前區域"
            )
        loaded_path=str(getattr(getattr(fresh,"map_loader",None),"path","") or "")
        if (
            not bool(getattr(fresh,"custom_map_loaded",False)) or
            not loaded_path or not self._same_authored_map(loaded_path,target_path)
        ):
            print("PORTAL target validation failed:",target_path,loaded_path)
            return self._reject_portal_transition(
                "map validation failed", "目的地地圖無效，已留在目前區域"
            )
        fresh.input=old_input
        fresh._map_session_states=map_session_states
        if shared_assets is not None:
            fresh.assets=shared_assets
        self._restore_map_session_state(fresh,target_path)
        fresh.inventory.import_state(inv)
        fresh.equipment.import_state(gear_state)
        fresh.equipment.sync_player(fresh.player)
        fresh.opened_chest_ids=opened_chests
        fresh._apply_opened_chest_state()
        fresh._import_recovery_chests(recovery_chests)
        fresh.time.import_state(time_state)
        try:fresh.melee.equip_weapon(equipped_weapon)
        except Exception:pass
        try:fresh.magic.set_magic(selected_magic)
        except Exception:pass
        try:
            if selected_tool in tuple(getattr(fresh.tools,"ORDER",()) or ()):fresh.tools.selected_tool=selected_tool
        except Exception:pass
        if old_action in (fresh.ACTION_TOOL,fresh.ACTION_MAGIC,fresh.ACTION_WEAPON):fresh.action_mode=old_action
        try:fresh.tools.set_action_mode_active(fresh.action_mode==fresh.ACTION_TOOL)
        except Exception:pass
        for k,v in player_state.items():
            if v is not None:setattr(fresh.player,k,v)
        # FIX65: map adoption must not restore a legacy walk-speed state.
        fresh.player.run_mode=True
        target_id=str(portal.get("target_portal","") or "")
        arrival=None
        target_row=None
        for row in fresh._portal_rows():
            if isinstance(row,dict) and str(row.get("id","") or "")==target_id:
                target_row=row
                arrival=row.get("arrival")
                break
        if target_row is None:
            print("PORTAL target id missing:",target_id,"in",target_path)
            return self._reject_portal_transition(
                "target portal missing "+target_id,
                "目的地入口資料缺失，已留在目前區域",
            )
        try:
            resolved=self._resolve_safe_portal_arrival(fresh,target_row,arrival)
            if resolved is None:raise RuntimeError("destination has no safe standing cell")
            atx,aty=resolved
            fresh.player.x=(int(atx)+0.5)*TILE_SIZE
            fresh.player.y=float(fresh.world.solid_top_y(int(atx),int(aty)))
            fresh.player.grounded=bool(fresh.physics.grounded(fresh.player))
            fresh.player.prev_x=fresh.player.x;fresh.player.prev_y=fresh.player.y
            fresh.player.vx=0.0;fresh.player.vy=0.0
            fresh.respawn_x=fresh.player.x;fresh.respawn_y=fresh.player.y
            player_box=fresh.player.bbox()
            if any(
                self._rect_overlap_strict(player_box,solid_rect,eps=.05)
                for _tx,_ty,_tile_id,solid_rect
                in fresh.world.solid_cells_in_rect(player_box,padding=1)
            ):
                raise RuntimeError("resolved portal arrival intersects terrain")
        except Exception as exc:
            print("PORTAL safe arrival rejected:",repr(exc))
            return self._reject_portal_transition(
                repr(exc),"目的地沒有安全落點，已留在目前區域"
            )
        fresh.set_viewport(*old_view)
        fresh._snap_camera_to_player()
        # Adopt the freshly built map while keeping this GameApp object alive;
        # UIKit/Metal/controls all retain references to this identity.
        adopted=dict(fresh.__dict__)
        self.__dict__.clear();self.__dict__.update(adopted)
        self.input=old_input
        self._map_session_states=map_session_states
        if shared_assets is not None:
            self.assets=shared_assets
        for _name,_value in host_owned.items():
            setattr(self,_name,_value)
        self._rebind_runtime_event_bus(old_events)
        self.map_transition_alpha=1.0
        self.map_transition_phase="fade_in"
        self._pending_portal=None
        self._portal_cooldown=1.0
        # Safe arrivals are normally outside the destination trigger. In that
        # case there is no reason to keep a stale "must exit" latch, which was
        # especially confusing after returning to the pyramid entrance.
        try:
            self._portal_requires_exit=any(
                self._portal_contains_player(row) for row in self._portal_rows()
            )
        except Exception:
            self._portal_requires_exit=False
        self._portal_post_lock=0.22
        self._clear_portal_prompt_latch()
        try:self.input.reset()
        except Exception:pass
        self._request_platform_controls_reset()
        self.current_map_path=target_path
        self._rebind_after_map_adopt()
        return True

    def _update_map_transition(self, dt):
        self._portal_cooldown=max(0.0,float(getattr(self,"_portal_cooldown",0.0))-max(0.0,float(dt)))
        phase=str(getattr(self,"map_transition_phase","") or "")
        if not phase:return False
        speed=2.7
        if phase=="fade_out":
            self.map_transition_alpha=min(1.0,float(self.map_transition_alpha)+float(dt)*speed)
            if self.map_transition_alpha>=0.999:
                if not self._perform_map_switch():
                    self.map_transition_phase="fade_in"
            return True
        if phase=="fade_in":
            self.map_transition_alpha=max(0.0,float(self.map_transition_alpha)-float(dt)*speed)
            if self.map_transition_alpha<=0.001:
                self.map_transition_alpha=0.0;self.map_transition_phase=""
            return True
        return False

    def _snap_camera_to_player(self):
        """Snap the camera after the real fullscreen viewport is known."""
        world_w = self.world.width_tiles * TILE_SIZE
        world_h = self.world.height_tiles * TILE_SIZE
        self.camera_x = clamp(
            float(self.player.x) - float(self.viewport_w) * 0.50,
            0.0,
            max(0.0, world_w - float(self.viewport_w)),
        )
        self.camera_y = clamp(
            float(self.player.y) - float(self.viewport_h) * CAMERA_VERTICAL_LOWER,
            0.0,
            max(0.0, world_h - float(self.viewport_h)),
        )
        self.prev_camera_x = float(self.camera_x)
        self.prev_camera_y = float(self.camera_y)

    def set_viewport(self, width, height):
        self.viewport_w = max(
            1.0,
            float(
                width
            ),
        )
        self.viewport_h = max(
            1.0,
            float(
                height
            ),
        )

        if self.custom_map_loaded:
            self._snap_camera_to_player()

        self._refresh_aim_screen_position()

    def _aim_origin_world(self):
        return (
            float(
                self.player.x
            ),
            float(
                self.player.y
            )
            - self.player.height()
            * 0.45,
        )

    def _normalized_aim_direction(self):
        dx = float(
            self.aim_dir_x
        )
        dy = float(
            self.aim_dir_y
        )

        length = math.hypot(
            dx,
            dy,
        )

        if length < 1e-8:
            dx = float(
                self.player.facing
                if self.player.facing != 0
                else 1
            )
            dy = 0.0
            length = 1.0

        return (
            dx / length,
            dy / length,
        )

    def aim_direction(self):
        return self._normalized_aim_direction()

    def aim_reticle_world(self):
        """Visible crosshair position.

        The reticle always stays on a fixed circle around the player.
        """
        origin_x, origin_y = (
            self._aim_origin_world()
        )
        dx, dy = (
            self._normalized_aim_direction()
        )

        return (
            float(
                origin_x
                + dx
                * AIM_RETICLE_RADIUS
            ),
            float(
                origin_y
                + dy
                * AIM_RETICLE_RADIUS
            ),
        )

    def aim_world_target(self):
        """Direction-only gameplay target.

        The returned point is at a fixed virtual distance and exists only so
        existing ballistic/tool code can consume a target point. Joystick
        magnitude and visible reticle distance do not affect it.
        """
        origin_x, origin_y = (
            self._aim_origin_world()
        )
        dx, dy = (
            self._normalized_aim_direction()
        )

        return (
            float(
                origin_x
                + dx
                * AIM_MAGIC_DIRECTION_DISTANCE
            ),
            float(
                origin_y
                + dy
                * AIM_MAGIC_DIRECTION_DISTANCE
            ),
        )

    def _refresh_aim_screen_position(self):
        reticle_x, reticle_y = (
            self.aim_reticle_world()
        )

        self.aim_screen_x = float(
            reticle_x
            - self.camera_x
        )
        self.aim_screen_y = float(
            reticle_y
            - self.camera_y
        )

    def begin_magic_aim(self):
        if self.aim_active:
            return False
        self.aim_active = True
        self._aim_shot_signature = self._ranged_aim_signature()
        self._aim_has_direction = False
        self._aim_hold_seconds = 0.0
        self.magic_aim_charge_seconds = 0.0
        # Do not alter the current reticle direction on touch-down. The first
        # non-deadzone stick vector becomes a target and is approached smoothly.
        self.aim_axis_x = 0.0
        self.aim_axis_y = 0.0
        self._refresh_aim_screen_position()

    def set_magic_aim_axis(self, x, y):
        """Set the ABSOLUTE joystick direction target without snapping.

        UIKit calls this with the knob's normalized displacement from joystick
        center. Magnitude is ignored for gameplay; only angle matters. The
        simulation thread applies a rate-limited angular transition.
        """
        try:
            dx, dy = float(x), float(y)
        except (TypeError, ValueError, OverflowError):
            return
        length = math.hypot(dx, dy)
        if not math.isfinite(length) or length < AIM_SOFT_SNAP_EPSILON:
            return

        dx /= length
        dy /= length
        self.aim_axis_x = dx
        self.aim_axis_y = dy
        if self.aim_active:
            self._aim_has_direction = True

        with self._command_lock:
            self._aim_target_x = dx
            self._aim_target_y = dy
            self._aim_target_valid = True

    def _apply_soft_absolute_aim(self, dt):
        """Rotate the reticle toward the stick's absolute direction target.

        This keeps absolute stick semantics after the short transition: once the
        crosshair catches up, it is exactly aligned with the joystick direction.
        """
        with self._command_lock:
            if not self._aim_target_valid:
                return
            tx = float(self._aim_target_x)
            ty = float(self._aim_target_y)

        tlen = math.hypot(tx, ty)
        if tlen < AIM_SOFT_SNAP_EPSILON:
            return
        tx /= tlen
        ty /= tlen

        cx, cy = self._normalized_aim_direction()
        current_angle = math.atan2(cy, cx)
        target_angle = math.atan2(ty, tx)
        delta = (target_angle - current_angle + math.pi) % (2.0 * math.pi) - math.pi

        max_step = math.radians(AIM_SOFT_SNAP_TURN_SPEED_DEG) * max(0.0, float(dt))
        lock_angle = math.radians(AIM_SOFT_SNAP_LOCK_DEG)

        if abs(delta) <= max(max_step, lock_angle):
            self.aim_dir_x = tx
            self.aim_dir_y = ty
            return

        new_angle = current_angle + math.copysign(max_step, delta)
        self.aim_dir_x = math.cos(new_angle)
        self.aim_dir_y = math.sin(new_angle)

    def set_aim_direction_to_world(
        self,
        world_x,
        world_y,
    ):
        """Convenience helper for tests/debug/tool scripts."""
        origin_x, origin_y = (
            self._aim_origin_world()
        )

        dx = float(
            world_x
        ) - origin_x
        dy = float(
            world_y
        ) - origin_y

        length = math.hypot(
            dx,
            dy,
        )

        if length < 1e-8:
            return

        self.aim_dir_x = (
            dx
            / length
        )
        self.aim_dir_y = (
            dy
            / length
        )
        with self._command_lock:
            self._aim_target_x = self.aim_dir_x
            self._aim_target_y = self.aim_dir_y
            self._aim_target_valid = True
        self._refresh_aim_screen_position()

    def _ranged_aim_signature(self):
        """An aim gesture may only fire the ranged mode it started with."""
        mode = self.action_mode
        if mode == self.ACTION_MAGIC:
            return (mode, str(getattr(self.magic, "selected_magic", "")))
        if mode == self.ACTION_WEAPON and str(self.melee.selected_weapon) in ("energy_bow", "bow"):
            return (mode, str(self.melee.selected_weapon))
        return None

    def _cancel_aim_release(self):
        """Disarm a pending right-stick shot without changing movement/reticle."""
        self._aim_shot_signature = None
        self._aim_has_direction = False
        self._aim_hold_seconds = 0.0

    def _process_move_stick_events(self, dt):
        """FIX85 compatibility no-op: left events must NEVER aim or attack.

        Player reads the independent InputManager axis snapshots, including
        FIX84 ladder+jump combinations. Retained for old diagnostic callers.
        """
        return None

    def release_magic_aim(self, held_seconds=None, fire=True):
        """Finish one right-stick gesture; real release fires magic/bow once.

        UI callbacks only enqueue commands. This method runs on the simulation
        thread, with the same charge/cooldown/mana path as the action button.
        """
        if held_seconds is None:
            held_seconds = self._aim_hold_seconds
        try:
            held_seconds = float(held_seconds)
            if not math.isfinite(held_seconds):
                fire = False
                held_seconds = 0.0
        except (TypeError, ValueError, OverflowError):
            fire = False
            held_seconds = 0.0
        held_seconds = max(0.0, min(2.0, held_seconds))
        signature = self._ranged_aim_signature()
        should_fire = bool(
            fire and self.aim_active and self._aim_has_direction
            and signature is not None and signature == self._aim_shot_signature
            and self.study_countable()
            and not getattr(self, "_input_modal_blocked", False)
        )
        dx, dy = self.aim_axis_x, self.aim_axis_y
        # Disarm BEFORE dispatch: duplicate pan-ended / touch-up callbacks
        # cannot cast a second shot or leave a charge held indefinitely.
        self.aim_active = False
        self._cancel_aim_release()
        self.aim_axis_x = 0.0
        self.aim_axis_y = 0.0
        self.magic_aim_charge_seconds = 0.0
        self.weapon_charge_seconds = 0.0
        if should_fire:
            length = math.hypot(dx, dy)
            if math.isfinite(length) and length > AIM_SOFT_SNAP_EPSILON:
                # A fast release commits the final right-stick direction, not
                # the earlier angle in the short visual smoothing transition.
                self.aim_dir_x, self.aim_dir_y = dx / length, dy / length
                with self._command_lock:
                    self._aim_target_x = self.aim_dir_x
                    self._aim_target_y = self.aim_dir_y
                    self._aim_target_valid = True
                self._right_stick_shot_this_frame = True
                self._trigger_action(charge_seconds=held_seconds)
            else:
                should_fire = False
        self._refresh_aim_screen_position()
        return should_fire

    @property
    def action_mode_name(self):
        return {
            self.ACTION_TOOL: "工具",
            self.ACTION_MAGIC: "魔法",
            self.ACTION_WEAPON: self.melee.selected_name,
        }.get(self.action_mode, "工具")

    def action_mode_ui_state(self):
        """Renderer-neutral description for the small attack-mode button."""
        mode=str(getattr(self,"action_mode",self.ACTION_TOOL) or self.ACTION_TOOL)
        if mode==self.ACTION_WEAPON:
            wid=str(getattr(self.melee,"selected_weapon","sword") or "sword")
            return {
                "mode":"weapon",
                "name":str(getattr(self.melee,"selected_name","武器") or "武器"),
                "title":"",
                "weapon":wid,
            }
        if mode==self.ACTION_MAGIC:
            mid=str(getattr(self.magic,"selected_magic","fireball") or "fireball")
            return {"mode":"magic","name":"魔法","title":"","magic":mid}
        tid=str(getattr(self.tools,"selected_tool","pickaxe") or "pickaxe")
        return {"mode":"tool","name":"工具","title":"","tool":tid}

    def toggle_action_mode(self, steps=1):
        self._cancel_aim_release()
        order = [self.ACTION_TOOL, self.ACTION_MAGIC]
        if self.inventory.first_owned_weapon():
            order.append(self.ACTION_WEAPON)
        order = tuple(order)
        try:
            index = order.index(self.action_mode)
        except ValueError:
            index = 0
        try:
            step_count = max(1, int(steps))
        except Exception:
            step_count = 1
        self.action_mode = order[(index + step_count) % len(order)]
        self.tools.set_action_mode_active(self.action_mode == self.ACTION_TOOL)
        self.magic_aim_charge_seconds = 0.0
        self.weapon_charge_seconds = 0.0
        self.action_debug["mode"] = self.action_mode
        self.events.emit(
            "message",
            text="攻擊模式：" + self.action_mode_name,
        )
        return self.action_mode_ui_state()

    def _commit_weapon_heavy_use(self, item_id, weapon_id):
        """Atomically spend one per-copy heavy use on the simulation thread."""
        result = self.inventory.commit_weapon_heavy_use(str(item_id or ""))
        if not bool(result.get("accepted", False)) or bool(result.get("unlimited", False)):
            return result
        row = WEAPON_DEFS.get(str(weapon_id or ""), {})
        name = str(row.get("name", "武器") or "武器")
        if bool(result.get("broken", False)):
            copies = max(0, int(result.get("count", 0) or 0))
            if copies > 0:
                self.events.emit(
                    "message",
                    text="%s耐久耗盡並消失；自動使用下一件（剩%d件）" % (name, copies),
                )
            else:
                self._pending_broken_weapon_fallback = {
                    "item_id": str(item_id or ""),
                    "weapon_id": str(weapon_id or ""),
                    "name": name,
                }
                self.events.emit("message", text=name + "耐久耗盡，武器已消失")
            try:
                self.events.emit(
                    "inventory_item_destroyed", item_id=str(item_id or ""),
                    name=name, reason="heavy_durability", count=1,
                )
            except Exception:
                pass
            # Boss rewards must be earned again, never duplicated into a free
            # recovery chest when their deliberately limited uses run out.
            recovery = (None if row.get("boss_exclusive") else
                        self._spawn_recovery_chest(str(item_id or ""), name))
            if recovery is not None:
                self.events.emit(
                    "message",
                    text="失去的%s已重新封入本區域的隨機寶箱" % name,
                )
        else:
            self.events.emit(
                "message",
                text="%s 重攻擊耐久 %d/%d" % (
                    name,
                    int(result.get("remaining", 0) or 0),
                    int(result.get("maximum", 10) or 10),
                ),
            )
        return result

    def _service_broken_weapon_fallback(self):
        """Switch only after the committed final attack finishes rendering."""
        pending = getattr(self, "_pending_broken_weapon_fallback", None)
        if not isinstance(pending, dict):
            return False
        if float(getattr(self.melee, "swing_timer", 0.0) or 0.0) > 0.0:
            return False
        broken_item = str(pending.get("item_id", "") or "")
        # A pickup during the final swing makes the same weapon usable again.
        if self.inventory.count_item(broken_item) > 0:
            self._pending_broken_weapon_fallback = None
            return True
        next_item = self.inventory.first_owned_weapon(exclude=(broken_item,))
        self._pending_broken_weapon_fallback = None
        if next_item and self.melee.equip_item(next_item):
            self.drone.sync_equipment(force=True)
            if hasattr(self, "boss_weapons"):
                self.boss_weapons.sync_equipment()
            self.events.emit(
                "weapon_equipped", weapon=str(self.melee.selected_weapon), icon="",
            )
            self.events.emit("message", text="自動切換：" + self.melee.selected_name)
            return True
        self.action_mode = self.ACTION_TOOL
        self.tools.set_action_mode_active(True)
        self.weapon_charge_seconds = 0.0
        self.events.emit("message", text="目前沒有武器，已切換為工具模式")
        return True

    def _trigger_action(self, charge_seconds=0.0):
        t0 = time.perf_counter()
        target_x, target_y = self.aim_world_target()

        if self.action_mode == self.ACTION_TOOL:
            self.tools.use_at(target_x, target_y)

        elif self.action_mode == self.ACTION_MAGIC:
            # FIX85: 動作 release and RIGHT aim-stick release share this
            # authoritative cast path and the original 1/2/3 charge thresholds.
            # The left movement stick never aims, charges, or fires.
            self.magic.cast_at(
                target_x,
                target_y,
                charge_seconds=max(0.0, float(charge_seconds)),
            )

        else:
            # Close-range weapons ignore the crosshair and always attack in
            # player.facing direction.
            if self.inventory.count_item(self.melee.selected_item_id) <= 0:
                self.action_mode = self.ACTION_TOOL
                self.tools.set_action_mode_active(True)
                self.weapon_charge_seconds = 0.0
                self.events.emit("message", text="目前沒有可用武器，已切換為工具模式")
                return False
            _charge=max(0.0,float(charge_seconds))
            _drone_weapon=str(getattr(self.melee,"selected_weapon",""))=="flying_drone"
            _accepted=self.melee.attack(charge_seconds=_charge)
            if _accepted and _drone_weapon:
                self.drone.queue_attack(heavy=(_charge>=float(self.melee.charge_threshold)))

        self.player.attack_timer = max(
            float(getattr(self.player, "attack_timer", 0.0)),
            0.08,
        )
        self.action_debug["mode"] = self.action_mode
        self.action_debug["count"] = int(self.action_debug.get("count", 0)) + 1
        self.action_debug["last_ms"] = (time.perf_counter() - t0) * 1000.0

    def toggle_tool(self, steps=1):
        self._cancel_aim_release()
        return self.tools.toggle_tool(steps=steps)

    def enqueue_ui_command(self, name, **payload):
        """Publish a UIKit command without touching gameplay state.

        FIX66 accumulates consecutive selector taps into one ``steps`` value.
        The simulation applies the exact modulo result once and emits one
        message, preventing a burst from becoming an ever-growing UI backlog.
        """
        command = str(name or "").strip()
        if command in ("toggle_tool", "toggle_magic", "toggle_action_mode"):
            data = dict(payload or {})
            try:
                data["steps"] = max(1, int(data.get("steps", 1)))
            except Exception:
                data["steps"] = 1
            return self.ui_commands.post(
                command,
                data,
                accumulate_key="steps",
                compact_through=(
                    "toggle_tool",
                    "toggle_magic",
                    "toggle_action_mode",
                ),
            )
        if command == "inventory_select":
            # Only the newest consecutive slot is meaningful before the modal
            # pump runs.  Preserve ordering against EQUIP/MOVE/DROP commands.
            return self.ui_commands.post(command, payload, coalesce=True)
        if command == "inventory_adjust":
            data = dict(payload or {})
            try:
                data["delta"] = int(data.get("delta", 0))
            except Exception:
                data["delta"] = 0
            return self.ui_commands.post(
                command, data, accumulate_key="delta"
            )
        return self.ui_commands.post(
            command,
            payload,
            coalesce=(command == "aim_axis"),
        )

    def _process_ui_commands(self):
        """Consume UIKit commands on the simulation thread in FIFO order."""
        handlers = {
            "toggle_tool": lambda p: self.toggle_tool(p.get("steps", 1)),
            "toggle_magic": lambda p: self.toggle_magic(p.get("steps", 1)),
            "toggle_action_mode": lambda p: self.toggle_action_mode(p.get("steps", 1)),
            "save": lambda _p: self.save_game(),
            "load": lambda _p: self.load_game(),
            "new_game": lambda _p: self.request_new_game(),
            "title_new_game": lambda _p: self.request_new_game(confirmed=True),
            "death_new_game": lambda _p: self.resolve_death_action("new_game"),
            "death_load_game": lambda _p: self.resolve_death_action("load_game"),
            "aim_begin": lambda _p: self.begin_magic_aim(),
            "aim_axis": lambda p: self.set_magic_aim_axis(p.get("x", 0.0), p.get("y", 0.0)),
            "aim_release": lambda p: self.release_magic_aim(p.get("held_seconds"), p.get("fire", False)),
            "debug_water": lambda _p: self.debug_add_water_ahead(),
            "debug_fire": lambda _p: self.debug_ignite_ahead(),
            "inventory_select": lambda p: self.inventory.select(p.get("index", 0)),
            "inventory_adjust": lambda p: self.inventory.adjust_quantity(p.get("delta", 0)),
            "inventory_max": lambda _p: self.inventory.set_max_quantity(),
            "inventory_move": lambda _p: (
                self.inventory.begin_move()
                if self.inventory.move_source is None
                else self.inventory.cancel_move()
            ),
            "inventory_cancel_move": lambda _p: self.inventory.cancel_move(),
            "inventory_equip": lambda _p: self.equip_selected_inventory_item(),
            "inventory_unequip": lambda _p: self.unequip_selected_inventory_item(),
            "inventory_drop": lambda _p: self._inventory_drop_command(),
            "inventory_remove": lambda _p: self._inventory_remove_command(),
        }
        processed = 0
        study = getattr(self, "study", None)
        # Remember the gate at batch entry. A resume command must not enable
        # stale world commands that were already queued behind the last answer.
        study_batch = bool(study is not None and study.active)
        guard = getattr(self, "playtime_guard", None)
        guard_batch = bool(guard is not None and guard.locked)
        for _sequence, name, payload in self.ui_commands.drain(limit=64):
            if name.startswith("playtime_"):
                if guard is not None and guard.handle(name, payload):
                    processed += 1
                continue
            # Unlock may not release gameplay/study inputs already queued behind
            # it. The next displayed modal owns the next fresh input batch.
            if guard_batch or bool(guard is not None and guard.locked):
                continue
            if name.startswith("study_"):
                if study is not None:
                    try:
                        if study.handle(name, payload):
                            processed += 1
                    except Exception as exc:
                        study.feedback = "操作未完成，請再試一次；學習進度保留。"
                        study.feedback_kind = "retry"
                        study._changed(new_token=True)
                        study._write_command_diagnostics(repr(exc))
                        print("STUDY pump recovered:", repr(exc))
                continue
            if study_batch or (study is not None and study.active):
                continue
            handler = handlers.get(name)
            if handler is None:
                print("UI COMMAND ignored:", name)
                continue
            try:
                handler(payload)
            except Exception as exc:
                print("UI COMMAND error:", name, repr(exc))
                self.events.emit("message", text="操作失敗：" + str(exc))
            processed += 1
        return processed

    def _selected_inventory_is_equipped(self):
        snap = self.inventory.snapshot()
        index = int(snap.get("selected_index", 0))
        slots = snap.get("slots", ())
        stack = slots[index] if 0 <= index < len(slots) else None
        if not stack:
            return False
        item_id=str(stack.get("item_id", "") or "")
        if item_id == str(self.melee.selected_item_id):
            return True
        try:
            return bool(self.equipment.is_equipped(item_id)) and not bool(
                self.equipment.can_remove_inventory_item(
                    item_id, int(snap.get("selected_quantity", 1))
                )
            )
        except Exception:
            return False

    def _inventory_drop_command(self):
        if self._selected_inventory_is_equipped():
            self.events.emit("message", text="目前使用中的武器／裝備不能全部丟棄，請先更換")
            return False
        return self.drop_selected_inventory()

    def _inventory_remove_command(self):
        if self._selected_inventory_is_equipped():
            self.events.emit("message", text="目前使用中的武器／裝備不能全部移除，請先更換")
            return False
        removed = self.inventory.remove_selected()
        if removed is not None:
            self.events.emit("message", text=f"移除 {removed.name} ×{removed.count}")
            return True
        return False

    def control_ui_state(self):
        """Immutable control snapshot for the UIKit main-thread renderer."""
        tool_name = str(getattr(self.tools, "selected_name", "鎬") or "鎬")
        tool_short = {"鏟子": "鎬", "十字鎬": "鎬", "鎬": "鎬", "斧頭": "斧", "鉤爪": "鉤"}.get(tool_name, "具")
        magic_name = str(getattr(self.magic, "selected_name", "火球") or "火球")
        return {
            "action_mode": dict(self.action_mode_ui_state()),
            "tool_id": str(getattr(self.tools, "selected_tool", "pickaxe") or "pickaxe"),
            "tool_name": tool_name,
            "tool_short": tool_short,
            "magic_name": magic_name,
            "magic_id": str(getattr(self.magic, "selected_magic", "fireball") or "fireball"),
        }

    def settings_snapshot(self):
        return dict(self.game_settings.snapshot())

    def set_game_setting(self, key, enabled):
        value = self.game_settings.set(str(key), enabled, persist=True)
        if str(key) == "study_intensity":
            self.study.configure_profile(value)
            self.events.emit("message", text="學習強度：" + profile_summary(value))
            return value
        if str(key) == "death_handling":
            self._sync_player_death_policy()
        labels = {
            "death_handling": "死亡處理",
            "starter_items": "新手道具",
        }
        self.events.emit(
            "message",
            text="%s：%s" % (labels.get(str(key), str(key)), "開啟" if value else "關閉"),
        )
        return value

    def toggle_game_setting(self, key):
        if str(key) == "study_intensity":
            return self.set_game_setting(key, (self.game_settings.study_intensity + 1) % 3)
        return self.set_game_setting(
            str(key), not bool(self.game_settings.values.get(str(key), False))
        )

    def _sync_player_death_policy(self):
        """Settings govern HP *before* menus or learning observe a death.

        Also repairs transient state from older releases without clearing any
        unfinished exercises, elapsed study time, decks, inventory or map data.
        """
        enabled = self.game_settings.death_handling_enabled
        self.player.configure_death_handling(enabled)
        if not enabled:
            was_pending = bool(getattr(self, "death_pending", False))
            self.death_pending = False
            study = getattr(self, "study", None)
            if study is not None:
                study.clear_death_context()
            if was_pending:
                self._request_platform_controls_reset()
        return enabled

    def resolve_death_action(self, action):
        """Keep the original death menu, but only after the learning gate."""
        # Ignore late UI callbacks after non-fatal mode was selected.
        if not self._sync_player_death_policy():
            return False
        if self.learning_blocked() or not bool(getattr(self, "death_pending", False)):
            return False
        action = str(action or "")
        if action == "new_game":
            self.death_pending = False
            ok = self.request_new_game(confirmed=True, study_on_start=False)
        elif action == "load_game":
            self.death_pending = False
            ok = self.load_game()
        else:
            return False
        if not ok and float(getattr(self.player, "hp", 0.0)) <= 0.0:
            self.death_pending = bool(self.game_settings.death_handling_enabled)
        return ok

    def _check_player_death(self):
        # FIX87: disabled means non-fatal gameplay, not merely a hidden menu.
        # Normalize legacy zero HP before StudyChallenge observes health. An
        # existing entry/timer/pending gate is still mandatory, never skipped.
        if not self._sync_player_death_policy():
            return bool(self.study.active)
        # The latch prevents a real death from creating a new round every frame.
        hp = float(getattr(self.player, "hp", 0.0))
        new_death = self.study.observe_health(hp)
        if hp > 0.0:
            return False
        if new_death:
            self._cancel_study_input(clear_queue=True)
            self._request_platform_controls_reset()
        if self.game_settings.death_handling_enabled:
            if not bool(getattr(self, "death_pending", False)):
                self.death_pending = True
                self.death_serial = int(getattr(self, "death_serial", 0)) + 1
                self.input.reset()
                self._request_platform_controls_reset()
                self.events.emit("message", text="角色已死亡，請先完成習題")
        return bool(self.study.active or getattr(self, "death_pending", False))

    def learning_blocked(self):
        return bool(self.study.active or self.playtime_guard.locked)

    def learning_overlay_snapshot(self):
        # Password gate has priority at the 30-minute boundary. The pending
        # study round remains untouched and appears immediately after unlock.
        return self.playtime_guard.snapshot() if self.playtime_guard.locked else self.study.snapshot()

    def study_countable(self):
        """Only ordinary playable world time counts toward the next challenge."""
        study = getattr(self, "study", None)
        return bool(
            study is not None and study.ready and not study.active
            and not self.playtime_guard.locked
            and study.entry_handled and float(getattr(self.player, "hp", 0.0)) > 0.0
            and not getattr(self, "death_pending", False)
            and not getattr(self, "_new_game_in_progress", False)
            and not getattr(self, "map_transition_phase", "")
            and float(getattr(self, "_portal_post_lock", 0.0)) <= 0.0
        )

    def _cancel_study_input(self, clear_queue=False):
        """Drop held actions, not projectiles/AI state; the whole world freezes."""
        self.input.reset()
        self._cancel_aim_release()
        self._attack_release_consumed = False
        self._right_stick_shot_this_frame = False
        if clear_queue:
            self.ui_commands.clear()
        with self._command_lock:
            self._magic_cast_queue.clear()
        self.weapon_charge_seconds = 0.0
        self.magic_aim_charge_seconds = 0.0
        self.aim_active = False
        self.aim_axis_x = 0.0
        self.aim_axis_y = 0.0
        self._aim_target_valid = False
        if hasattr(self.player, "attack_charge_seconds"):
            self.player.attack_charge_seconds = 0.0
        # Do not zero player velocity or alter monsters: an airborne frame
        # resumes exactly where it was interrupted, without simulating pause time.

    def advance_study_playtime(self, seconds):
        """Advance both independent clocks only as far as the first barrier."""
        self.playtime_guard.ensure_restored()
        if not self.study_countable():
            return False
        try:
            seconds = float(seconds)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(seconds) or seconds <= 0.0:
            return False
        remaining = max(0.0, self.study.interval - self.study.elapsed)
        if remaining <= 1e-8:
            started = self.study.require_event("timer")
            locked = False
        else:
            dt = min(seconds, remaining, max(0.0, LIMIT_SECONDS - self.playtime_guard.elapsed))
            # Both receive this interval even when one opens a modal. At tier 2
            # the password warning and the 30-minute study gate become due
            # together; unlocking cannot erase or answer the pending exercises.
            locked = self.playtime_guard.advance(dt)
            started = self.study.advance(dt)
        if started or locked:
            self._cancel_study_input(clear_queue=True)
            self._request_platform_controls_reset()
            self.playtime_guard.checkpoint(force=True)
        return bool(started or locked)

    def update(self, dt):
        # Apply even before modal/transition returns: a stale pending-death flag
        # from an earlier policy must not strand the world in a paused state.
        self._sync_player_death_policy()
        if self.study.entry_handled:
            self.playtime_guard.ensure_restored()
        self._right_stick_shot_this_frame = False
        # FIX78 enforces the same hard gate even without the iOS host. Commands
        # are pumped, but no entity/physics/weather/time subsystem may advance.
        if self.learning_blocked():
            self._process_ui_commands()
            self._cancel_study_input()
            return
        # Run before transition/portal early returns so Save/Load and button
        # state changes cannot become stranded during a fade or arrival lock.
        self._process_ui_commands()
        if self.learning_blocked():
            self._cancel_study_input()
            return
        if bool(getattr(self, "death_pending", False)):
            try:
                self.input.reset()
                self.input.begin_frame(); self.input.end_frame()
            except Exception:
                pass
            return
        if self._service_new_game_transition():
            try:
                self.input.reset()
                self.input.begin_frame(); self.input.end_frame()
            except Exception:
                pass
            return
        if bool(getattr(self, "_new_game_in_progress", False)):
            # The worker performs map construction and JSON I/O. The gameplay
            # thread remains responsive but holds the old simulation stable so
            # portals, attacks or terrain edits cannot race the confirmed reset.
            try:
                self.input.reset()
                self.input.begin_frame(); self.input.end_frame()
            except Exception:
                pass
            return
        if self._update_map_transition(dt):
            try:
                self.input.reset()
                self.input.begin_frame(); self.input.end_frame()
            except Exception: pass
            return
        _post=float(getattr(self,"_portal_post_lock",0.0))
        if _post>0.0:
            self._portal_post_lock=max(0.0,_post-max(0.0,float(dt)))
            self.player.vx=0.0;self.player.vy=0.0
            try:
                self.input.reset()
                self.input.begin_frame(); self.input.end_frame()
            except Exception:pass
            return
        # Restore after title/load/new-game/portal work, so a pending challenge
        # never consumes the title's intended world selection.
        self.playtime_guard.ensure_restored()
        self.study.enter_game()
        # Coalesce a dead save and entry into ONE round, before any world tick
        # or HP regeneration. A zero-HP save must not become alive invisibly.
        self._check_player_death()
        if self.learning_blocked() or self.death_pending:
            self._cancel_study_input(clear_queue=True)
            self._request_platform_controls_reset()
            return
        # Absolute world time advances on the authoritative fixed physics step.
        # This keeps day/night and ecology deterministic even if Metal FPS drops.
        self.time.update(dt)
        hp_before_damage = float(self.player.hp)

        # Snapshot UIKit input edges for this game frame.
        self.input.begin_frame()
        # Movement axes are consumed only by Player, never by ranged aiming.
        if self.aim_active and self._aim_shot_signature is not None:
            if self._ranged_aim_signature() != self._aim_shot_signature:
                self._cancel_aim_release()
            else:
                self._aim_hold_seconds = min(2.0, self._aim_hold_seconds + max(0.0, dt))

        # FIX46 action semantics:
        #   TOOL: press edge triggers immediately once.
        #   MAGIC: hold 動作 to charge, release once to cast.
        #   WEAPON: hold 動作, release once; short hold = normal attack,
        #           weapon-specific long hold = heavy attack.
        attack_pressed = self.input.just_pressed("attack")
        if attack_pressed:
            self._attack_release_consumed = False
        # Alternate fire controls must not double-fire one overlapping hold.
        if self._right_stick_shot_this_frame and (
            self.input.down("attack") or self.input.just_released("attack")
        ):
            self._attack_release_consumed = True
        attack_released = (self.input.just_released("attack")
                           and not self._attack_release_consumed)
        if attack_released and self._ranged_aim_signature() is not None:
            self._cancel_aim_release()
        if self.input.just_released("attack"):
            self._attack_release_consumed = False

        # Terraria-style latch: one press launches and the rope remains until
        # Jump, another action press, tool switch, or mode switch.  A short tap
        # must not recall the hook on its release edge.
        if self.input.just_pressed("jump"):
            try:self.tools.grapple.detach("jump", retract=False)
            except Exception:pass

        if self.action_mode == self.ACTION_MAGIC:
            self.weapon_charge_seconds = 0.0
            if attack_pressed:
                self.magic_aim_charge_seconds = 0.0

            if self.input.down("attack"):
                self.magic_aim_charge_seconds = min(
                    float(MAGIC_CHARGE_LEVEL3_MAX),
                    max(0.0, float(self.input.held_seconds("attack"))),
                )

            if attack_released:
                held = min(
                    float(MAGIC_CHARGE_LEVEL3_MAX),
                    max(0.0, float(self.input.released_duration("attack"))),
                )
                self.magic_aim_charge_seconds = held
                self._trigger_action(charge_seconds=held)
                self.magic_aim_charge_seconds = 0.0
        elif self.action_mode == self.ACTION_WEAPON:
            self.magic_aim_charge_seconds = 0.0
            if attack_pressed:
                self.weapon_charge_seconds = 0.0
            if self.input.down("attack"):
                # Do not clamp at the threshold so renderer/UI can still show
                # a fully charged state while the button remains held.
                self.weapon_charge_seconds = min(3.0, max(0.0, float(self.input.held_seconds("attack"))))
            if attack_released:
                held = min(3.0, max(0.0, float(self.input.released_duration("attack"))))
                self.weapon_charge_seconds = held
                self._trigger_action(charge_seconds=held)
                self.weapon_charge_seconds = 0.0
        else:
            self.magic_aim_charge_seconds = 0.0
            self.weapon_charge_seconds = 0.0
            if attack_pressed:
                self._trigger_action()

        # Show right-stick charging without coupling it to movement or hiding
        # a longer charge currently owned by the separate action button.
        if self.aim_active and self._aim_has_direction and self._aim_shot_signature is not None:
            if self.action_mode == self.ACTION_MAGIC:
                self.magic_aim_charge_seconds = max(self.magic_aim_charge_seconds, self._aim_hold_seconds)
            elif self.action_mode == self.ACTION_WEAPON:
                self.weapon_charge_seconds = max(self.weapon_charge_seconds, self._aim_hold_seconds)

        pending_casts = []
        with self._command_lock:
            if self._magic_cast_queue:
                pending_casts = self._magic_cast_queue
                self._magic_cast_queue = []

        for cast_x, cast_y, cast_hold in pending_casts:
            self.magic.cast_at(
                cast_x,
                cast_y,
                charge_seconds=cast_hold,
            )

        # Soft absolute aim follows ONLY the right stick. Either ranged fire
        # control owns its own charge; movement does not modify either one.
        self._apply_soft_absolute_aim(dt)

        # The reticle follows player/camera every frame but never changes radius.
        self._refresh_aim_screen_position()

        # Active chunk set is refreshed before and after movement.
        self.chunk_streamer.update(
            self.player.x,
            self.player.y,
        )

        self.map_runtime.update_player(
            self,
            dt,
        )

        self.player.update(
            dt,
            self.world,
            self.physics,
            self.input,
            self.events,
            self.environment,
        )

        # Grapple observes the post-physics player position and applies the
        # pull velocity before wrap/portal handling.  Mining cooldown shares
        # this fixed-step update without a second ToolSystem tick below.
        self.tools.update(dt)

        self._handle_horizontal_world_wrap()

        self.chunk_streamer.update(
            self.player.x,
            self.player.y,
        )

        # FIX37: lava contact uses the current-frame post-physics position.
        # Movement terrain effects remain in the pre-physics update above.
        self.map_runtime.update_player_lava(
            self,
            dt,
        )

        portal_used = self._check_map_portal_trigger()

        if (not portal_used) and self.input.just_pressed("interact"):
            self.interactions.interact(self.player)

        self._environment_accumulator += dt
        environment_steps = 0

        while (
            self._environment_accumulator + 1e-12 >= self._environment_step
            and environment_steps < PERF_ENVIRONMENT_MAX_STEPS
        ):
            self.environment.update(
                self._environment_step,
                self._environment_objects,
            )
            self._environment_accumulator -= self._environment_step
            environment_steps += 1

        # Never let ecology catch-up steal an unbounded frame after a stall.
        self._environment_accumulator = min(
            self._environment_accumulator,
            self._environment_step * PERF_ENVIRONMENT_MAX_STEPS,
        )

        # Environment phase changes can create ICE/IGNEOUS_ROCK after player
        # collision already ran this frame. Rescue before AI/combat proceeds.
        self._resolve_player_dynamic_solid_overlap()

        self.npc_ai.update(dt)
        self.creature_system.update(dt)
        self.map_runtime.update_creatures(self, dt)
        self.lava_spit.update(dt)
        self.pyramid_traps.update(dt)
        self.item_drop_system.update(dt)
        self.physics_object_system.update(dt)
        self.magic.update(dt)
        # Spell-driven freezing can also materialize solid ice this frame.
        self._resolve_player_dynamic_solid_overlap()
        self.melee.update(dt)
        self.drone.update(dt)
        self.boss_weapons.update(dt)
        self._service_broken_weapon_fallback()
        self.toxic.update(dt)
        self._update_health_regen(dt, hp_before_damage)
        if self._check_player_death():
            self.input.end_frame()
            return

        # Count the grace interval only after every damage system has observed
        # the current value for this frame.
        self.player.startup_damage_grace = max(
            0.0, float(getattr(self.player, "startup_damage_grace", 0.0)) - float(dt)
        )

        if ENGINEERING_HUD_ENABLED:
            self._audit_timer += dt
            if self._audit_timer >= 0.75:
                self._audit_timer = 0.0
                self.debug_audit = {
                    "matter": self.environment.matter.audit(self),
                    "energy": self.environment.energy.snapshot(),
                }

        if self.player.mana < MAX_MANA:
            self.player.mana = min(
                MAX_MANA,
                self.player.mana
                + MANA_RECOVERY_PER_SECOND
                * dt,
            )

        self._update_player_animation(dt)
        self.prev_camera_x = self.camera_x
        self.prev_camera_y = self.camera_y
        self._update_camera(dt)

        self._refresh_aim_screen_position()

        self.input.end_frame()

    def _update_health_regen(self, dt, hp_before_damage):
        """Slow natural HP recovery after a quiet period.

        Any real HP loss in this simulation step restarts the delay. Poison gas
        suppresses regeneration until the poison dose clears, so natural regen
        cannot cancel the intended toxic element mechanic. Creature bites are
        direct hit events and therefore only reset this timer once per hit.
        """
        p = self.player
        current = max(0.0, min(float(MAX_HP), float(p.hp)))
        if current < float(hp_before_damage) - 1e-6:
            p.health_regen_delay_remaining = float(PLAYER_HP_REGEN_DELAY_SECONDS)
            return

        if bool(getattr(p, "poisoned", False)):
            p.health_regen_delay_remaining = max(
                float(getattr(p, "health_regen_delay_remaining", 0.0)), 0.5
            )
            return

        remaining = max(0.0, float(getattr(p, "health_regen_delay_remaining", 0.0)))
        if remaining > 0.0:
            p.health_regen_delay_remaining = max(0.0, remaining - float(dt))
            return

        if current < float(MAX_HP):
            p.hp = min(
                float(MAX_HP),
                current + float(PLAYER_HP_REGEN_PER_SECOND) * max(0.0, float(dt)),
            )

    def _configure_player_animation_events(self):
        """Bind authored animation markers to gameplay timing.

        ``jump.events.takeoff`` is stored as a frame index by the asset editor.
        Runtime converts that frame to seconds using the authored FPS. Older
        two-or-more-frame jump assets without an explicit marker safely use the
        second frame, while static/one-frame assets keep instant legacy jumping.
        """
        delay=0.0; source="instant"; frame=0; fps=0.0
        try:
            info=self.assets.animation_info("player.default","jump")
            if info:
                count=max(1,int(info.get("frame_count",1) or 1))
                fps=max(1.0,float(info.get("fps",6.0) or 6.0))
                events=info.get("events",{}) if isinstance(info.get("events",{}),dict) else {}
                if "takeoff" in events:
                    frame=max(0,min(count-1,int(events["takeoff"]))); source="authored"
                elif count>=2:
                    frame=1; source="fallback-frame-2"
                else:
                    frame=0; source="single-frame"
                delay=float(frame)/fps
        except Exception:
            delay=0.0; source="instant"; frame=0; fps=0.0
        self.player.configure_jump_takeoff_delay(delay)
        self.player_jump_event_debug={
            "takeoff_frame":int(frame),
            "takeoff_delay":float(delay),
            "fps":float(fps),
            "source":str(source),
        }

    @staticmethod
    def _player_visual_group(state):
        """Return the body-posture family used for animation linking."""
        state=str(state or "")
        if state in ("idle","walk","run"):
            return "stand"
        if state in ("crouch","crouch_walk","crouch_run"):
            return "crouch"
        if state in ("prone","crawl"):
            return "prone"
        if state in ("jump","fall"):
            return "air"
        return state

    @staticmethod
    def _player_locomotion_state(state):
        return str(state or "") in ("walk","run","crouch_walk","crouch_run","crawl")

    @staticmethod
    def _animation_sync_frame(previous_state, next_state, previous_info, next_info, previous_frame):
        """Choose the authored entry frame when changing visual clips.

        FIX17 keyframe-link policy:
        * A normal posture entry still starts at frame 0 so its authored intro
          plays (stand -> crouch, crouch -> prone, etc.).
        * Switching clips inside the SAME posture family enters at the target's
          ``link`` marker (or explicit loop_start).
        * PRONE -> CROUCH also uses the crouch link marker because a crouch clip
          commonly contains a stand->crouch intro that must not replay here.
        * Returning from ROLL uses the destination link marker.
        * FIX18 landing from AIR into CROUCH/PRONE uses the destination link
          marker, so a stand->crouch intro is not replayed after a crouch jump.
        * Locomotion clips with link markers on BOTH sides preserve normalized
          cycle phase relative to those anchors, so walk<->run changes do not
          arbitrarily snap both feet back to frame 0.
        """
        if not isinstance(next_info,dict):
            return 0
        count=max(1,int(next_info.get("frame_count",1) or 1))
        dst_link=next_info.get("link_frame")
        dst_loop_start=max(0,min(count-1,int(next_info.get("loop_start",0) or 0)))
        if dst_link is not None:
            try:dst_link=max(0,min(count-1,int(dst_link)))
            except Exception:dst_link=None

        prev=str(previous_state or ""); nxt=str(next_state or "")
        prev_group=GameApp._player_visual_group(prev)
        next_group=GameApp._player_visual_group(nxt)
        should_link=(prev_group==next_group and bool(prev)) or (prev_group=="prone" and next_group=="crouch") or (prev=="roll") or (prev_group=="air" and next_group in ("crouch","prone"))
        if not should_link:
            return 0

        # Phase-synchronised locomotion switch. The marked link frame acts like
        # a shared foot-contact/key-pose anchor in both clips.
        if (
            GameApp._player_locomotion_state(prev)
            and GameApp._player_locomotion_state(nxt)
            and isinstance(previous_info,dict)
            and previous_info.get("link_frame") is not None
            and dst_link is not None
        ):
            try:
                sc=max(1,int(previous_info.get("frame_count",1) or 1))
                sl=max(0,min(sc-1,int(previous_info.get("loop_start",0) or 0)))
                se=max(sl,min(sc-1,int(previous_info.get("loop_end",sc-1))))
                sa=max(sl,min(se,int(previous_info.get("link_frame"))))
                dc=count
                dl=dst_loop_start
                de=max(dl,min(dc-1,int(next_info.get("loop_end",dc-1))))
                da=max(dl,min(de,int(dst_link)))
                sspan=max(1,se-sl+1); dspan=max(1,de-dl+1)
                sf=max(sl,min(se,int(previous_frame)))
                phase=((sf-sa)%sspan)/float(sspan)
                offset=int(round(phase*dspan))%dspan
                return dl+((da-dl+offset)%dspan)
            except Exception:
                pass

        if dst_link is not None:
            return int(dst_link)
        # Explicit loop_start without a link marker is still a valid stable
        # entry point for same-posture transitions.
        return int(dst_loop_start)

    def _update_player_animation(self, dt):
        p = self.player

        if p.state == "climb":
            state = "climb"
        elif p.state == "roll":
            state = "roll"
        elif bool(getattr(p,"jump_preparing",False)):
            state = "jump"
        elif not p.grounded:
            state = "fall" if float(getattr(p, "vy", 0.0)) > 0.0 else "jump"
        elif p.state == "crouch":
            if abs(p.vx) > 1:
                state = "crouch_run" if p.run_mode else "crouch_walk"
            else:
                state = "crouch"
        elif p.state == "prone":
            state = "crawl" if abs(p.vx) > 1 else "prone"
        elif abs(p.vx) > 1:
            state = "run" if p.run_mode else "walk"
        else:
            state = "idle"

        animation="player_"+state
        current_id=str(getattr(self.player_animation,"animation_id","") or "")
        previous_state=current_id[7:] if current_id.startswith("player_") else current_id
        changed=(current_id!=animation)

        # External pixel animation metadata is authoritative when available.
        # The logical SpriteRegistry remains a safe built-in fallback.
        try:next_info=self.assets.animation_info("player.default",state)
        except Exception:next_info=None
        try:previous_info=self.assets.animation_info("player.default",previous_state) if previous_state else None
        except Exception:previous_info=None

        if next_info:
            start_frame=0
            if changed:
                start_frame=self._animation_sync_frame(
                    previous_state,state,previous_info,next_info,
                    int(getattr(self.player_animation,"frame_index",0) or 0),
                )
            self.player_animation.play(
                animation,
                start_frame=start_frame,
                fps=float(next_info.get("fps",6.0) or 6.0),
                frame_count=max(1,int(next_info.get("frame_count",1) or 1)),
                loop=bool(next_info.get("loop",state not in ("jump","roll"))),
                loop_start=int(next_info.get("loop_start",0) or 0),
                loop_end=int(next_info.get("loop_end",max(0,int(next_info.get("frame_count",1) or 1)-1))),
            )
        else:
            self.player_animation.play(animation)

        self.player_animation.update(dt)

    def _front_tile(self):
        p = self.player
        tx = int((p.x + p.facing * TILE_SIZE * 1.2) // TILE_SIZE)
        ty = max(0, int(p.y // TILE_SIZE) - 1)
        return tx, ty

    def debug_add_water_ahead(self):
        tx, ty = self._front_tile()
        cx = tx // 16
        cy = ty // 16
        amount = self.environment.state.take_vapor(cx, cy, 1.0)
        if amount <= 0.001:
            self.events.emit("message", text="目前區域沒有足夠水氣可凝結")
            return
        self.environment.water.deposit(tx, ty, amount)
        self.events.emit(
            "message",
            text=f"測試凝結水 {amount:.2f} → ({tx},{ty})",
        )

    def debug_ignite_ahead(self):
        tx, air_ty = self._front_tile()
        for cx, cy in ((tx, air_ty), (tx, air_ty + 1), (tx, air_ty + 2)):
            if self.environment.fire.ignite(cx, cy):
                self.events.emit("message", text=f"測試點火 ({cx},{cy})")
                return
        self.events.emit("message", text="前方沒有可燃物")

    def ignite_tile(self, tx, ty):
        return self.environment.fire.ignite(tx, ty)

    def add_water(self, tx, ty, amount=1.0):
        cx = int(tx) // 16
        cy = int(ty) // 16
        available = self.environment.state.take_vapor(cx, cy, amount)
        if available > 0.0:
            self.environment.water.deposit(tx, ty, available)
        return available

    # --------------------------------------------------------
    # FIX39 treasure chests / persistent weapon equipment
    # --------------------------------------------------------
    def _map_key(self):
        path = str(
            getattr(self, "current_map_path", "")
            or getattr(getattr(self, "map_runtime", None), "source_path", "")
            or getattr(getattr(self, "map_loader", None), "path", "")
            or "editor_map"
        )
        return os.path.splitext(os.path.basename(path))[0] or "editor_map"

    def _find_chest_floor(self, tx, preferred_row=None):
        tx = max(1, min(int(self.world.width_tiles) - 2, int(tx)))
        h = int(self.world.height_tiles)
        if h < 4:
            return None
        rows = []
        if preferred_row is not None:
            base = max(2, min(h - 1, int(preferred_row)))
            for radius in range(0, min(18, h)):
                if radius == 0:
                    rows.append(base)
                else:
                    rows.extend((base - radius, base + radius))
        else:
            try:
                row = self.world.fauna_surface_row(tx)
            except Exception:
                row = None
            if row is not None:
                rows.append(int(row))
            rows.extend(range(2, h))
        seen = set()
        for fy in rows:
            fy = int(fy)
            if fy in seen or not (2 <= fy < h):
                continue
            seen.add(fy)
            try:
                if not bool(self.world.is_solid(tx, fy)):
                    continue
            except Exception:
                try:
                    from world.tile_registry import tile_def
                    if not bool(tile_def(self.world.get_tile(tx, fy)).solid):
                        continue
                except Exception:
                    continue
            clear = True
            for ay in (fy - 1, fy - 2):
                try:
                    from world.tile_registry import tile_def
                    if bool(tile_def(self.world.get_tile(tx, ay)).solid):
                        clear = False
                        break
                except Exception:
                    clear = False
                    break
            if clear:
                return fy
        return None

    def _recovery_chest_candidate(self, rng, avoid_player=True):
        """Find one random valid floor without spawning inside another chest."""
        width=max(3,int(self.world.width_tiles));height=max(4,int(self.world.height_tiles))
        occupied=[int(float(chest.x)//TILE_SIZE) for chest in tuple(getattr(self,"chests",()) or ())]
        player_tx=int(float(self.player.x)//TILE_SIZE)
        attempts=max(96,min(512,width*2))
        for _ in range(attempts):
            tx=rng.randint(1,max(1,width-2))
            if any(abs(tx-other)<4 for other in occupied):
                continue
            if avoid_player and abs(tx-player_tx)<8:
                continue
            floor=self._find_chest_floor(tx,None)
            if floor is not None:
                return int(tx),int(floor)
        # Deterministic exhaustive fallback guarantees a position on ordinary
        # maps even when the random samples all hit water/solid ceilings.
        for distance_rule in (True,False):
            for offset in range(width):
                tx=1+((player_tx+13+offset*37) % max(1,width-2))
                if any(abs(tx-other)<3 for other in occupied):
                    continue
                if distance_rule and abs(tx-player_tx)<6:
                    continue
                floor=self._find_chest_floor(tx,None)
                if floor is not None:
                    return int(tx),int(floor)
        # Absolute last resort for an empty or malformed custom map: the user
        # requirement is that a disappeared weapon always returns in a chest.
        # Keep the chest inside map bounds and near, but not directly on, the
        # player.  Ordinary authored maps never use this floating fallback.
        try:
            player_row=int((float(self.player.y)+float(getattr(self.player,"h",0.0))*.5)//TILE_SIZE)+1
        except Exception:
            player_row=height//2
        floor=max(2,min(height-1,player_row))
        fallback_tx=max(1,min(width-2,player_tx+8 if player_tx+8<width-1 else player_tx-8))
        for delta in (0,5,-5,10,-10,1,-1):
            tx=max(1,min(width-2,fallback_tx+delta))
            if not any(abs(tx-other)<2 for other in occupied):
                return int(tx),int(floor)
        return int(fallback_tx),int(floor)

    def _spawn_recovery_chest(self,item_id,name):
        """Persist and materialize the exact weapon copy destroyed by durability."""
        item_id=str(item_id or "");name=str(name or "武器")
        if not item_id.startswith("weapon_"):
            return None
        self._recovery_chest_serial=max(0,int(getattr(self,"_recovery_chest_serial",0) or 0))+1
        serial=int(self._recovery_chest_serial)
        entropy=(time.time_ns() ^ (serial*0x9E3779B1) ^ sum(ord(c) for c in self._map_key()))
        candidate=self._recovery_chest_candidate(random.Random(entropy))
        if candidate is None:
            return None
        tx,floor=candidate
        map_key=self._map_key()
        chest_id="recovery_chest:%d:%s:%d:%d:%s"%(serial,map_key,tx,floor,item_id)
        record={
            "entity_id":chest_id,"map_key":map_key,"tx":tx,"floor":floor,
            "item_id":item_id,"name":name,"count":1,
        }
        self.recovery_chest_records.append(record)
        chest=Chest(
            entity_id=chest_id,x=(tx+.5)*TILE_SIZE,y=float(floor)*TILE_SIZE,
            loot_item_id=item_id,loot_name=name,count=1,
            opened=(chest_id in self.opened_chest_ids),
        )
        self.scene.add_entity(chest);self.chests.append(chest)
        return chest

    def _export_recovery_chests(self):
        records=[]
        for raw in tuple(getattr(self,"recovery_chest_records",()) or ()):
            if not isinstance(raw,dict):
                continue
            item_id=str(raw.get("item_id","") or "")[:128]
            entity_id=str(raw.get("entity_id","") or "")[:192]
            map_key=str(raw.get("map_key","") or "")[:96]
            if not item_id.startswith("weapon_") or not entity_id or not map_key:
                continue
            try:tx=int(raw.get("tx",0));floor=int(raw.get("floor",0));count=max(1,min(9,int(raw.get("count",1) or 1)))
            except Exception:continue
            records.append({
                "entity_id":entity_id,"map_key":map_key,"tx":tx,"floor":floor,
                "item_id":item_id,"name":str(raw.get("name","武器") or "武器")[:128],
                "count":count,
            })
        return {"serial":max(0,int(getattr(self,"_recovery_chest_serial",0) or 0)),"records":records}

    def _import_recovery_chests(self,payload):
        payload=payload if isinstance(payload,dict) else {}
        source=payload.get("records",())
        records=[];seen=set();highest=0
        if isinstance(source,(list,tuple)):
            for raw in source:
                if not isinstance(raw,dict):continue
                entity_id=str(raw.get("entity_id","") or "")[:192]
                map_key=str(raw.get("map_key","") or "")[:96]
                item_id=str(raw.get("item_id","") or "")[:128]
                if not entity_id or entity_id in seen or not map_key or not item_id.startswith("weapon_"):
                    continue
                try:
                    tx=int(raw.get("tx",0));floor=int(raw.get("floor",0));count=max(1,min(9,int(raw.get("count",1) or 1)))
                except Exception:
                    continue
                if not (0<=tx<int(self.world.width_tiles) or map_key!=self._map_key()):
                    continue
                records.append({
                    "entity_id":entity_id,"map_key":map_key,"tx":tx,"floor":floor,
                    "item_id":item_id,"name":str(raw.get("name","武器") or "武器")[:128],
                    "count":count,
                });seen.add(entity_id)
                try:
                    if entity_id.startswith("recovery_chest:"):
                        highest=max(highest,int(entity_id.split(":",2)[1]))
                except Exception:pass
        try:saved_serial=max(0,int(payload.get("serial",0) or 0))
        except Exception:saved_serial=0
        self.recovery_chest_records=records
        self._recovery_chest_serial=max(saved_serial,highest)
        return self._sync_recovery_chests_for_current_map()

    def _sync_recovery_chests_for_current_map(self):
        map_key=self._map_key();existing={str(chest.entity_id) for chest in tuple(getattr(self,"chests",()) or ())}
        added=0
        for row in tuple(getattr(self,"recovery_chest_records",()) or ()):
            if str(row.get("map_key","") or "")!=map_key:continue
            chest_id=str(row.get("entity_id","") or "")
            if not chest_id or chest_id in existing:continue
            try:tx=int(row.get("tx",0));floor=int(row.get("floor",0))
            except Exception:continue
            if not (0<=tx<int(self.world.width_tiles) and 1<=floor<int(self.world.height_tiles)):
                continue
            chest=Chest(
                entity_id=chest_id,x=(tx+.5)*TILE_SIZE,y=float(floor)*TILE_SIZE,
                loot_item_id=str(row.get("item_id","weapon_sword") or "weapon_sword"),
                loot_name=str(row.get("name","武器") or "武器"),
                count=max(1,int(row.get("count",1) or 1)),
                opened=(chest_id in self.opened_chest_ids),
            )
            self.scene.add_entity(chest);self.chests.append(chest);existing.add(chest_id);added+=1
        return added

    def _build_world_chests(self):
        rows=[];map_key=self._map_key();meta={}
        try:meta=dict((self.map_loader.payload or {}).get("metadata",{}) or {}) if self.map_loader else {}
        except Exception:meta={}
        underground_only=bool(meta.get("underground_only",False))
        candidates=[]
        if not underground_only:
            for index,span in enumerate(tuple(getattr(self.biomes,"spans",()) or ())):
                try:start,end,biome=int(span[0]),int(span[1]),str(span[2])
                except Exception:continue
                if end-start<8:continue
                tx=start+max(4,min(end-start-4,(end-start)//2))
                candidates.append(("biome_%s_%02d"%(biome,index),tx,None))
        for index,row in enumerate(meta.get("underground_dungeons",()) or ()):
            try:
                bounds=row.get("bounds") if isinstance(row,dict) else row
                x0,y0,x1,y1=[int(v) for v in list(bounds)[:4]]
                candidates.append(("dungeon_%02d"%index,(x0+x1)//2,y1 if x1>x0 else y0+max(2,y1)))
            except Exception:continue
        for index,row in enumerate(meta.get("underground_caverns",()) or ()):
            try:
                x,y,w,h=[int(v) for v in list(row)[:4]]
                candidates.append(("cavern_%02d"%index,x+w//2,y+h+1))
            except Exception:continue
        if underground_only and not candidates:
            candidates=[("underground_mid",int(self.world.width_tiles)//2,int(self.world.height_tiles)//2)]

        # Reconstruct the legacy capped set first, then add ceil(50%) more.
        # This makes the requested increase measurable rather than merely
        # changing the random density constant.
        positions=[];used=[]
        for label,tx,preferred in candidates:
            if len(positions)>=10:break
            if any(abs(int(tx)-prev)<5 for prev in used):continue
            floor=self._find_chest_floor(tx,preferred)
            if floor is None:continue
            positions.append((str(label),int(tx),int(floor)));used.append(int(tx))
        legacy_count=len(positions)
        if legacy_count==0:
            # Keep tiny/custom maps functional: establish one valid legacy
            # equivalent before applying the 50% multiplier.
            width=max(3,int(self.world.width_tiles));seed=sum(ord(c) for c in map_key)
            for attempt in range(min(width*2,512)):
                tx=1+((seed+attempt*37+attempt*attempt*11)%max(1,width-2))
                floor=self._find_chest_floor(tx,None)
                if floor is not None:
                    positions.append(("fallback_00",int(tx),int(floor)));used.append(int(tx));break
            legacy_count=len(positions)
        target=min(15,max(legacy_count,int(math.ceil(legacy_count*1.5))))
        width=max(3,int(self.world.width_tiles));seed=sum((i+1)*ord(c) for i,c in enumerate(map_key))
        attempt=0
        while len(positions)<target and attempt<max(256,width*3):
            tx=1+((seed+attempt*37+attempt*attempt*11)%max(1,width-2));attempt+=1
            if any(abs(int(tx)-prev)<5 for prev in used):continue
            floor=self._find_chest_floor(tx,None)
            if floor is None:continue
            positions.append(("bonus_%02d"%(len(positions)-legacy_count),int(tx),int(floor)));used.append(int(tx))
        self._static_chest_legacy_count=legacy_count
        self._static_chest_target_count=target
        self._static_chest_actual_count=len(positions)
        for serial,(label,tx,floor) in enumerate(positions):
            wid=chest_weapon_for_index(serial,seed=seed)
            item=weapon_item(wid)
            if item is None:continue
            item_id,name=item
            chest_id="chest:%s:%s:%d:%d"%(map_key,label,tx,floor)
            chest=Chest(
                entity_id=chest_id,x=(tx+.5)*TILE_SIZE,y=float(floor)*TILE_SIZE,
                loot_item_id=item_id,loot_name=name,count=1,
                opened=(chest_id in self.opened_chest_ids),
            )
            self.scene.add_entity(chest);rows.append(chest)
        return rows

    def _apply_opened_chest_state(self):
        opened = set(getattr(self, "opened_chest_ids", set()) or set())
        for chest in tuple(getattr(self, "chests", ()) or ()):
            chest.opened = str(chest.entity_id) in opened

    def _open_chest(self, chest):
        if chest is None or bool(getattr(chest, "opened", False)):
            return False
        from systems.editor_objects import add_loot_atomically
        _entries = getattr(chest,"editor_loot",None) or [
            {"id":str(chest.loot_item_id),"name":str(chest.loot_name),"count":int(chest.count)}
        ]
        if not add_loot_atomically(self.inventory,_entries):
            self.events.emit("message", text="背包已滿，寶箱保留未開啟")
            return False
        chest.opened = True
        self.opened_chest_ids.add(str(chest.entity_id))
        self.events.emit("audio_chest_open")
        self.events.emit("message", text="寶箱取得："+"、".join(str(r["name"])+"×"+str(r["count"]) for r in _entries[:5])+("…" if len(_entries)>5 else "")+"（已放入背包）")
        return True

    def equip_selected_inventory_item(self):
        self._cancel_aim_release()
        snap = self.inventory.snapshot()
        index = int(snap.get("selected_index", 0))
        slots = snap.get("slots", ())
        if not (0 <= index < len(slots)) or slots[index] is None:
            self.events.emit("message", text="請先選擇武器或裝備")
            return False
        stack = slots[index]
        item_id=str(stack.get("item_id", "") or "")
        if item_id == "tool_grapple":
            try:
                self.tools.grapple.detach("inventory_select", retract=False)
            except Exception:
                pass
            self.tools.selected_tool = self.tools.TOOL_GRAPPLE
            self.action_mode = self.ACTION_TOOL
            self.tools.set_action_mode_active(True)
            self.events.emit("message", text="已選用工具：鉤爪")
            return True
        wid = weapon_from_item(item_id)
        if wid:
            if not self.melee.equip_weapon(wid):
                return False
            self.drone.sync_equipment(force=True)
            if hasattr(self, "boss_weapons"):
                self.boss_weapons.sync_equipment()
            # Important: the stack is NOT removed. Swapping equipment therefore
            # never destroys the previous weapon and all weapons remain selectable.
            self.events.emit("audio_weapon_equip")
            self.events.emit("weapon_equipped", weapon=str(self.melee.selected_weapon), icon="")
            self.events.emit("message", text=f"已裝備：{self.melee.selected_name}")
            return True
        if is_equipment_item(item_id):
            if not self.equipment.equip(item_id):
                return False
            self.equipment.sync_player(self.player)
            # Reuse the existing lightweight equip cue. No extra UIKit object
            # or main-thread update is created by wearable changes.
            self.events.emit("audio_weapon_equip")
            return True
        self.events.emit("message", text="這個物品不能裝備")
        return False

    def unequip_selected_inventory_item(self):
        """Unequip the selected wearable without removing its backpack stack.

        UIKit/Metal only enqueue ``inventory_unequip``.  Reading the selected
        stack, changing the authoritative slot and synchronizing Player all
        happen here on the simulation thread, in FIFO order with selection and
        inventory commands.
        """
        snap = self.inventory.snapshot()
        index = int(snap.get("selected_index", 0))
        slots = snap.get("slots", ())
        if not (0 <= index < len(slots)) or slots[index] is None:
            self.events.emit("message", text="請先選擇要脫下的裝備")
            return False

        item_id = str(slots[index].get("item_id", "") or "")
        if not is_equipment_item(item_id):
            self.events.emit("message", text="選取的物品不是可穿戴裝備")
            return False
        # EquipmentSystem.unequip accepts either an item ID or a slot.  Keep
        # this inventory action exact: selecting an unworn shoe must not clear
        # a different shoe currently occupying the same foot slot.
        if not self.equipment.is_equipped(item_id):
            self.events.emit("message", text="這件裝備目前沒有穿戴")
            return False
        if not self.equipment.unequip(item_id):
            return False

        # Player owns movement/shield transients.  Synchronizing immediately
        # cancels hover/air dash and clears shield feedback in the same frame;
        # the renderer then observes empty slot IDs and removes the overlay.
        self.equipment.sync_player(self.player)
        self.events.emit("equipment_unequipped", item_id=item_id)
        return True

    def equip_selected_inventory_weapon(self):
        """Backward-compatible entry point used by older tests/scripts."""
        return self.equip_selected_inventory_item()

    def ensure_weapon_inventory(self):
        try:
            snap = self.inventory.snapshot()
            if not any(row and str(row.get("item_id", "")).startswith("weapon_") for row in snap.get("slots", ())):
                self.inventory.add("weapon_sword", "鐵劍", 1, max_stack=self.inventory.WEAPON_MAX_STACK)
        except Exception:
            pass

    def ensure_starter_tool_inventory(self):
        """Migrate only pre-inventory saves to the FIX73 starter grappling hook."""
        try:
            if self.inventory.count_item("tool_grapple") <= 0:
                self.inventory.add("tool_grapple", "鉤爪", 1, max_stack=1)
        except Exception:
            pass

    def ensure_equipment_inventory(self):
        """Give FIX66-or-older saves one complete wearable test set once.

        If a save already owns any wearable, missing pieces remain intentional
        drops/removals. A legacy save owns none, so all six become immediately
        testable without rebuilding every treasure map.
        """
        try:
            snap=self.inventory.snapshot()
            owns_any=any(
                row and is_equipment_item(row.get("item_id", ""))
                for row in snap.get("slots", ())
            )
            if not owns_any:
                for item_id in EQUIPMENT_DEFS:
                    if item_id == "equipment.wind_god_wings":
                        continue  # FIX83 boss reward; never part of the legacy starter set.
                    self.equipment.acquire(item_id, 1)
            self.equipment.reconcile_inventory()
            self.equipment.sync_player(self.player)
        except Exception:
            pass

    def _pickup_world_item(self, item):
        item_count = max(0, int(getattr(item, "count", 0) or 0))
        durability = clean_drop_heavy_uses(
            getattr(item, "item_id", "item"),
            item_count,
            getattr(item, "heavy_uses", None),
        )
        item.heavy_uses = durability
        accepted = self.inventory.add(
            getattr(item, "item_id", "item"),
            getattr(item, "name", "物品"),
            item_count,
            getattr(item, "max_stack", 99),
            heavy_uses=durability,
        )
        # InteractionSystem decreases ``item.count`` after this callback.  Move
        # the exact same prefix now so a partial pickup leaves the uncollected
        # copies (and their original order) on the ground.
        if durability is not None and int(accepted) > 0:
            item.heavy_uses = list(durability[int(accepted):])
        # InteractionSystem marks a fully collected item inactive after this
        # callback returns.  Retire dynamic drops now as well so the resident
        # Scene list cannot accumulate one dead Item per pickup.  Authored
        # static/prototype items are intentionally left under their old rules.
        if (
            int(accepted) >= max(0, int(getattr(item, "count", 0) or 0))
            and getattr(self, "item_drop_system", None) is not None
        ):
            self.item_drop_system.retire(item, picked=True)
        return int(accepted)

    def _new_dynamic_drop_item(
        self, item_id, name, count, x, y, preferred_entity_id=None,
        heavy_uses=None,
    ):
        """Allocate one monotonic, collision-free ``drop_*`` identity."""
        wanted = str(preferred_entity_id or "")[:64]
        suffix = wanted[5:] if wanted.startswith("drop_") else ""
        wanted_serial = int(suffix) if suffix.isdigit() else -1
        if (
            0 < wanted_serial <= 1000000000
            and not self.item_drop_system.contains_entity_id(wanted)
        ):
            serial = wanted_serial
            self._drop_serial = max(int(self._drop_serial), serial)
            entity_id = wanted
        else:
            # At most 256 live IDs exist, so this loop is tightly bounded even
            # after a hand-edited save contains a duplicate serial.
            current = max(0, min(1000000000, int(self._drop_serial)))
            serial = current + 1 if current < 1000000000 else 1
            while True:
                entity_id = "drop_%06d" % serial
                if not self.item_drop_system.contains_entity_id(entity_id):
                    break
                serial = serial + 1 if serial < 1000000000 else 1
            self._drop_serial = max(current, int(serial))
        item = Item(
            entity_id=entity_id,
            x=float(x), y=float(y), item_id=str(item_id), name=str(name),
            count=max(1, int(count)), max_stack=99,
            heavy_uses=clean_drop_heavy_uses(item_id, count, heavy_uses),
            picked=False,
        )
        item.drop_created_serial = int(serial)
        return item

    def spawn_world_item_result(
        self, item_id, name, count, x, y, *, preferred_entity_id=None,
        allow_merge=True, announce_rejection=True, heavy_uses=None,
    ):
        """Spawn/merge loot and return explicit accepted/rejected quantities."""
        result = self.item_drop_system.spawn(
            item_id, name, count, x, y,
            item_factory=lambda iid, label, amount, px, py: self._new_dynamic_drop_item(
                iid, label, amount, px, py,
                preferred_entity_id=preferred_entity_id,
            ),
            allow_merge=bool(allow_merge),
            heavy_uses=heavy_uses,
        )
        self.last_drop_spawn_result = result
        if int(result.rejected_count) > 0 and bool(announce_rejection):
            self.events.emit(
                "message",
                text=(
                    "地面掉落物已達安全上限；未放置 %s ×%d"
                    % (str(name or item_id or "物品"), int(result.rejected_count))
                ),
            )
            self.events.emit(
                "world_drop_rejected",
                item_id=str(item_id or "item"),
                count=int(result.rejected_count),
                reason=str(result.reason),
            )
        return result

    def spawn_world_item(
        self, item_id, name, count, x, y, *, return_result=False,
        preferred_entity_id=None, allow_merge=True,
        announce_rejection=True, heavy_uses=None,
    ):
        """Compatibility wrapper; ``return_result=True`` exposes accounting.

        Existing gameplay/validator callers receive the created or merged
        :class:`Item` as before. New producers should use
        :meth:`spawn_world_item_result` (or ``return_result=True``) so a full
        budget can never consume quantities silently.
        """
        result = self.spawn_world_item_result(
            item_id, name, count, x, y,
            preferred_entity_id=preferred_entity_id,
            allow_merge=allow_merge,
            announce_rejection=announce_rejection,
            heavy_uses=heavy_uses,
        )
        return result if bool(return_result) else result.item

    def drop_selected_inventory(self):
        # Preflight from a locked snapshot. The world budget is resolved before
        # mutating the backpack, so a total rejection leaves slots, selection
        # and selected quantity byte-for-byte unchanged.
        snapshot = self.inventory.snapshot()
        selected_index = int(snapshot.get("selected_index", 0) or 0)
        slots = list(snapshot.get("slots", ()) or ())
        selected = slots[selected_index] if 0 <= selected_index < len(slots) else None
        if not isinstance(selected, dict):
            return False
        requested = max(1, min(
            int(selected.get("count", 0) or 0),
            int(snapshot.get("selected_quantity", 1) or 1),
        ))
        item_id = str(selected.get("item_id", "item") or "item")
        name = str(selected.get("name", "物品") or "物品")
        selected_heavy_uses = selected.get("heavy_uses")
        drop_heavy_uses = (
            list(selected_heavy_uses[:requested])
            if isinstance(selected_heavy_uses, (list, tuple))
            else None
        )
        p = self.player
        result = self.spawn_world_item_result(
            item_id, name, requested,
            p.x + (1 if p.facing >= 0 else -1) * TILE_SIZE * 0.55,
            p.y - 5.0,
            heavy_uses=drop_heavy_uses,
            announce_rejection=False,
        )
        accepted = max(0, int(result.accepted_count))
        if accepted <= 0:
            self.events.emit("message", text="地面掉落物已滿，物品已退回背包")
            return False
        removed = self.inventory.remove_selected(quantity=accepted)
        if removed is None or int(removed.count) != accepted:
            # Simulation-thread command routing makes this unreachable; keep a
            # loud diagnostic if a future asynchronous producer violates it.
            self.events.emit("message", text="掉落取消：背包狀態已變更，請重試")
            return False
        self.events.emit(
            "message", text=f"丟棄 {name} ×{accepted}"
        )
        return True

    def world_drop_items(self):
        # FIX69 compatibility: public callers also see authored/static Items.
        # Runtime budgets and save paths use dynamic_world_drop_items() or the
        # ItemDropSystem index directly, so those objects never consume cap.
        return [
            entity for entity in self.scene.entities
            if isinstance(entity, Item) and entity.active and not entity.picked
        ]

    def dynamic_world_drop_items(self):
        return list(self.item_drop_system.dynamic_items())

    def environment_stats(self):
        data = self.environment.stats()
        data["fireballs"] = len(self.magic.fireballs)
        data["waterballs"] = len(self.magic.waterballs)
        data["iceballs"] = len(self.magic.iceballs)
        data["electricballs"] = len(self.magic.electricballs)
        return data

    def toggle_magic(self, steps=1):
        self._cancel_aim_release()
        return self.magic.toggle_magic(steps=steps)

    def save_game(self):
        if bool(getattr(self, "_new_game_in_progress", False)):
            self.events.emit("message", text="新遊戲正在建立，暫時無法存檔")
            return False
        try:
            path = self.save_manager.save(self)

            self.events.emit(
                "message",
                text="已存檔：save_01（永久）",
            )
            print("Saved:", path)

            return True

        except Exception as exc:
            self.events.emit(
                "message",
                text="存檔失敗：" + str(exc),
            )
            print(
                "Save error:",
                repr(exc),
            )
            return False

    def load_game(self):
        """Load the saved map first, then restore its spatial state.

        FIX60 closes the secondary-map bug where the overworld coordinate was
        applied to a pyramid/mine/castle world.  A missing or invalid map
        reference now starts the current map from its safe authored spawn
        instead of leaving the player outside the playable view.
        """
        if bool(getattr(self, "_new_game_in_progress", False)):
            self.events.emit("message", text="新遊戲正在建立，暫時無法讀檔")
            return False
        # Every explicit Load begins a new discovery pass, including missing
        # or invalid-save fallbacks that do not reach _finalize_manual_load().
        self._clear_portal_prompt_latch()
        try:
            if not self.save_manager.exists():
                self._restart_current_map_safely()
                self.events.emit(
                    "message",
                    text="沒有可用存檔，已從安全起點重新開始",
                )
                return True

            saved_map=self.save_manager.saved_map_path()
            if not saved_map:
                self._restart_current_map_safely()
                self.events.emit(
                    "message",
                    text="存檔地圖已失效，已從安全起點重新開始",
                )
                return True

            current=str(
                getattr(self,"current_map_path","")
                or getattr(getattr(self,"map_runtime",None),"source_path","")
                or getattr(getattr(self,"map_loader",None),"path","")
                or ""
            )
            switched=not self._same_authored_map(current,saved_map)
            if switched and not self._adopt_clean_map(saved_map):
                self._restart_current_map_safely()
                self.events.emit(
                    "message",
                    text="無法開啟存檔地圖，已從安全起點重新開始",
                )
                return True

            ok, info = self.save_manager.load(self)

            if ok:
                self._reset_transients_after_manual_load()
                self._finalize_manual_load()
                self.events.emit(
                    "message",
                    text=("讀檔完成：已返回上次地圖" if switched else "讀檔完成：save_01"),
                )
                print("Loaded:", info)
                return True

            self._restart_current_map_safely()
            self.events.emit("message",text=str(info)+"；已從安全起點重新開始")
            return True

        except Exception as exc:
            try:self._restart_current_map_safely()
            except Exception as restart_exc:print("Safe restart error:",repr(restart_exc))
            self.events.emit(
                "message",
                text="讀檔失敗，已安全重新開始：" + str(exc),
            )
            print(
                "Load error:",
                repr(exc),
            )
            return False

    def _update_camera(self, dt):
        world_w = self.world.width_tiles * TILE_SIZE
        world_h = self.world.height_tiles * TILE_SIZE

        # Horizontal camera keeps the player near screen center.
        target_x = self.player.x - self.viewport_w * 0.50
        target_x = clamp(
            target_x,
            0,
            max(0.0, world_w - self.viewport_w),
        )

        # ----------------------------------------------------
        # V0.4.4 vertical dead-zone camera
        # ----------------------------------------------------
        # V0.4.3 used a fixed GROUND_ROW camera height, so collision/physics
        # could move the player down into a hole while the screen stayed at
        # the old height.  Now the player is allowed to move inside a vertical
        # dead-zone.  Once they fall below the lower line, the camera follows.
        # A normal jump usually remains inside the zone, avoiding camera bob.
        upper_line = self.viewport_h * CAMERA_VERTICAL_UPPER
        lower_line = self.viewport_h * CAMERA_VERTICAL_LOWER
        player_screen_y = self.player.y - self.camera_y

        target_y = self.camera_y

        if player_screen_y > lower_line:
            target_y = self.player.y - lower_line
        elif player_screen_y < upper_line:
            target_y = self.player.y - upper_line

        target_y = clamp(
            target_y,
            0,
            max(0.0, world_h - self.viewport_h),
        )

        factor_x = min(1.0, dt * 8.0)
        factor_y = min(
            1.0,
            dt * CAMERA_VERTICAL_FOLLOW_SPEED,
        )

        self.camera_x += (
            target_x - self.camera_x
        ) * factor_x

        self.camera_y += (
            target_y - self.camera_y
        ) * factor_y

    def set_tile(self, tx, ty, tile_id):
        """Public world-edit API; edits automatically become save deltas."""
        self.world.set_tile(
            tx,
            ty,
            tile_id,
            track_change=True,
        )

    def respawn_if_needed(self):
        world_h = self.world.height_tiles * TILE_SIZE

        if self.player.y > world_h + 100:
            self.player.x = float(self.respawn_x)
            self.player.y = float(self.respawn_y)
            self.player.prev_x = float(self.respawn_x)
            self.player.prev_y = float(self.respawn_y)
            self.player.vx = 0
            self.player.vy = 0
            try:self.tools.reset_transients()
            except Exception:pass
            try:self.equipment.reset_air_mobility(self.player,reason="respawn",rearm=True)
            except Exception:pass
