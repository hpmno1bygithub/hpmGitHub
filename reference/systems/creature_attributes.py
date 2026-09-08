# -*- coding: utf-8 -*-
"""FIX131 creature movement/habitat families and five-element affinity rules.

The five combat affinities are intentionally data-only and deterministic:
    water > fire
    fire > earth
    earth > electric
    electric > water
    fire > ice
Advantage = 2.0x, reverse matchup = 0.5x, unrelated = 1.0x.
"""

ELEMENTS = ('water','fire','electric','earth','ice')
ELEMENT_ZH = {
    'water':'水','fire':'火','electric':'電','earth':'土','ice':'冰',
}

# Explicit list for every shipped creature/monster/BOSS in FIX130.
CREATURE_ELEMENTS = {
    # 水
    'gull':'water','shore_crab':'water','sea_turtle':'water','heron':'water','otter':'water',
    'mangrove_crab':'water','sardine':'water','mackerel':'water','sea_bass':'water','carp':'water',
    'crucian_carp':'water','freshwater_bass':'water','mallard':'water','kingfisher':'water',
    'crocodile':'water','swamp_snake':'water','swamp_frog':'water','capybara':'water',
    'blue_poop':'water','toilet_man':'water','abyss_crab_king':'water','swan':'water','corrosive_slime':'water',
    # 火
    'creeper':'fire','fire_zombie':'fire','flying_imp':'fire','infernal_goat':'fire',
    'burning_slime':'fire','flame_turtle':'fire','exploding_wisp':'fire','lava_beetle_emperor':'fire',
    'goblin_priest':'fire','trihead_dragon':'fire',
    # 電
    'plane_monster':'electric','blue_scarab':'electric','dungeon_bandit_mage':'electric',
    'giant_bago_bird':'electric','lightning_zombie':'electric','speaker_man':'electric','thunder_roc':'electric',
    # 冰
    'snow_wolf':'ice','mountain_goat':'ice','ice_slime':'ice','snow_hare':'ice','ghost':'ice','crystal_nine_tail':'ice',
    # 土
    'slime':'earth','boar':'earth','wolf':'earth','deer':'earth','dragonfly':'earth','damselfly':'earth',
    'desert_scorpion':'earth','sand_lizard':'earth','desert_snake':'earth','desert_beetle':'earth',
    'swamp_slime':'earth','jungle_spider':'earth','jaguar':'earth','jungle_snake':'earth',
    'village_rat':'earth','bandit':'earth','village_dog':'earth','villager':'earth','cave_bat':'earth',
    'cave_spider':'earth','giant_spider_monster':'earth','cave_snorble':'earth','mummy':'earth',
    'sphinx_monster':'earth','minos':'earth','isis_serpent':'earth','zombie':'earth','giant_worm':'earth',
    'crystal_knight':'earth','crystal_slime':'earth','crystal_skeleton':'earth','crystal_lizard':'earth',
    'abyss_colossus':'earth','giant_python':'earth','drill_worm':'earth','ancient_tree_demon':'earth',
    'vulture':'earth','peregrine_falcon':'earth','butterfly_backdrop':'earth','exp99_chimpanzee':'earth',
    'goblin_lord':'earth','goblin_redcap':'earth','congo_kong':'earth',
}

# One attacker may have more than one advantage (fire > earth AND ice).
ADVANTAGE_TARGETS = {
    'water': frozenset(('fire',)),
    'fire': frozenset(('earth','ice')),
    'earth': frozenset(('electric',)),
    'electric': frozenset(('water',)),
    'ice': frozenset(),
}


def normalize_element(value, default='earth'):
    key=str(value or default).lower()
    return key if key in ELEMENTS else str(default)


def creature_element(species, profile=None):
    key=str(species or '')
    if key in CREATURE_ELEMENTS:
        return CREATURE_ELEMENTS[key]
    # New/custom creatures remain in the five-element system.  Respect an
    # authored valid affinity, otherwise use earth as the stable neutral-biome
    # default rather than creating a sixth 'neutral' affinity.
    if isinstance(profile,dict):
        authored=str(profile.get('element','') or '').lower()
        if authored in ELEMENTS:return authored
    return 'earth'


def apply_creature_elements(archetypes):
    for species,profile in (archetypes or {}).items():
        if isinstance(profile,dict):
            profile['element']=creature_element(species,profile)
    return archetypes


def elemental_multiplier(attacker_element,target_element):
    """Return exactly 2.0 / 0.5 / 1.0 for the requested five-element chart."""
    atk=normalize_element(attacker_element,'earth')
    tgt=normalize_element(target_element,'earth')
    if tgt in ADVANTAGE_TARGETS.get(atk,()):return 2.0
    if atk in ADVANTAGE_TARGETS.get(tgt,()):return 0.5
    return 1.0


# MapEditor per-placement ecology modes.  ``default`` preserves the species'
# authored low-level locomotion; the other modes intentionally override it.
HABITAT_MODES=('default','fly_rest','aquatic','land','burrow')
HABITAT_ZH={
    'default':'沿用物種',
    'fly_rest':'飛行：空中活動／落地休息',
    'aquatic':'水中：無水無法移動',
    'land':'陸地：行走／奔跑，可越過2格',
    'burrow':'岩土躲藏：完整挖空圖塊後現身',
}

FLYING_LOCOMOTION=frozenset(('bird','insect_low','fly_low','cave_fly','ghost_fly','sky_fly','background_fly','fly'))
AQUATIC_LOCOMOTION=frozenset(('fish','swim','waterbird','water'))


def normalize_habitat_mode(value):
    key=str(value or 'default')
    return key if key in HABITAT_MODES else 'default'


def default_habitat_mode(profile):
    loc=str((profile or {}).get('locomotion','ground') or 'ground')
    if loc in AQUATIC_LOCOMOTION:return 'aquatic'
    if loc in FLYING_LOCOMOTION:return 'fly_rest'
    return 'land'


def effective_locomotion(profile,row=None):
    mode=normalize_habitat_mode((row or {}).get('habitat_mode','default'))
    if mode=='fly_rest':return 'bird'
    if mode=='aquatic':return 'fish'
    if mode in ('land','burrow'):return 'ground'
    loc=str((profile or {}).get('locomotion','ground') or 'ground')
    # FIX103 used short aliases before the common locomotion families existed.
    if loc=='fly':return 'bird'
    if loc=='water':return 'fish'
    return loc
