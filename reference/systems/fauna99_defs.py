# -*- coding: utf-8 -*-
"""FIX99 expansion definitions. No implicit spawning or existing-asset overrides."""
CREATURES = {
 'vulture':dict(name='禿鷹',hp=65,attack=8,speed=88,w=42,h=40,hostile=False,locomotion='bird',loot=(('feather','羽毛',2),)),
 'peregrine_falcon':dict(name='遊隼',hp=48,attack=9,speed=180,w=32,h=27,hostile=False,locomotion='bird',loot=(('feather','羽毛',2),)),
 'butterfly_backdrop':dict(name='蝴蝶（背景生物）',hp=1,attack=0,speed=22,w=20,h=22,hostile=False,locomotion='background_fly',background_only=True,loot=(),respawn_enabled=False),
 'swan':dict(name='天鵝',hp=80,attack=8,speed=54,w=43,h=55,hostile=False,element='water',locomotion='waterbird',loot=(('feather','羽毛',3),)),
 'exp99_chimpanzee':dict(name='黑猩猩（擴充）',hp=145,attack=16,speed=86,w=44,h=58,hostile=False,locomotion='ground',loot=(('raw_meat','生肉',2),)),
 'corrosive_slime':dict(name='腐蝕黏菌',hp=35,attack=18,speed=0,w=30,h=28,hostile=True,locomotion='ceiling_drop',poison_immune=True,loot=(),respawn_seconds=60),
 'goblin_priest':dict(name='祭司哥布林',hp=120,attack=14,speed=51,w=30,h=50,hostile=True,locomotion='ground',loot=(('coin','錢幣',5),)),
 'goblin_lord':dict(name='領主哥布林',hp=245,attack=25,speed=67,w=43,h=63,hostile=True,locomotion='ground',loot=(('coin','錢幣',8),('iron_ore','鐵礦',2))),
 'goblin_redcap':dict(name='紅帽哥布林',hp=110,attack=13,speed=97,w=29,h=43,hostile=True,locomotion='ground',loot=(('coin','錢幣',4),)),
 'congo_kong':dict(name='剛果大金剛',hp=2800,attack=28,speed=138,w=112,h=132,hostile=True,locomotion='ground',boss=True,boss_title='剛果大金剛',loot=(('coin','錢幣',60),),respawn_seconds=300),
 'trihead_dragon':dict(name='三頭龍怪',hp=3400,attack=22,speed=45,w=166,h=178,hostile=True,locomotion='ground',boss=True,boss_title='三頭龍怪',loot=(('coin','錢幣',75),),respawn_seconds=300),
}
for _c in CREATURES.values():
 _c['authored_only']=True
WEAPONS = {
 'kong_gauntlets':dict(item_id='weapon_kong_gauntlets',name='金剛拳套',damage=40.,delivery='boss_fists',reach_tiles=0.,cooldown=.65,attack_duration=.55,knockback=35.,max_targets=24,visual_length=28.,visual_width=14.,attack_style='fist_combo',start_angle=-12.,end_angle=8.,heavy_charge_seconds=.65,heavy_damage_mult=.55,heavy_reach_tiles=0.,heavy_knockback_mult=1.,heavy_cooldown=3.5,heavy_attack_duration=.6,heavy_max_targets=24,heavy_style='fist_barrage',heavy_start_angle=-16.,heavy_end_angle=12.,swing_sound='hammer_swing',hit_sound='hammer_hit',heavy_use_limit=20,description='普通前方兩拳；重擊前方2格寬×3格高，連續拳擊3秒。'),
 'triple_staff':dict(item_id='weapon_triple_staff',name='三重魔法杖',damage=32.,delivery='boss_storm',reach_tiles=0.,cooldown=.9,attack_duration=.6,knockback=30.,max_targets=24,visual_length=42.,visual_width=9.,attack_style='triple_cast',start_angle=-30.,end_angle=-8.,heavy_charge_seconds=.75,heavy_damage_mult=1.6,heavy_reach_tiles=0.,heavy_knockback_mult=1.,heavy_cooldown=4.8,heavy_attack_duration=.8,heavy_max_targets=24,heavy_style='storm_call',heavy_start_angle=-60.,heavy_end_angle=-20.,swing_sound='magic_electric',hit_sound='magic_electric',heavy_use_limit=10,description='普通水／火／冰三球；重擊三球向前上方匯聚烏雲，前方5格多點落雷。'),
}
REWARDS={'congo_kong':'kong_gauntlets','trihead_dragon':'triple_staff'}

def make_actions():
 def a(state,effect='damage_knockback',**kw):
  row=dict(enabled=True,animation_state=state,motion='stationary',effect=effect,range_min=0.,range_max=80.,vertical_range=80.,cooldown=1.5,duration=0.,speed=150.,lift_speed=280.,damage=12.,knockback=70.,hit_radius=65.,requires_ground=False,priority=10,chance=1.)
  row.update(kw);return row
 return {
 'creature.vulture':dict(override_legacy_contact=True,actions={'attack':a('attack',motion='dive',speed=175,range_max=180,damage=8,hit_radius=35,cooldown=2.2)}),
 'creature.peregrine_falcon':dict(override_legacy_contact=True,actions={'attack':a('attack',motion='dive',speed=350,range_max=240,damage=9,hit_radius=30,cooldown=2)}),
 'creature.butterfly_backdrop':dict(override_legacy_contact=True,actions={}),
 'creature.swan':dict(override_legacy_contact=True,actions={'attack':a('attack',damage=8,hit_radius=52,range_max=75,cooldown=2.)}),
 'creature.exp99_chimpanzee':dict(override_legacy_contact=True,actions={'attack':a('attack',effect='kong_combo',damage=12,range_max=90,hit_radius=60,cooldown=2.),'leap':a('leap',motion='leap',damage=16,range_min=95,range_max=200,speed=145,lift_speed=240,hit_radius=70,cooldown=3.,requires_ground=True)}),
 'creature.corrosive_slime':dict(override_legacy_contact=True,actions={}),
 'creature.goblin_priest':dict(override_legacy_contact=True,actions={'attack':a('attack',effect='magic_bolt',element='fire',range_min=30,range_max=400,vertical_range=260,damage=14,speed=255,radius=8,cooldown=2.)}),
 'creature.goblin_lord':dict(override_legacy_contact=True,actions={'attack':a('attack',damage=25,hit_radius=78,range_max=90,knockback=190,cooldown=1.7),'charge':a('charge',motion='charge',range_min=100,range_max=210,speed=155,damage=20,hit_radius=78,cooldown=3.,requires_ground=True)}),
 'creature.goblin_redcap':dict(override_legacy_contact=True,actions={'attack':a('attack',damage=13,hit_radius=48,range_max=64,cooldown=.8),'lunge':a('lunge',motion='leap',range_min=75,range_max=145,speed=170,lift_speed=130,damage=16,hit_radius=50,cooldown=2.4,requires_ground=True)}),
 'creature.congo_kong':dict(override_legacy_contact=True,actions={'attack':a('attack',effect='kong_combo',range_max=140,hit_radius=95,damage=24,knockback=90,cooldown=1.6,priority=25),'barrage':a('barrage',effect='kong_combo',range_max=140,hit_radius=100,damage=12,knockback=45,duration=3.4,cooldown=6.,priority=28),'leap':a('leap',motion='leap',range_min=145,range_max=340,speed=255,lift_speed=340,damage=22,hit_radius=96,cooldown=2.5,priority=20,requires_ground=True)}),
 'creature.trihead_dragon':dict(override_legacy_contact=True,actions={'attack':a('attack',effect='triple_breath',range_max=760,vertical_range=420,speed=300,damage=22,hit_radius=22,cooldown=2.5,priority=20,life=3.5)}),
 }
