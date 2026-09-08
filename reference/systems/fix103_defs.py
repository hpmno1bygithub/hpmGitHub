# -*- coding: utf-8 -*-
"""FIX103 six additive bosses and their reward weapons."""
CREATURES={
    'lava_beetle_emperor':dict(name='熔岩甲蟲皇',hp=3900.,attack=30.,speed=78.,w=150.,h=108.,hostile=True,locomotion='ground',boss=True,boss_title='熔岩甲蟲皇',authored_only=True,habitat='地獄岩漿區／火山地下',loot=(('coin','錢幣',80),),respawn_seconds=300),
    'crystal_nine_tail':dict(name='冰晶九尾狐',hp=3600.,attack=27.,speed=150.,w=142.,h=104.,hostile=True,locomotion='ground',boss=True,boss_title='冰晶九尾狐',authored_only=True,habitat='雪地高原／冰川洞窟',loot=(('coin','錢幣',80),),respawn_seconds=300),
    'abyss_crab_king':dict(name='深海巨螯蟹王',hp=4300.,attack=34.,speed=54.,w=168.,h=112.,hostile=True,locomotion='water',boss=True,boss_title='深海巨螯蟹王',authored_only=True,habitat='深海／海底洞窟',loot=(('coin','錢幣',80),),respawn_seconds=300),
    'thunder_roc':dict(name='雷霆巨鷹',hp=3700.,attack=29.,speed=190.,w=158.,h=118.,hostile=True,locomotion='fly',boss=True,boss_title='雷霆巨鷹',authored_only=True,habitat='高層空島／暴風雲層',loot=(('coin','錢幣',80),),respawn_seconds=300),
    'drill_worm':dict(name='地底鑽岩巨蟲',hp=4200.,attack=33.,speed=92.,w=180.,h=86.,hostile=True,locomotion='ground',boss=True,boss_title='地底鑽岩巨蟲',authored_only=True,habitat='深層地下／水晶礦坑',loot=(('coin','錢幣',80),),respawn_seconds=300),
    'ancient_tree_demon':dict(name='古樹魔神',hp=4600.,attack=31.,speed=42.,w=170.,h=176.,hostile=True,locomotion='ground',boss=True,boss_title='古樹魔神',authored_only=True,habitat='雨林巨木深處',loot=(('coin','錢幣',80),),respawn_seconds=300),
}
WEAPONS={
    'magma_core_hammer':dict(item_id='weapon_magma_core_hammer',name='熔核戰錘',damage=72.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=20,description='普攻熔岩錘擊；重擊前方依序升起4道熔岩柱，最後一道最大。重攻20次後耗盡。'),
    'nine_tail_fan':dict(item_id='weapon_nine_tail_fan',name='九尾冰扇',damage=56.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=15,description='普攻扇射3枚冰刃；重擊前方5格形成冰霜暴風並連續落冰晶。重攻15次後耗盡。'),
    'abyss_claw':dict(item_id='weapon_abyss_claw',name='深海巨鉗',damage=68.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=20,description='普攻雙鉗剪擊；重擊形成巨大水壓鉗向內合攏並爆發水震波。重攻20次後耗盡。'),
    'thunder_longbow':dict(item_id='weapon_thunder_longbow',name='雷神長弓',damage=60.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=15,description='普攻高速雷箭可貫穿2個目標；重擊前方7格形成雷雲並連續落雷。重攻15次後耗盡。'),
    'earth_drill_lance':dict(item_id='weapon_earth_drill_lance',name='地脈鑽槍',damage=74.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=15,description='普攻長距離旋轉鑽刺；重擊向前鑽進5格並形成碎岩衝擊帶。重攻15次後耗盡。'),
    'world_tree_staff':dict(item_id='weapon_world_tree_staff',name='世界樹權杖',damage=58.0,delivery='boss_fix103',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=100.,max_targets=24,visual_length=48.,visual_width=8.,attack_style='fix103_normal',start_angle=-50.,end_angle=30.,heavy_charge_seconds=.72,heavy_damage_mult=1.0,heavy_reach_tiles=0.,heavy_knockback_mult=1.0,heavy_cooldown=3.2,heavy_attack_duration=.8,heavy_max_targets=32,heavy_style='fix103_heavy',heavy_start_angle=-100.,heavy_end_angle=70.,swing_sound='greatsword_swing',hit_sound='greatsword_hit',heavy_use_limit=20,description='普攻種子彈命中後纏繞；重擊前方5格召喚巨根與藤蔓連續穿刺3秒。重攻20次後耗盡。'),
}
REWARDS={
    'lava_beetle_emperor':'magma_core_hammer',
    'crystal_nine_tail':'nine_tail_fan',
    'abyss_crab_king':'abyss_claw',
    'thunder_roc':'thunder_longbow',
    'drill_worm':'earth_drill_lance',
    'ancient_tree_demon':'world_tree_staff',
}
def make_actions():
    def row(state,effect,**kw):
        d=dict(enabled=True,animation_state=state,motion='stationary',effect=effect,range_min=0.,range_max=250.,vertical_range=240.,cooldown=2.4,duration=1.,speed=0.,lift_speed=0.,damage=25.,knockback=130.,hit_radius=130.,requires_ground=False,priority=25,chance=1.)
        d.update(kw);return d
    return {
      'creature.lava_beetle_emperor':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_lava',damage=30.,cooldown=2.2),'special':row('special','fix103_lava'+'_heavy',damage=16.5,cooldown=5.5,range_max=420.,priority=32)}),
      'creature.crystal_nine_tail':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_ice',damage=27.,cooldown=2.2),'special':row('special','fix103_ice'+'_heavy',damage=14.9,cooldown=5.5,range_max=420.,priority=32)}),
      'creature.abyss_crab_king':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_water',damage=34.,cooldown=2.2),'special':row('special','fix103_water'+'_heavy',damage=18.7,cooldown=5.5,range_max=420.,priority=32)}),
      'creature.thunder_roc':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_thunder',damage=29.,cooldown=2.2),'special':row('special','fix103_thunder'+'_heavy',damage=16.0,cooldown=5.5,range_max=420.,priority=32)}),
      'creature.drill_worm':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_drill',damage=33.,cooldown=2.2),'special':row('special','fix103_drill'+'_heavy',damage=18.2,cooldown=5.5,range_max=420.,priority=32)}),
      'creature.ancient_tree_demon':dict(override_legacy_contact=True,actions={'attack':row('attack','fix103_roots',damage=31.,cooldown=2.2),'special':row('special','fix103_roots'+'_heavy',damage=17.1,cooldown=5.5,range_max=420.,priority=32)}),
    }
