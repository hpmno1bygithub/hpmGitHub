# -*- coding: utf-8 -*-
"""FIX102 swamp/rainforest-underground python; authored placement only.
New identifiers are creature.giant_python and weapon.python_whip. Existing
maps, species and definitions are never replaced by these defaults.
"""
HABITAT = '沼澤雨林地下'
CREATURES = {
    'giant_python': dict(name='巨型大蟒蛇', hp=3200., attack=26., speed=68.,
        w=200., h=106., hostile=True, locomotion='ground', boss=True,
        boss_title='巨型大蟒蛇', authored_only=True, poison_immune=True,
        habitat=HABITAT, loot=(('coin','錢幣',65),), respawn_seconds=300),
}
WEAPONS = {
    'python_whip': dict(item_id='weapon_python_whip',name='蟒蛇鞭',damage=46.,
        delivery='boss_python',reach_tiles=0.,cooldown=.62,attack_duration=.65,
        knockback=90.,max_targets=24,visual_length=54.,visual_width=3.,
        attack_style='whip_snap',start_angle=-68.,end_angle=24.,
        heavy_charge_seconds=.7,heavy_damage_mult=.52,heavy_reach_tiles=0.,
        heavy_knockback_mult=.4,heavy_cooldown=3.6,heavy_attack_duration=1.,
        heavy_max_targets=24,heavy_style='python_cyclone',heavy_start_angle=-130.,
        heavy_end_angle=70.,swing_sound='dagger_swing',hit_sound='dagger_hit',
        heavy_use_limit=20,normal_range_tiles=3.,tornado_start_tiles=1.,
        tornado_travel_tiles=4.,tornado_start_height_tiles=2.,tornado_end_height_tiles=5.,
        tornado_width_tiles=1.,tornado_seconds=3.,tornado_tick_seconds=.25,
        description='沼澤雨林地下蟒蛇掉落；普攻前方3格。重擊前1格起，3秒前移4格，旋風由2格長至5格。重擊20次後耗盡。'),
}
REWARDS={'giant_python':'python_whip'}

def make_actions():
    def row(state,effect,**kw):
        d=dict(enabled=True,animation_state=state,motion='stationary',effect=effect,
               range_min=0.,range_max=220.,vertical_range=140.,cooldown=2.4,
               duration=1.,speed=0.,lift_speed=0.,damage=26.,knockback=140.,
               hit_radius=120.,requires_ground=True,priority=25,chance=1.)
        d.update(kw);return d
    return {'creature.giant_python':dict(override_legacy_contact=True,actions={
        'attack':row('attack','python_tail'),
        'tornado':row('tornado','python_tornado',range_min=125.,range_max=340.,
                      vertical_range=220.,cooldown=6.,duration=1.25,damage=12.,priority=30),
    })}
