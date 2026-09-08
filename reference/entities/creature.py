# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from engine.entity import Entity


@dataclass
class Creature(Entity):
    species: str = "slime"
    name: str = "生物"
    hp: float = 60.0
    max_hp: float = 60.0
    attack_damage: float = 6.0
    speed: float = 42.0
    hostile: bool = True
    facing: int = 1
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = True
    # FIX20: physics/contact box only. Custom pixel art is NOT stretched to
    # these values; its visible size comes from authored cells × 2.5 world px.
    width_px: float = 30.0
    height_px: float = 26.0
    home_x: float = 0.0
    patrol_radius: float = 120.0
    behavior_state: str = "idle"
    attack_cooldown: float = 0.0
    hurt_timer: float = 0.0
    death_processed: bool = False
    poison_immune: bool = False
    poison_dose: float = 0.0
    poisoned: bool = False
    temperature_c: float = 37.0
    slow_timer: float = 0.0
    stun_timer: float = 0.0
    attack_anim_timer: float = 0.0
    attack_recoil_timer: float = 0.0
    attack_rearm_required: bool = False
    biome: str = "plains"
    loot_table: tuple = ()
    asset_id: str = ""
    locomotion: str = "ground"   # ground / fish / swim / bird / waterbird / insect_low
    home_y: float = 0.0
    motion_time: float = 0.0
    # FIX26 habitat / locomotion state. These are runtime-only defaults so old
    # saves remain compatible; they are reconstructed automatically after load.
    out_of_water_time: float = 0.0
    habitat_damage_clock: float = 0.0
    flight_mode: str = ""          # perched / takeoff / flight / landing
    flight_timer: float = 0.0
    flight_target_x: float = 0.0
    flight_target_y: float = 0.0
    rest_timer: float = 0.0
    # FIX11: renderer-only clock. Unlike motion_time (which some locomotion
    # code uses for swimming/bobbing), this advances for every active creature.
    visual_animation_time: float = 0.0
    habitat_margin_px: float = 0.0
    # FIX19 data-driven AI action bridge. These runtime fields are transient;
    # definitions live in assets/creature_actions.json and animation art/events
    # remain in assets/pixel_sources/*.json.
    ai_action_id: str = ""
    ai_action_elapsed: float = 0.0
    ai_action_duration: float = 0.0
    ai_action_effect_time: float = 0.0
    ai_action_hit_done: bool = False
    ai_action_target_x: float = 0.0
    ai_action_target_y: float = 0.0
    ai_action_cooldowns: dict = field(default_factory=dict)
    animation_state_override: str = ""
    # FIX131 combat affinity: every creature uses water/fire/electric/earth/ice.
    element_type: str = "earth"
    boss: bool = False
    boss_title: str = ""
    # FIX65: awareness belongs to each creature.  Prone stealth only applies
    # while this is False; it is never cleared merely by going prone.
    player_detected: bool = False
    # FIX76 passive fauna do not initiate combat.  A direct player-owned hit
    # permanently provokes this individual until it dies/respawns, allowing
    # it to use the same authored chase/contact/projectile actions as hostile
    # creatures without changing the species' original ``hostile`` metadata.
    provoked_by_player: bool = False
    # FIX130 per-placement MapEditor AI overrides.  Empty/default values keep
    # the species' existing behaviour, so old maps are byte-for-byte semantic
    # compatible until an author explicitly changes a placed instance.
    editor_move_mode: str = "default"
    editor_behavior_mode: str = "default"
    editor_attack_mode: str = "default"
    editor_detection_radius_px: float = 0.0
    editor_detection_vertical_px: float = 0.0
    editor_attack_cooldown_scale: float = 1.0
    editor_speed_scale: float = 1.0
    # FIX131 per-placement ecology/habitat override.
    editor_habitat_mode: str = "default"
    editor_step_height_tiles: float = 0.0
    editor_hidden_until_mined: bool = False
    editor_hidden_tile: tuple = ()
    editor_revealed: bool = True

    @property
    def background_only(self):
        return self.species == "butterfly_backdrop"

    def can_attack_player(self):
        return not self.background_only and bool(self.hostile or self.provoked_by_player)

    def provoke_by_player(self):
        if self.background_only: return False
        self.provoked_by_player = True
        self.player_detected = True
        return True

    def width(self, state=None):
        return self.width_px

    def height(self, state=None):
        return self.height_px

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else float(x)
        y = self.y if y is None else float(y)
        return (
            x - self.width_px * 0.5,
            y - self.height_px,
            x + self.width_px * 0.5,
            y,
        )

    def combat_bbox(self):
        """Visible body outline used for melee/contact damage.

        Future sprite metadata can override this independently of terrain
        collision when real animal artwork is imported.
        """
        return self.bbox()
