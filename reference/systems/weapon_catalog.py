# -*- coding: utf-8 -*-
"""FIX62 data-driven melee/ranged weapon definitions and inventory icons.

The action button is now release-triggered in weapon mode.  A short hold uses
normal attack data; holding past each weapon's ``heavy_charge_seconds`` uses
its dedicated heavy attack profile and presentation.
"""

WEAPON_DEFS = {
    "sword": {
        "item_id": "weapon_sword", "name": "鐵劍", "icon_glyph": "", "damage": 34.0,
        "reach_tiles": 1.00, "cooldown": 0.30, "attack_duration": 0.24,
        "knockback": 120.0, "max_targets": 1,
        "visual_length": 29.0, "visual_width": 4.2,
        "attack_style": "slash", "start_angle": -72.0, "end_angle": 28.0,
        "hitbox_top_scale": 0.88, "hitbox_bottom_scale": 0.05,
        "swing_sound": "sword_swing", "hit_sound": "sword_hit",
        "heavy_charge_seconds": 0.55, "heavy_damage_mult": 1.62,
        "heavy_reach_tiles": 1.38, "heavy_knockback_mult": 1.25,
        "heavy_cooldown": 0.54, "heavy_attack_duration": 0.42,
        "heavy_max_targets": 2, "heavy_style": "crescent_flash",
        "heavy_start_angle": -132.0, "heavy_end_angle": 62.0,
    },
    "dagger": {
        "item_id": "weapon_dagger", "name": "獵刀", "icon_glyph": "", "damage": 23.0,
        "reach_tiles": 0.72, "cooldown": 0.18, "attack_duration": 0.14,
        "knockback": 72.0, "max_targets": 1,
        "visual_length": 18.0, "visual_width": 3.0,
        "attack_style": "stab", "start_angle": -8.0, "end_angle": 5.0,
        "hitbox_top_scale": 0.68, "hitbox_bottom_scale": 0.25,
        "swing_sound": "dagger_swing", "hit_sound": "dagger_hit",
        "heavy_charge_seconds": 0.45, "heavy_damage_mult": 1.52,
        "heavy_reach_tiles": 1.16, "heavy_knockback_mult": 1.15,
        "heavy_cooldown": 0.46, "heavy_attack_duration": 0.40,
        "heavy_max_targets": 2, "heavy_style": "dagger_flurry",
        "heavy_start_angle": -18.0, "heavy_end_angle": 40.0,
    },
    "spear": {
        "item_id": "weapon_spear", "name": "長矛", "icon_glyph": "", "damage": 31.0,
        "reach_tiles": 1.58, "cooldown": 0.38, "attack_duration": 0.29,
        "knockback": 138.0, "max_targets": 1,
        "visual_length": 45.0, "visual_width": 3.2,
        "attack_style": "thrust", "start_angle": -5.0, "end_angle": 2.0,
        "hitbox_top_scale": 0.67, "hitbox_bottom_scale": 0.28,
        "swing_sound": "spear_swing", "hit_sound": "spear_hit",
        "heavy_charge_seconds": 0.58, "heavy_damage_mult": 1.58,
        "heavy_reach_tiles": 2.35, "heavy_knockback_mult": 1.35,
        "heavy_cooldown": 0.60, "heavy_attack_duration": 0.38,
        "heavy_max_targets": 3, "heavy_style": "lance_breaker",
        "heavy_start_angle": -10.0, "heavy_end_angle": 2.0,
    },
    "battle_axe": {
        "item_id": "weapon_battle_axe", "name": "巨斧", "icon_glyph": "", "damage": 45.0,
        "reach_tiles": 1.08, "cooldown": 0.48, "attack_duration": 0.39,
        "knockback": 176.0, "max_targets": 2,
        "visual_length": 33.0, "visual_width": 6.2,
        "attack_style": "overhead", "start_angle": -112.0, "end_angle": 54.0,
        "hitbox_top_scale": 1.04, "hitbox_bottom_scale": 0.00,
        "swing_sound": "axe_swing", "hit_sound": "axe_hit",
        "heavy_charge_seconds": 0.62, "heavy_damage_mult": 1.62,
        "heavy_reach_tiles": 1.62, "heavy_knockback_mult": 1.38,
        "heavy_cooldown": 0.76, "heavy_attack_duration": 0.48,
        "heavy_max_targets": 3, "heavy_style": "horizontal_cleave",
        "heavy_start_angle": -18.0, "heavy_end_angle": 12.0,
    },
    "war_hammer": {
        "item_id": "weapon_war_hammer", "name": "鐵鎚", "icon_glyph": "", "damage": 56.0,
        "reach_tiles": 1.00, "cooldown": 0.62, "attack_duration": 0.50,
        "knockback": 225.0, "max_targets": 1,
        "visual_length": 32.0, "visual_width": 7.2,
        "attack_style": "hammer_slam", "start_angle": -125.0, "end_angle": 62.0,
        "hitbox_top_scale": 1.08, "hitbox_bottom_scale": -0.03,
        "swing_sound": "hammer_swing", "hit_sound": "hammer_hit",
        "heavy_charge_seconds": 0.68, "heavy_damage_mult": 1.82,
        "heavy_reach_tiles": 1.20, "heavy_knockback_mult": 1.55,
        "heavy_cooldown": 0.92, "heavy_attack_duration": 0.62,
        "heavy_max_targets": 2, "heavy_style": "ground_slam",
        "heavy_start_angle": -150.0, "heavy_end_angle": 82.0,
    },
    "greatsword": {
        "item_id": "weapon_greatsword", "name": "巨劍", "icon_glyph": "", "damage": 50.0,
        "reach_tiles": 1.28, "cooldown": 0.56, "attack_duration": 0.45,
        "knockback": 195.0, "max_targets": 2,
        "visual_length": 41.0, "visual_width": 7.0,
        "attack_style": "wide_slash", "start_angle": -96.0, "end_angle": 52.0,
        "hitbox_top_scale": 1.02, "hitbox_bottom_scale": -0.02,
        "swing_sound": "greatsword_swing", "hit_sound": "greatsword_hit",
        "heavy_charge_seconds": 0.72, "heavy_damage_mult": 1.72,
        "heavy_reach_tiles": 1.55, "heavy_knockback_mult": 1.45,
        "heavy_cooldown": 0.90, "heavy_attack_duration": 0.62,
        "heavy_max_targets": 3, "heavy_style": "flying_crescent",
        "heavy_start_angle": -126.0, "heavy_end_angle": 72.0,
        "heavy_projectile_damage": 42.0, "heavy_projectile_speed": 390.0,
        "heavy_projectile_life": 0.88, "heavy_projectile_radius": 14.0,
        "heavy_projectile_spawn_offset": 30.0, "heavy_projectile_visual_scale": 1.50,
    },
    "energy_bow": {
        "item_id": "weapon_energy_bow", "name": "能量弓", "icon_glyph": "", "damage": 30.0,
        "delivery": "energy_arrow", "reach_tiles": 0.0,
        "cooldown": 0.38, "attack_duration": 0.30,
        "knockback": 92.0, "max_targets": 1,
        "visual_length": 31.0, "visual_width": 3.2,
        "attack_style": "energy_shot", "start_angle": -12.0, "end_angle": 8.0,
        "hitbox_top_scale": 0.70, "hitbox_bottom_scale": 0.20,
        "swing_sound": "magic_electric", "hit_sound": "spear_hit",
        "projectile_damage": 30.0, "projectile_speed": 520.0,
        "projectile_life": 1.35, "projectile_radius": 4.5,
        "heavy_charge_seconds": 0.64, "heavy_damage_mult": 1.0,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.05,
        "heavy_cooldown": 1.05, "heavy_attack_duration": 0.72,
        "heavy_max_targets": 12, "heavy_style": "energy_arrow_rain",
        "heavy_start_angle": -80.0, "heavy_end_angle": -62.0,
        "heavy_arrow_count": 12, "heavy_projectile_damage": 18.0,
        "heavy_projectile_speed": 230.0, "heavy_projectile_life": 1.75,
        "heavy_projectile_radius": 4.0, "heavy_projectile_gravity": 720.0,
    },
    "whip": {
        "item_id": "weapon_whip", "name": "甩鞭", "icon_glyph": "", "damage": 27.0,
        "delivery": "melee", "reach_tiles": 2.28,
        "cooldown": 0.42, "attack_duration": 0.34,
        "knockback": 104.0, "max_targets": 2,
        "visual_length": 72.0, "visual_width": 3.0,
        "attack_style": "whip_snap", "start_angle": -42.0, "end_angle": 24.0,
        "hitbox_top_scale": 0.88, "hitbox_bottom_scale": 0.02,
        "swing_sound": "dagger_swing", "hit_sound": "dagger_hit",
        "heavy_charge_seconds": 0.58, "heavy_damage_mult": 1.55,
        "heavy_reach_tiles": 2.38, "heavy_knockback_mult": 1.30,
        "heavy_cooldown": 0.82, "heavy_attack_duration": 0.64,
        "heavy_max_targets": 8, "heavy_style": "whip_spin",
        "heavy_start_angle": -160.0, "heavy_end_angle": 200.0,
    },
    "laser_gun": {
        "item_id": "weapon_laser_gun", "name": "雷射槍", "icon_glyph": "", "damage": 25.0,
        "delivery": "laser", "reach_tiles": 0.0,
        "cooldown": 0.34, "attack_duration": 0.22,
        "knockback": 72.0, "max_targets": 4,
        "visual_length": 27.0, "visual_width": 5.0,
        "attack_style": "laser_shot", "start_angle": -3.0, "end_angle": 3.0,
        "hitbox_top_scale": 0.70, "hitbox_bottom_scale": 0.20,
        "swing_sound": "magic_electric", "hit_sound": "magic_electric",
        "projectile_damage": 25.0, "projectile_speed": 760.0,
        "projectile_life": 1.65, "projectile_radius": 4.0,
        # A laser reads as a short travelling line rather than a glowing dot.
        "projectile_reflections": 3, "projectile_visual_length": 18.0,
        "heavy_charge_seconds": 0.70, "heavy_damage_mult": 1.35,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.12,
        "heavy_cooldown": 1.08, "heavy_attack_duration": 0.58,
        "heavy_max_targets": 6, "heavy_style": "laser_fan_5x5",
        "heavy_start_angle": -16.0, "heavy_end_angle": 16.0,
        "heavy_projectile_damage": 34.0, "heavy_projectile_speed": 840.0,
        "heavy_projectile_life": 3.20, "heavy_projectile_radius": 5.0,
        "heavy_projectile_reflections": 5, "heavy_projectile_count": 5,
        "heavy_projectile_spread_degrees": 18.0,
        "heavy_projectile_visual_length": 22.0,
    },
    "yoyo": {
        "item_id": "weapon_yoyo", "name": "溜溜球", "icon_glyph": "", "damage": 24.0,
        "delivery": "yoyo", "reach_tiles": 0.0,
        "cooldown": 0.44, "attack_duration": 0.34,
        "knockback": 78.0, "max_targets": 3,
        "visual_length": 25.0, "visual_width": 5.0,
        "attack_style": "yoyo_cast", "start_angle": -24.0, "end_angle": 10.0,
        "hitbox_top_scale": 0.72, "hitbox_bottom_scale": 0.16,
        "swing_sound": "dagger_swing", "hit_sound": "dagger_hit",
        "projectile_damage": 24.0, "projectile_speed": 520.0,
        "projectile_life": 1.10, "projectile_radius": 8.0,
        "projectile_range": 168.0, "projectile_return_speed": 620.0,
        "heavy_charge_seconds": 0.58, "heavy_damage_mult": 1.45,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.18,
        "heavy_cooldown": 0.90, "heavy_attack_duration": 0.64,
        "heavy_max_targets": 8, "heavy_style": "yoyo_orbit_storm",
        "heavy_start_angle": -150.0, "heavy_end_angle": 210.0,
        "heavy_projectile_damage": 35.0, "heavy_projectile_life": 1.35,
        "heavy_projectile_radius": 11.0, "heavy_orbit_radius": 104.0,
        "heavy_orbit_turns": 2.6,
    },
    "battle_top": {
        "item_id": "weapon_battle_top", "name": "戰鬥陀螺", "icon_glyph": "", "damage": 32.0,
        "delivery": "battle_top", "reach_tiles": 0.0,
        "cooldown": 0.52, "attack_duration": 0.40,
        "knockback": 116.0, "max_targets": 4,
        "visual_length": 24.0, "visual_width": 8.0,
        "attack_style": "top_launch", "start_angle": -40.0, "end_angle": 20.0,
        "hitbox_top_scale": 0.68, "hitbox_bottom_scale": 0.12,
        "swing_sound": "axe_swing", "hit_sound": "hammer_hit",
        "projectile_damage": 32.0, "projectile_speed": 410.0,
        "projectile_life": 1.45, "projectile_radius": 10.0,
        "projectile_gravity": 760.0, "projectile_bounces": 4,
        "heavy_charge_seconds": 0.66, "heavy_damage_mult": 1.55,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.35,
        "heavy_cooldown": 1.00, "heavy_attack_duration": 0.70,
        "heavy_max_targets": 10, "heavy_style": "top_tornado",
        "heavy_start_angle": -170.0, "heavy_end_angle": 190.0,
        "heavy_projectile_damage": 46.0, "heavy_projectile_speed": 470.0,
        "heavy_projectile_life": 2.10, "heavy_projectile_radius": 15.0,
        "heavy_projectile_gravity": 680.0, "heavy_projectile_bounces": 8,
    },
    "rpg_launcher": {
        "item_id": "weapon_rpg_launcher", "name": "RPG火箭筒", "icon_glyph": "", "damage": 64.0,
        "delivery": "rocket", "reach_tiles": 0.0,
        "cooldown": 0.86, "attack_duration": 0.52,
        "knockback": 188.0, "max_targets": 8,
        "visual_length": 38.0, "visual_width": 7.0,
        "attack_style": "rocket_launch", "start_angle": -10.0, "end_angle": 6.0,
        "hitbox_top_scale": 0.72, "hitbox_bottom_scale": 0.15,
        "swing_sound": "hammer_swing", "hit_sound": "hammer_hit",
        "projectile_damage": 64.0, "projectile_speed": 455.0,
        "projectile_life": 2.00, "projectile_radius": 6.0,
        "explosion_damage": 74.0, "explosion_radius": 72.0,
        "heavy_charge_seconds": 0.78, "heavy_damage_mult": 1.0,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.25,
        "heavy_cooldown": 1.28, "heavy_attack_duration": 0.82,
        "heavy_max_targets": 12, "heavy_style": "rocket_salvo",
        "heavy_start_angle": -18.0, "heavy_end_angle": 18.0,
        "heavy_projectile_count": 3, "heavy_projectile_spread_degrees": 13.0,
        "heavy_projectile_damage": 72.0, "heavy_projectile_speed": 500.0,
        "heavy_projectile_life": 2.20, "heavy_projectile_radius": 7.0,
        "heavy_explosion_damage": 96.0, "heavy_explosion_radius": 92.0,
    },
    "tnt": {
        "item_id": "weapon_tnt", "name": "TNT炸藥", "icon_glyph": "", "damage": 42.0,
        "delivery": "tnt", "reach_tiles": 0.0,
        "cooldown": 0.70, "attack_duration": 0.42,
        "knockback": 168.0, "max_targets": 6,
        "visual_length": 22.0, "visual_width": 8.0,
        "attack_style": "tnt_throw", "start_angle": -38.0, "end_angle": 18.0,
        "hitbox_top_scale": 0.0, "hitbox_bottom_scale": 0.0,
        "swing_sound": "hammer_swing", "hit_sound": "hammer_hit",
        "projectile_damage": 42.0, "projectile_speed": 330.0,
        "projectile_life": 1.30, "projectile_radius": 8.0,
        "projectile_gravity": 720.0,
        "explosion_damage": 58.0, "explosion_radius": 62.0,
        "blast_width": 1,
        "heavy_charge_seconds": 0.72, "heavy_damage_mult": 1.0,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.28,
        "heavy_cooldown": 1.20, "heavy_attack_duration": 0.72,
        "heavy_max_targets": 12, "heavy_style": "tnt_demolition",
        "heavy_start_angle": -58.0, "heavy_end_angle": 24.0,
        "heavy_projectile_damage": 58.0, "heavy_projectile_speed": 350.0,
        "heavy_projectile_life": 1.40, "heavy_projectile_radius": 10.0,
        "heavy_projectile_gravity": 680.0,
        "heavy_explosion_damage": 92.0, "heavy_explosion_radius": 96.0,
        "heavy_blast_width": 3,
        "heavy_use_limit": 5,
    },
    "flying_drone": {
        "item_id": "weapon_flying_drone", "name": "飛行無人機", "icon_glyph": "", "damage": 28.0,
        "delivery": "companion_drone", "reach_tiles": 0.0,
        "cooldown": 0.85, "attack_duration": 0.38,
        "knockback": 155.0, "max_targets": 1,
        "visual_length": 24.0, "visual_width": 8.0,
        "attack_style": "drone_dive", "start_angle": -15.0, "end_angle": 15.0,
        "hitbox_top_scale": 0.0, "hitbox_bottom_scale": 0.0,
        "swing_sound": "magic_electric", "hit_sound": "hammer_hit",
        "drone_acquire_radius": 280.0, "drone_scan_interval": 0.15,
        "drone_dive_speed": 430.0, "drone_return_speed": 350.0,
        "drone_hit_radius": 12.0,
        "heavy_charge_seconds": 0.72, "heavy_damage_mult": 1.0,
        "heavy_reach_tiles": 0.0, "heavy_knockback_mult": 1.0,
        "heavy_cooldown": 1.20, "heavy_attack_duration": 0.72,
        "heavy_max_targets": 8, "heavy_style": "drone_kamikaze",
        "heavy_start_angle": -30.0, "heavy_end_angle": 30.0,
        "heavy_explosion_damage": 76.0, "heavy_explosion_radius": 84.0,
        "heavy_consume_count": 1, "heavy_use_limit": 1,
    },
}

# Every accepted heavy attack spends one use.  A stack remains one backpack
# entry, while InventorySystem persists an independent counter for each copy.
for _weapon_id, _row in WEAPON_DEFS.items():
    _row.setdefault("heavy_use_limit", 10)

from systems.boss_weapon_defs import BOSS_WEAPON_DEFS
WEAPON_DEFS.update(BOSS_WEAPON_DEFS)

ITEM_TO_WEAPON = {str(v["item_id"]): k for k, v in WEAPON_DEFS.items()}
# Static treasure follows an exact four-slot cycle: one close-range/blade
# family item, then three shooting/throwing family items.  Selection within
# each family rotates independently, so every complete group remains 1:3.
CHEST_MELEE_WEAPONS = (
    "sword", "dagger", "spear", "battle_axe", "war_hammer",
    "greatsword", "whip",
)
CHEST_RANGED_WEAPONS = (
    "energy_bow", "laser_gun", "yoyo", "battle_top",
    "rpg_launcher", "flying_drone", "tnt",
)
CHEST_WEAPON_RATIO_PATTERN = ("melee", "ranged", "ranged", "ranged")
# Compatibility/export order remains a deterministic weighted sequence.
CHEST_WEAPON_ORDER = tuple(
    weapon_id
    for index in range(max(len(CHEST_MELEE_WEAPONS), len(CHEST_RANGED_WEAPONS)))
    for weapon_id in (
        CHEST_MELEE_WEAPONS[index % len(CHEST_MELEE_WEAPONS)],
        CHEST_RANGED_WEAPONS[(index * 3 + 0) % len(CHEST_RANGED_WEAPONS)],
        CHEST_RANGED_WEAPONS[(index * 3 + 1) % len(CHEST_RANGED_WEAPONS)],
        CHEST_RANGED_WEAPONS[(index * 3 + 2) % len(CHEST_RANGED_WEAPONS)],
    )
)


def chest_weapon_for_index(index, seed=0):
    """Return one deterministic chest weapon while preserving exact 1:3 type ratio."""
    index = max(0, int(index or 0))
    seed = max(0, int(seed or 0))
    block, slot = divmod(index, 4)
    if slot == 0:
        return CHEST_MELEE_WEAPONS[(block + seed) % len(CHEST_MELEE_WEAPONS)]
    ranged_index = block * 3 + (slot - 1) + seed
    return CHEST_RANGED_WEAPONS[ranged_index % len(CHEST_RANGED_WEAPONS)]


def weapon_from_item(item_id):
    return ITEM_TO_WEAPON.get(str(item_id), "")


def weapon_item(weapon_id):
    row = WEAPON_DEFS.get(str(weapon_id))
    if not row:
        return None
    return str(row["item_id"]), str(row["name"])


def weapon_icon_glyph(item_or_weapon):
    """Return a compact icon glyph for backpack/pickup UI, or empty string."""
    key = str(item_or_weapon or "")
    if key in WEAPON_DEFS:
        row = WEAPON_DEFS[key]
    else:
        wid = ITEM_TO_WEAPON.get(key)
        row = WEAPON_DEFS.get(wid) if wid else None
    return str((row or {}).get("icon_glyph", "") or "")
