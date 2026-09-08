# -*- coding: utf-8 -*-
"""FIX88 boss-only rewards. Data has no platform/import dependencies."""
BOSS_WEAPON_DEFS = {
    'quake_hammer': dict(
        item_id='weapon_quake_hammer', name='撼動大錘', damage=96.0,
        delivery='boss_hammer', reach_tiles=3.0, cooldown=.65, attack_duration=.5,
        knockback=240.0, max_targets=8, visual_length=54.0, visual_width=12.0,
        attack_style='hammer_slam', start_angle=-120., end_angle=65.,
        heavy_charge_seconds=.68, heavy_damage_mult=1.9, heavy_reach_tiles=4.0,
        heavy_knockback_mult=1.5, heavy_cooldown=1.05, heavy_attack_duration=.7,
        heavy_max_targets=16, heavy_style='quake_slam', heavy_start_angle=-150., heavy_end_angle=85.,
        swing_sound='hammer_swing', hit_sound='hammer_hit', heavy_use_limit=10,
        description='普通擊碎前方最多3格；重擊粉碎前方4×4格。'),
    'pressure_cannon': dict(
        item_id='weapon_pressure_cannon', name='高壓水槍砲', damage=50.0,
        delivery='boss_water', reach_tiles=0.0, cooldown=.70, attack_duration=.35,
        knockback=28.0, max_targets=32, visual_length=42.0, visual_width=10.0,
        attack_style='water_shot', start_angle=-3., end_angle=3.,
        heavy_charge_seconds=.65, heavy_damage_mult=2.6, heavy_reach_tiles=0.0,
        heavy_knockback_mult=1.4, heavy_cooldown=3.3, heavy_attack_duration=.55,
        heavy_max_targets=32, heavy_style='pressure_stream', heavy_start_angle=-8., heavy_end_angle=8.,
        swing_sound='magic_water', hit_sound='magic_water', heavy_use_limit=20,
        normal_seconds=.5, heavy_seconds=3.0, range_tiles=12.0,
        normal_width=6.0, heavy_width=24.0,
        description='細水柱0.5秒／粗水柱3秒；重擊固定朝角色面向前方，角色仍可自由移動。'),
    'bee_swarm': dict(
        item_id='weapon_bee_swarm', name='群峰追擊', damage=10.0,
        delivery='boss_swarm', reach_tiles=0.0, cooldown=.45, attack_duration=.35,
        knockback=24.0, max_targets=8, visual_length=22.0, visual_width=8.0,
        attack_style='swarm_call', start_angle=0., end_angle=0.,
        heavy_charge_seconds=.65, heavy_damage_mult=5.5, heavy_reach_tiles=0.0,
        heavy_knockback_mult=2.0, heavy_cooldown=2.0, heavy_attack_duration=.6,
        heavy_max_targets=8, heavy_style='swarm_sacrifice', heavy_start_angle=0., heavy_end_angle=0.,
        swing_sound='dagger_swing', hit_sound='dagger_hit', heavy_use_limit=1,
        bee_count=8, heavy_launch_interval=.18, drone_range_multiplier=1.5,
        description='八蜂自動追擊；重擊依序捨身衝撞，優先BOSS，耗用一組。'),
    'master_sword': dict(
        item_id='weapon_master_sword', name='宗師名劍', damage=88.0,
        delivery='boss_sword', reach_tiles=1.7, cooldown=.48, attack_duration=.4,
        knockback=220.0, max_targets=4, visual_length=48.0, visual_width=6.0,
        attack_style='wide_slash', start_angle=-95., end_angle=55.,
        hitbox_top_scale=1.1, hitbox_bottom_scale=-.05,
        heavy_charge_seconds=.72, heavy_damage_mult=1.8, heavy_reach_tiles=2.1,
        heavy_knockback_mult=1.4, heavy_cooldown=.95, heavy_attack_duration=.65,
        heavy_max_targets=6, heavy_style='master_crescent', heavy_start_angle=-126., heavy_end_angle=72.,
        swing_sound='greatsword_swing', hit_sound='greatsword_hit', heavy_use_limit=20,
        # Radius is twice the BASE greatsword radius; its presentation envelope
        # is reused exactly once, so the final crescent diameter is exactly 2×.
        heavy_projectile_damage=110.0, heavy_projectile_speed=390.0,
        heavy_projectile_life=1.10, heavy_projectile_radius=28.0,
        heavy_projectile_spawn_offset=58.0, heavy_projectile_visual_scale=1.5,
        description='普通揮斬；重擊發射直徑為巨劍月牙2倍、沿角色面向水平直飛的巨大月牙。'),
    'alien_cannon': dict(
        item_id='weapon_alien_cannon', name='外星能量砲', damage=64.0,
        delivery='boss_orbital', reach_tiles=0.0, cooldown=.85, attack_duration=.4,
        knockback=22.0, max_targets=32, visual_length=40.0, visual_width=12.0,
        attack_style='orbital_call', start_angle=-5., end_angle=5.,
        heavy_charge_seconds=.72, heavy_damage_mult=2.2, heavy_reach_tiles=0.0,
        heavy_knockback_mult=1.3, heavy_cooldown=3.4, heavy_attack_duration=.65,
        heavy_max_targets=32, heavy_style='orbital_twin', heavy_start_angle=-8., heavy_end_angle=8.,
        swing_sound='magic_electric', hit_sound='magic_electric', heavy_use_limit=10,
        normal_seconds=.5, heavy_seconds=3.0, column_height_tiles=8.0,
        normal_start_tiles=1.0, normal_travel_tiles=3.0,
        heavy_start_tiles=1.0, heavy_travel_tiles=4.0,
        description='普通：角色前1格起，向前掃3格、寬1格；重擊：角色前後各1格起，向外各掃4格、寬2格，共3秒。'),
}
from systems.fauna99_defs import WEAPONS as _WEAPONS99, REWARDS as _REWARDS99
BOSS_WEAPON_DEFS.update(_WEAPONS99)
from systems.python102_defs import WEAPONS as _WEAPONS102, REWARDS as _REWARDS102
BOSS_WEAPON_DEFS.update(_WEAPONS102)
from systems.fix103_defs import WEAPONS as _WEAPONS103, REWARDS as _REWARDS103
BOSS_WEAPON_DEFS.update(_WEAPONS103)
for _row in BOSS_WEAPON_DEFS.values():
    _row.update(boss_exclusive=True, defer_heavy_commit=True, icon_glyph='')
BOSS_REWARDS = {
    'abyss_colossus': 'quake_hammer',
    'loch_ness_monster': 'pressure_cannon',
    'queen_bee': 'bee_swarm',
    'wuxia_sword_tomb_master': 'master_sword',
    'biolume_heartwarden': 'alien_cannon',
}
BOSS_REWARDS.update(_REWARDS99)
BOSS_REWARDS.update(_REWARDS102)
BOSS_REWARDS.update(_REWARDS103)
BOSS_ITEM_LIMITS = {row['item_id']: row['heavy_use_limit'] for row in BOSS_WEAPON_DEFS.values()}

def with_boss_reward(species, loot):
    """Works for fresh creatures AND old-save/respawn loot tables. Idempotent."""
    result = tuple(loot or ())
    key = BOSS_REWARDS.get(str(species))
    if key:
        row = BOSS_WEAPON_DEFS[key]
        if not any(str(entry[0]) == row['item_id'] for entry in result if entry):
            result += ((row['item_id'], row['name'], 1),)
    return result
