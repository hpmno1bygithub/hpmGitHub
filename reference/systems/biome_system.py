# -*- coding: utf-8 -*-
"""Biome metadata and deterministic climate-aware creature population."""
import math
from config import TILE_SIZE
from entities.creature import Creature
from world.tile_registry import LAYERED_SOLID_TILES, AIR, tile_def
from world.soil_layers import soil_top_ratio
from world.biomes import (
    BIOME_OCEAN, BIOME_SWAMP, BIOME_LAKE, BIOME_PLAINS, BIOME_DESERT, BIOME_VILLAGE,
    BIOME_RAINFOREST, BIOME_SNOW_MOUNTAIN,
    BIOME_LABELS, default_biome_spans, normalize_biome_spans,
    biome_at_tile_x,
)

# All actors are targetable. `hostile=False` means they do not initiate combat.
# FIX20: w/h are collision + procedural-fallback dimensions only. Edited pixel
# art keeps the global 1-cell=2.5-world-px density and is never stretched to w/h.
CREATURE_ARCHETYPES = {
    "slime": dict(name="史萊姆", hp=55, attack=5, speed=44, w=30, h=25, hostile=True,
                  loot=(("slime_gel", "史萊姆凝膠", 2),)),
    "boar": dict(name="野豬", hp=85, attack=9, speed=58, w=40, h=28, hostile=True,
                 loot=(("raw_meat", "生肉", 2), ("hide", "獸皮", 1))),
    "wolf": dict(name="灰狼", hp=74, attack=10, speed=72, w=38, h=29, hostile=True,
                 loot=(("raw_meat", "生肉", 1), ("fur", "毛皮", 2))),
    "deer": dict(name="鹿", hp=70, attack=4, speed=76, w=40, h=34, hostile=False,
                 loot=(("raw_meat", "生肉", 2), ("hide", "獸皮", 1))),

    "gull": dict(name="海鷗", hp=26, attack=2, speed=88, w=30, h=18, hostile=False, locomotion="bird",
                 loot=(("feather", "羽毛", 1),)),
    "shore_crab": dict(name="岸蟹", hp=34, attack=4, speed=40, w=26, h=16, hostile=False,
                        loot=(("chitin", "甲殼", 1),)),
    "sea_turtle": dict(name="海龜", hp=98, attack=3, speed=28, w=44, h=24, hostile=False, element="water", locomotion="swim",
                        loot=(("hide", "獸皮", 1),)),
    "heron": dict(name="蒼鷺", hp=38, attack=4, speed=58, w=28, h=36, hostile=False, locomotion="bird",
                   loot=(("feather", "羽毛", 2),)),
    "otter": dict(name="水獺", hp=62, attack=5, speed=62, w=36, h=22, hostile=False,
                   loot=(("fur", "毛皮", 1), ("raw_meat", "生肉", 1))),
    "mangrove_crab": dict(name="紅樹林蟹", hp=36, attack=5, speed=42, w=26, h=16, hostile=False,
                           loot=(("chitin", "甲殼", 1),)),

    # Aquatic animals use continuous forward swimming with smooth depth
    # correction; they never run the terrestrial gravity/walk controller.
    "sardine": dict(name="沙丁魚", hp=18, attack=1, speed=74, w=24, h=10, hostile=False, element="water", locomotion="fish",
                    loot=(("raw_fish", "生魚肉", 1),)),
    "mackerel": dict(name="鯖魚", hp=28, attack=2, speed=86, w=30, h=12, hostile=False, element="water", locomotion="fish",
                     loot=(("raw_fish", "生魚肉", 1),)),
    "sea_bass": dict(name="海鱸", hp=42, attack=3, speed=68, w=34, h=14, hostile=False, element="water", locomotion="fish",
                     loot=(("raw_fish", "生魚肉", 2),)),
    "carp": dict(name="鯉魚", hp=34, attack=2, speed=58, w=31, h=13, hostile=False, element="water", locomotion="fish",
                 loot=(("raw_fish", "生魚肉", 1),)),
    "crucian_carp": dict(name="鯽魚", hp=24, attack=1, speed=54, w=25, h=11, hostile=False, element="water", locomotion="fish",
                         loot=(("raw_fish", "生魚肉", 1),)),
    "freshwater_bass": dict(name="淡水鱸", hp=40, attack=3, speed=70, w=33, h=14, hostile=False, element="water", locomotion="fish",
                            loot=(("raw_fish", "生魚肉", 2),)),
    "mallard": dict(name="綠頭鴨", hp=30, attack=2, speed=54, w=28, h=16, hostile=False, element="water", locomotion="waterbird",
                    loot=(("feather", "羽毛", 1),)),
    "kingfisher": dict(name="翠鳥", hp=22, attack=2, speed=90, w=24, h=14, hostile=False, locomotion="bird",
                       loot=(("feather", "羽毛", 1),)),
    "dragonfly": dict(name="蜻蜓", hp=8, attack=0, speed=96, w=16, h=7, hostile=False, locomotion="insect_low",
                      loot=(("insect_wing", "昆蟲翅", 1),)),
    "damselfly": dict(name="豆娘", hp=7, attack=0, speed=82, w=13, h=6, hostile=False, locomotion="insect_low",
                      loot=(("insect_wing", "昆蟲翅", 1),)),

    "desert_scorpion": dict(name="沙漠蠍", hp=48, attack=8, speed=52, w=30, h=18, hostile=True,
                            loot=(("scorpion_tail", "蠍尾", 1), ("chitin", "甲殼", 1))),
    "sand_lizard": dict(name="沙蜥", hp=42, attack=5, speed=66, w=34, h=18, hostile=False,
                        loot=(("lizard_scale", "蜥蜴鱗", 2), ("raw_meat", "生肉", 1))),
    "desert_snake": dict(name="沙蛇", hp=46, attack=8, speed=68, w=36, h=14, hostile=True,
                         loot=(("snake_skin", "蛇皮", 1), ("toxic_sac", "毒囊", 1))),
    "desert_beetle": dict(name="沙甲蟲", hp=50, attack=6, speed=46, w=29, h=18, hostile=True,
                          loot=(("chitin", "甲殼", 2),)),

    "swamp_slime": dict(name="沼澤史萊姆", hp=68, attack=7, speed=38, w=32, h=26, hostile=True,
                        loot=(("slime_gel", "史萊姆凝膠", 2), ("toxic_sac", "毒囊", 1))),
    "crocodile": dict(name="鱷魚", hp=125, attack=14, speed=52, w=52, h=24, hostile=True, element="water",
                      loot=(("raw_meat", "生肉", 3), ("hide", "獸皮", 2), ("tooth", "利齒", 1))),
    "swamp_snake": dict(name="沼澤蛇", hp=52, attack=9, speed=62, w=38, h=14, hostile=True,
                        loot=(("snake_skin", "蛇皮", 1), ("toxic_sac", "毒囊", 1))),
    "swamp_frog": dict(name="沼澤蛙", hp=34, attack=3, speed=54, w=26, h=17, hostile=False, element="water",
                       loot=(("frog_leg", "蛙腿", 1),)),

    "jungle_spider": dict(name="雨林巨蛛", hp=58, attack=9, speed=64, w=34, h=20, hostile=True,
                          loot=(("silk", "蛛絲", 2), ("toxic_sac", "毒囊", 1))),
    "jaguar": dict(name="美洲豹", hp=96, attack=13, speed=82, w=44, h=29, hostile=True,
                   loot=(("raw_meat", "生肉", 2), ("hide", "獸皮", 2))),
    "jungle_snake": dict(name="雨林蛇", hp=54, attack=10, speed=70, w=38, h=14, hostile=True,
                         loot=(("snake_skin", "蛇皮", 1), ("toxic_sac", "毒囊", 1))),
    "capybara": dict(name="水豚", hp=82, attack=4, speed=48, w=44, h=28, hostile=False,
                     loot=(("raw_meat", "生肉", 2), ("hide", "獸皮", 1))),

    "snow_wolf": dict(name="雪狼", hp=88, attack=12, speed=74, w=40, h=30, hostile=True,
                      loot=(("raw_meat", "生肉", 2), ("white_fur", "白色毛皮", 2))),
    "mountain_goat": dict(name="山羊", hp=72, attack=6, speed=56, w=40, h=30, hostile=False,
                          loot=(("raw_meat", "生肉", 2), ("hide", "獸皮", 1), ("horn", "羊角", 1))),
    "ice_slime": dict(name="冰史萊姆", hp=66, attack=8, speed=42, w=31, h=25, hostile=True, element="ice",
                      loot=(("slime_gel", "史萊姆凝膠", 1), ("ice_crystal", "冰晶", 1))),
    "snow_hare": dict(name="雪兔", hp=36, attack=2, speed=86, w=27, h=20, hostile=False,
                      loot=(("raw_meat", "小塊生肉", 1), ("white_fur", "白色毛皮", 1))),

    "village_rat": dict(name="村莊鼠", hp=28, attack=3, speed=70, w=24, h=15, hostile=True,
                        loot=(("raw_meat", "小塊生肉", 1),)),
    "bandit": dict(name="盜賊", hp=105, attack=12, speed=60, w=30, h=52, hostile=True,
                   loot=(("cloth", "布料", 2), ("coin", "錢幣", 4))),
    "village_dog": dict(name="村犬", hp=64, attack=7, speed=70, w=36, h=24, hostile=False,
                        loot=(("raw_meat", "生肉", 1),)),
    "villager": dict(name="村民", hp=90, attack=5, speed=44, w=30, h=52, hostile=False,
                     loot=(("cloth", "布料", 1), ("coin", "錢幣", 2))),

    # FIX29 underground ecology. These actors are spawned only from authored
    # underground cavern/dungeon metadata and remain inactive until their chunk
    # is streamed near the player.
    "cave_bat": dict(name="洞穴蝙蝠", hp=30, attack=7, speed=92, w=30, h=18, hostile=True, locomotion="cave_fly",
                     loot=(("hide", "獸皮", 1),)),
    "cave_spider": dict(name="洞穴巨蛛", hp=72, attack=10, speed=58, w=36, h=21, hostile=True,
                        loot=(("silk", "蛛絲", 2), ("toxic_sac", "毒囊", 1))),
    # FIX41 underground additions. These are separate species/visual assets, not
    # scaled versions of existing cave actors.
    "giant_spider_monster": dict(name="巨型蜘蛛怪", hp=210, attack=21, speed=62, w=74, h=38, hostile=True,
                                  loot=(("silk", "巨蛛絲", 4), ("toxic_sac", "大型毒囊", 2), ("chitin", "厚甲殼", 2))),
    "cave_snorble": dict(name="洞洞傻呼嚕", hp=155, attack=13, speed=42, w=58, h=36, hostile=True,
                          loot=(("snorble_fur", "呼嚕毛", 2), ("raw_meat", "洞穴獸肉", 2))),
    "plane_monster": dict(name="飛機怪獸", hp=132, attack=16, speed=118, w=78, h=34, hostile=True, locomotion="cave_fly",
                           loot=(("wing_plate", "飛翼甲片", 2), ("monster_drop", "怪獸核心", 1))),
    "mummy":dict(name="木乃伊",hp=150,attack=14,speed=34,w=34,h=54,hostile=True,loot=(("ancient_cloth","古代繃帶",2),)),
    "sphinx_monster":dict(name="獅身人面怪",hp=310,attack=24,speed=62,w=78,h=50,hostile=True,loot=(("ancient_gem","古代寶石",1),)),
    "minos":dict(name="米諾斯",hp=390,attack=30,speed=56,w=58,h=74,hostile=True,loot=(("minos_horn","米諾斯之角",2),)),
    "isis_serpent":dict(name="伊西斯",hp=265,attack=20,speed=50,w=44,h=70,hostile=True,loot=(("serpent_scale","神蛇鱗片",3),)),
    "blue_scarab":dict(name="藍甲蟲",hp=28,attack=5,speed=72,w=24,h=14,hostile=True,loot=(("scarab_shell","藍甲蟲殼",1),)),
    "zombie": dict(name="殭屍", hp=120, attack=12, speed=36, w=31, h=53, hostile=True,
                   loot=(("cloth", "布料", 1), ("coin", "錢幣", 1))),
    "giant_worm": dict(name="巨型蠕蟲", hp=145, attack=15, speed=48, w=56, h=27, hostile=True,
                       loot=(("raw_meat", "生肉", 3), ("hide", "獸皮", 1))),
    "dungeon_bandit_mage": dict(name="地下城魔法強盜", hp=110, attack=8, speed=46, w=31, h=52, hostile=True,
                                 loot=(("cloth", "布料", 2), ("coin", "錢幣", 6))),
    # FIX30 expanded underground bestiary.  These remain underground-only and
    # therefore do not change the surface biome rotations.
    "creeper": dict(name="苦力帕", hp=82, attack=18, speed=54, w=34, h=50, hostile=True,
                    loot=(("sulfur_powder", "硫磺粉", 2), ("monster_drop", "地下魔物素材", 1))),
    "blue_poop": dict(name="藍色大便", hp=64, attack=9, speed=44, w=31, h=27, hostile=True,
                      loot=(("slime_gel", "黏液", 2), ("blue_core", "藍色核心", 1))),
    "giant_bago_bird": dict(name="彩色巨型巴戈鳥", hp=165, attack=17, speed=96, w=68, h=48, hostile=True, locomotion="cave_fly",
                            loot=(("rainbow_feather", "彩色巨羽", 2), ("raw_meat", "生肉", 2))),
    "fire_zombie": dict(name="火焰殭屍", hp=145, attack=13, speed=34, w=33, h=54, hostile=True, element="fire",
                        loot=(("ember_core", "餘燼核心", 1), ("cloth", "焦黑布料", 1))),
    "lightning_zombie": dict(name="閃電殭屍", hp=138, attack=12, speed=40, w=33, h=54, hostile=True, element="electric",
                             loot=(("electric_core", "電能核心", 1), ("cloth", "布料", 1))),
    "ghost": dict(name="幽靈", hp=74, attack=11, speed=72, w=38, h=44, hostile=True, locomotion="ghost_fly",
                  loot=(("ectoplasm", "靈質", 2),)),
    # FIX31 new dungeon humanoids. Kept original to this project: a ceramic
    # toilet-bodied mutant and a speaker-headed raider.
    "toilet_man": dict(name="馬桶人", hp=172, attack=17, speed=43, w=40, h=56, hostile=True,
                       loot=(("ceramic_shard", "陶瓷碎片", 2), ("coin", "錢幣", 2))),
    "speaker_man": dict(name="音響人", hp=158, attack=15, speed=52, w=38, h=56, hostile=True,
                        loot=(("speaker_core", "音響核心", 1), ("wire", "電線", 2))),
    # FIX62 themed underground bestiary. Every visual source is authored on a
    # 32x32 canvas; these dimensions are compact collision silhouettes only.
    "flying_imp": dict(name="飛行小惡魔", hp=72, attack=12, speed=105, w=30, h=28, hostile=True,
                        locomotion="cave_fly", element="fire",
                        loot=(("imp_horn", "小惡魔角", 1), ("ember_core", "餘燼核心", 1))),
    "infernal_goat": dict(name="煉獄羊", hp=145, attack=18, speed=70, w=44, h=32, hostile=True,
                           element="fire", loot=(("infernal_horn", "煉獄羊角", 2), ("raw_meat", "焦熱獸肉", 2))),
    "burning_slime": dict(name="燃燒史萊姆", hp=92, attack=12, speed=44, w=32, h=27, hostile=True,
                           element="fire", loot=(("slime_gel", "燃燒黏液", 2), ("ember_core", "餘燼核心", 1))),
    "flame_turtle": dict(name="火炎龜", hp=180, attack=16, speed=34, w=46, h=27, hostile=True,
                          element="fire", loot=(("flame_shell", "火炎龜甲", 2), ("ember_core", "餘燼核心", 1))),
    "exploding_wisp": dict(name="自爆鬼火", hp=48, attack=24, speed=82, w=26, h=28, hostile=True,
                            locomotion="cave_fly", element="fire",
                            loot=(("wisp_ash", "鬼火灰燼", 2),)),
    "crystal_knight": dict(name="水晶騎士", hp=205, attack=21, speed=46, w=34, h=52, hostile=True,
                            element="crystal", loot=(("crystal_shard", "水晶碎片", 3), ("knight_plate", "晶甲碎片", 1))),
    "crystal_slime": dict(name="水晶史萊姆", hp=95, attack=12, speed=40, w=32, h=27, hostile=True,
                           element="crystal", loot=(("crystal_shard", "水晶碎片", 2), ("slime_gel", "晶化黏液", 1))),
    "crystal_skeleton": dict(name="水晶骷髏怪", hp=135, attack=16, speed=52, w=32, h=50, hostile=True,
                              element="crystal", loot=(("crystal_bone", "水晶骨", 2), ("crystal_shard", "水晶碎片", 2))),
    "crystal_lizard": dict(name="水晶蜥蜴", hp=105, attack=14, speed=68, w=43, h=23, hostile=True,
                            element="crystal", loot=(("crystal_scale", "水晶鱗片", 2), ("crystal_shard", "水晶碎片", 1))),
    # Giant deep-dungeon boss.  boss=True drives the dedicated global boss bar.
    "abyss_colossus": dict(name="深淵巨像", hp=1350, attack=36, speed=34, w=88, h=92, hostile=True,
                           boss=True, boss_title="深淵巨像",
                           loot=(("abyss_core", "深淵核心", 1), ("coin", "錢幣", 40), ("monster_drop", "巨像碎片", 6), ("weapon_quake_hammer", "撼動大錘", 1))),
}

BIOME_CREATURE_ROTATION = {
    BIOME_OCEAN: ("sardine", "mackerel", "sea_bass", "sea_turtle", "gull", "shore_crab"),
    BIOME_SWAMP: ("mangrove_crab", "crocodile", "swamp_snake", "swamp_frog", "swamp_slime", "otter", "heron"),
    BIOME_LAKE: ("carp", "crucian_carp", "freshwater_bass", "mallard", "kingfisher", "dragonfly", "damselfly"),
    BIOME_PLAINS: ("slime", "boar", "wolf", "deer"),
    BIOME_DESERT: ("desert_scorpion", "sand_lizard", "desert_snake", "desert_beetle"),
    BIOME_VILLAGE: ("village_rat", "bandit", "village_dog", "villager"),
    BIOME_RAINFOREST: ("jungle_spider", "jaguar", "jungle_snake", "capybara"),
    BIOME_SNOW_MOUNTAIN: ("snow_wolf", "mountain_goat", "ice_slime", "snow_hare"),
}


# FIX99: explicit-placement expansion; never changes automatic map populations.
from systems.python102_defs import CREATURES as _PYTHON102
for _species, _cfg in _PYTHON102.items():
    CREATURE_ARCHETYPES.setdefault(_species, dict(_cfg))

from systems.fix103_defs import CREATURES as _FIX103
for _species, _cfg in _FIX103.items():
    CREATURE_ARCHETYPES.setdefault(_species, dict(_cfg))

from systems.fauna99_defs import CREATURES as _FAUNA99
for _species, _cfg in _FAUNA99.items():
    CREATURE_ARCHETYPES.setdefault(_species, dict(_cfg))

# FIX131: every shipped creature/monster/BOSS belongs to exactly one of the
# five combat affinities.  This intentionally overrides older partial values
# such as 'crystal' so the runtime never falls outside water/fire/electric/
# earth/ice.
from systems.creature_attributes import apply_creature_elements
apply_creature_elements(CREATURE_ARCHETYPES)


class BiomeSystem:
    def __init__(self, world, map_loader=None, env=None, water_system=None):
        self.world = world
        self.env = env
        self.water_system = water_system
        meta = {}
        if map_loader is not None:
            try:
                meta = dict((map_loader.payload or {}).get("metadata", {}))
            except Exception:
                meta = {}
        self.metadata = dict(meta)
        self.spans = normalize_biome_spans(meta.get("biome_spans"), world.width_tiles)
        # FIX77: biome spans are immutable during one loaded map. The previous
        # hot query called normalize_biome_spans() for every creature patrol
        # probe, rebuilding the same nested list thousands of times per second.
        # One compact tile-indexed lookup preserves exact boundary behavior and
        # turns every runtime query into a clamped list access.
        width = max(1, int(world.width_tiles))
        biome_by_tx = [BIOME_PLAINS] * width
        for start, end, biome in self.spans:
            lo = max(0, min(width, int(start)))
            hi = max(lo, min(width, int(end)))
            if hi > lo:
                biome_by_tx[lo:hi] = [str(biome)] * (hi - lo)
        self._biome_by_tx = tuple(biome_by_tx)
        self.version = int(meta.get("biome_version", 0) or 0)

    def biome_at_tile(self, tx):
        lookup = self._biome_by_tx
        index = max(0, min(len(lookup) - 1, int(tx)))
        return lookup[index]

    def biome_at_world_x(self, x):
        return self.biome_at_tile(int(float(x) // TILE_SIZE))

    def label_at_world_x(self, x):
        return BIOME_LABELS.get(self.biome_at_world_x(x), "溫帶平原")

    def _surface_pos(self, tx, fallback_y):
        tx = max(1, min(self.world.width_tiles - 2, int(tx)))
        row = self.world.fauna_surface_row(tx)
        if row is None:
            return (tx + 0.5) * TILE_SIZE, float(fallback_y)
        top_ratio = 0.0
        try:
            if self.world.get_tile(tx, int(row)) in LAYERED_SOLID_TILES:
                top_ratio = float(soil_top_ratio(self.world.soil_layer_mask_at(tx, int(row))))
        except Exception:
            top_ratio = 0.0
        return (tx + 0.5) * TILE_SIZE, (float(row) + top_ratio) * TILE_SIZE

    def _water_rows(self, tx):
        """Topmost contiguous free-water column, excluding deep waterway."""
        if self.env is None:
            return ()
        tx = max(0, min(self.world.width_tiles - 1, int(tx)))
        # Only free surface water counts as aquatic habitat. Saturated soil and
        # the deep underground waterway are intentionally excluded.
        solid_row = self.world.fauna_surface_row(tx)
        rows = [
            ty for ty in range(self.world.height_tiles)
            if float(self.env.water_amount(tx, ty)) > 0.025
            and (solid_row is None or int(ty) < int(solid_row))
        ]
        if not rows:
            return ()
        rows.sort()
        group = [rows[0]]
        for ty in rows[1:]:
            if ty == group[-1] + 1:
                group.append(ty)
            else:
                break
        return tuple(group)

    def _find_water_tx(self, tx, start, end, min_cells=1):
        """Return a real liquid column or None.

        FIX26 deliberately never falls back to the requested tile for aquatic
        spawns: that old fallback was how fish could initialise on dry land / in
        open air when the authored map had no water at the expected fraction.
        """
        tx = max(int(start), min(int(end) - 1, int(tx)))
        max_radius = max(1, int(end) - int(start))
        for radius in range(max_radius + 1):
            candidates = (tx,) if radius == 0 else (tx - radius, tx + radius)
            for cx in candidates:
                if not (int(start) <= cx < int(end)):
                    continue
                if len(self._water_rows(cx)) >= int(min_cells):
                    return cx
        return None

    def _find_dry_tx(self, tx, start, end):
        """Nearest standable dry tile for birds that must perch before flight."""
        tx = max(int(start), min(int(end) - 1, int(tx)))
        max_radius = max(1, int(end) - int(start))
        for radius in range(max_radius + 1):
            candidates = (tx,) if radius == 0 else (tx - radius, tx + radius)
            for cx in candidates:
                if not (int(start) <= cx < int(end)):
                    continue
                if self._water_rows(cx):
                    continue
                if self.world.fauna_surface_row(cx) is not None:
                    return cx
        return None

    def _low_air_pos(self, tx, fallback_y, lift_px=28.0):
        """Low-altitude spawn that is guaranteed to be above the local surface."""
        tx = max(1, min(self.world.width_tiles - 2, int(tx)))
        ground_x, ground_y = self._surface_pos(tx, fallback_y)
        rows = self._water_rows(tx)
        support_y = float(ground_y)
        if rows:
            _wx, water_y = self._water_surface_pos(tx, fallback_y, lift_px=0.0)
            support_y = min(support_y, float(water_y))
        return ground_x, support_y - max(16.0, min(52.0, float(lift_px)))

    def _aquatic_pos(self, tx, fallback_y, depth_fraction=0.55, body_h=12.0):
        rows = self._water_rows(tx)
        if not rows:
            return self._surface_pos(tx, fallback_y)
        top = min(rows); bottom = max(rows)
        amount = max(0.0, min(1.0, float(self.env.water_amount(tx, top))))
        if self.water_system is not None:
            try:
                surface_y = float(self.water_system.surface_y(tx, top))
            except Exception:
                surface_y = (float(top) + 1.0 - amount) * TILE_SIZE
        else:
            surface_y = (float(top) + 1.0 - amount) * TILE_SIZE
        floor_y = float(bottom + 1) * TILE_SIZE
        fy = max(0.20, min(0.78, float(depth_fraction)))
        center_y = surface_y + max(4.0, floor_y - surface_y) * fy
        return (tx + 0.5) * TILE_SIZE, center_y + float(body_h) * 0.5

    def _water_surface_pos(self, tx, fallback_y, lift_px=0.0):
        rows = self._water_rows(tx)
        if not rows:
            x, y = self._surface_pos(tx, fallback_y)
            return x, y - float(lift_px)
        top = min(rows)
        amount = max(0.0, min(1.0, float(self.env.water_amount(tx, top))))
        if self.water_system is not None:
            try:
                sy = float(self.water_system.surface_y(tx, top))
            except Exception:
                sy = (float(top) + 1.0 - amount) * TILE_SIZE
        else:
            sy = (float(top) + 1.0 - amount) * TILE_SIZE
        return (tx + 0.5) * TILE_SIZE, sy - float(lift_px)

    def _solid_tile(self, tx, ty):
        if not (0 <= int(tx) < self.world.width_tiles and 0 <= int(ty) < self.world.height_tiles):
            return False
        try:
            return bool(tile_def(self.world.get_tile(int(tx), int(ty))).solid)
        except Exception:
            return False

    def _underground_floor_pos(self, tx, preferred_row, body_h=24.0, y_min=18, y_max=None):
        """Find a solid underground floor with enough air above the actor."""
        tx=max(1,min(self.world.width_tiles-2,int(tx)))
        y_min=max(1,int(y_min)); y_max=min(self.world.height_tiles-1,int(y_max if y_max is not None else self.world.height_tiles-1))
        clearance=max(2,int((float(body_h)+TILE_SIZE-1)//TILE_SIZE)+1)
        candidates=[]
        for ty in range(y_min,y_max+1):
            if not self._solid_tile(tx,ty):
                continue
            clear=True
            for q in range(1,clearance+1):
                ay=ty-q
                if ay<0 or self._solid_tile(tx,ay):
                    clear=False; break
                # FIX29: terrestrial dungeon creatures must not initialize in
                # flooded underground corridors.  Check the actor clearance,
                # not the solid floor cell itself.
                if self.env is not None:
                    try:
                        if float(self.env.water_amount(tx, ay)) > 0.025:
                            clear=False; break
                    except Exception:
                        pass
            if clear:
                candidates.append(ty)
        if not candidates:
            return None
        ty=min(candidates,key=lambda r:abs(int(r)-int(preferred_row)))
        return (tx+0.5)*TILE_SIZE, float(ty)*TILE_SIZE

    def _underground_air_pos(self, tx, preferred_row, body_h=18.0, search_radius=5):
        """Find an open cave cell for a flying creature, never inside rock/water."""
        tx=max(1,min(self.world.width_tiles-2,int(tx)))
        preferred_row=max(1,min(self.world.height_tiles-2,int(preferred_row)))
        for radius in range(max(1,int(search_radius))+1):
            for ty in ((preferred_row,) if radius==0 else (preferred_row-radius,preferred_row+radius)):
                if not (1<=ty<self.world.height_tiles-1):
                    continue
                try:
                    if self.world.get_tile(tx,ty)!=AIR or self.world.get_tile(tx,ty-1)!=AIR:
                        continue
                    if self.env is not None and float(self.env.water_amount(tx,ty))>0.025:
                        continue
                except Exception:
                    continue
                center_y=(float(ty)+0.5)*TILE_SIZE
                return (tx+0.5)*TILE_SIZE, center_y+float(body_h)*0.5
        return None

    def _special_custom_position(self, habitat, cfg, slot, total, fallback_y):
        """Return an authored habitat position and underground flag.

        FIX73 special habitats are real map geometry. Missing metadata does not
        silently relocate a cave/magma/sky creature to the plains.
        """
        habitat = str(habitat or "")
        locomotion = str(cfg.get("locomotion", "ground") or "ground")
        # Named surface subregions (for example savanna) provide real spawn
        # columns without pretending to be a whole climate-width biome.
        fauna_regions = self.metadata.get("fauna_regions", {})
        region = fauna_regions.get(habitat) if isinstance(fauna_regions, dict) else None
        if isinstance(region, dict):
            points = region.get("spawn_columns", ()) or ()
            if not points:
                return None
            point = points[int(slot) % len(points)]
            try:
                tx, floor = int(point[0]), int(point[1])
            except Exception:
                return None
            x, y = (tx + 0.5) * TILE_SIZE, float(floor) * TILE_SIZE
            if locomotion in ("bird", "insect_low", "fly_low"):
                y -= 26.0 + 7.0 * (int(slot) % 3)
            return float(x), float(y), False
        if habitat == "sky_island":
            rows = self.metadata.get("sky_islands", ())
            if not isinstance(rows, list) or not rows:
                return None
            variant = str(cfg.get("habitat_variant", "") or "")
            preferred = [
                row for row in rows
                if isinstance(row, dict) and str(row.get("source_biome", "")) == variant
            ]
            choices = preferred or [row for row in rows if isinstance(row, dict)]
            if not choices:
                return None
            island = choices[int(slot) % len(choices)]
            points = island.get("spawn_columns", ()) or ()
            if not points:
                return None
            point = points[(int(slot) // max(1, len(choices))) % len(points)]
            try:
                tx, floor = int(point[0]), int(point[1])
            except Exception:
                return None
            x = (tx + 0.5) * TILE_SIZE
            y = float(floor) * TILE_SIZE
            if locomotion in ("bird", "insect_low", "fly_low", "cave_fly", "ghost_fly"):
                y -= 24.0 + 8.0 * (int(slot) % 3)
            return float(x), float(y), False

        if habitat == "cave":
            rows = self.metadata.get("underground_caverns", ())
            if not isinstance(rows, list) or not rows:
                return None
            row = rows[int(slot) % len(rows)]
            try:
                cx, cy, rx, ry = [int(value) for value in row[:4]]
            except Exception:
                return None
            tx = max(1, min(self.world.width_tiles - 2, cx + ((int(slot) % 3) - 1) * max(1, rx // 3)))
            if locomotion in ("bird", "insect_low", "fly_low", "cave_fly", "ghost_fly"):
                pos = self._underground_air_pos(tx, cy, body_h=float(cfg.get("h", 18.0)), search_radius=max(4, ry + 2))
            else:
                pos = self._underground_floor_pos(
                    tx, cy + ry + 1, body_h=float(cfg.get("h", 24.0)),
                    y_min=max(2, cy - ry - 2), y_max=min(self.world.height_tiles - 1, cy + ry + 5),
                )
            return None if pos is None else (float(pos[0]), float(pos[1]), True)

        if habitat == "magma_hell":
            zones = self.metadata.get("underground_biomes", ())
            zone = next((
                row for row in zones if isinstance(row, dict)
                and str(row.get("type", "")) == "magma_hell"
            ), None) if isinstance(zones, list) else None
            ledges = zone.get("safe_ledges", ()) if isinstance(zone, dict) else ()
            if not ledges:
                return None
            ledge = ledges[int(slot) % len(ledges)]
            try:
                tx, floor = int(ledge["x"]), int(ledge["floor"])
            except Exception:
                return None
            if not self._solid_tile(tx, floor):
                return None
            # The authorer cleared lava from this shelf; verify imported state
            # too so a malformed/old map cannot spawn a creature in molten rock.
            try:
                if float(self.env.state.lava_amount(tx, floor - 1)) > 0.025:
                    return None
            except Exception:
                pass
            return (tx + 0.5) * TILE_SIZE, float(floor) * TILE_SIZE, True
        return None

    def _append_custom_creatures(self, scene, out, serial_start, fallback_y):
        """Spawn every asset-editor-created creature at least once.

        FIX32 deliberately separates *being authored* from needing a Python
        code change. The custom profile written by the asset editor chooses a
        habitat and the ordinary biome / water placement rules are reused.

        FIX71 assigns a stable fractional slot inside each habitat.  The old
        midpoint-only rule stacked every custom creature on one coordinate;
        with the 50-creature expansion that made whole groups overlap before
        their first AI tick.
        """
        serial=int(serial_start)
        if bool(self.metadata.get("underground_only", False)):
            return serial
        already={str(getattr(c,"species","")) for c in out}
        span_by_biome={str(b):(int(a),int(z)) for a,z,b in self.spans}
        habitat_members={}
        for species,cfg in sorted(CREATURE_ARCHETYPES.items()):
            if (not bool(cfg.get("_custom",False)) or species in already
                    or bool(cfg.get("authored_only",False))):
                continue
            habitat=str(cfg.get("habitat","plains") or "plains")
            try:spawn_count=max(1,min(4,int(cfg.get("spawn_count",1))))
            except Exception:spawn_count=1
            for repeat in range(spawn_count):
                habitat_members.setdefault(habitat,[]).append((str(species),repeat))
        habitat_slots={
            (species,repeat):(index,len(members))
            for _habitat,members in habitat_members.items()
            for index,(species,repeat) in enumerate(members)
        }
        for species,cfg in sorted(CREATURE_ARCHETYPES.items()):
            if (not bool(cfg.get("_custom",False)) or species in already
                    or bool(cfg.get("authored_only",False))):
                continue
            habitat=str(cfg.get("habitat","plains") or "plains")
            try:spawn_count=max(1,min(4,int(cfg.get("spawn_count",1))))
            except Exception:spawn_count=1
            for repeat in range(spawn_count):
                slot,total=habitat_slots.get((str(species),repeat),(repeat,spawn_count))
                locomotion=str(cfg.get("locomotion","ground") or "ground")
                special = self._special_custom_position(habitat,cfg,slot,total,fallback_y)
                underground = False
                if special is not None:
                    x,y,underground=special
                elif habitat in ("sky_island","cave","magma_hell","savanna"):
                    continue
                else:
                    start,end=span_by_biome.get(habitat, span_by_biome.get(BIOME_PLAINS,(2,self.world.width_tiles-2)))
                    width=max(4,end-start)
                    fraction=float(slot+1)/float(max(2,total+1))
                    tx=max(start+1,min(end-2,start+int(round(width*fraction))))
                    if locomotion in ("fish","swim","waterbird"):
                        found=self._find_water_tx(tx,start,end,min_cells=(2 if locomotion in ("fish","swim") else 1))
                        if found is None:
                            continue
                        tx=found
                    elif locomotion=="bird":
                        found=self._find_dry_tx(tx,start,end)
                        if found is not None: tx=found
                    if locomotion in ("fish","swim"):
                        x,y=self._aquatic_pos(tx,fallback_y,0.36+0.12*(repeat%3),body_h=float(cfg["h"]))
                    elif locomotion=="waterbird":
                        x,y=self._water_surface_pos(tx,fallback_y,lift_px=-2.0)
                    elif locomotion in ("bird","insect_low"):
                        x,y=self._low_air_pos(tx,fallback_y,lift_px=26.0+8.0*(repeat%3))
                    else:
                        x,y=self._surface_pos(tx,fallback_y)
                serial+=1
                c=Creature(
                    entity_id="creature_%s_%02d"%(species,serial), x=float(x), y=float(y),
                    species=str(species), name=str(cfg.get("name",species)),
                    hp=float(cfg.get("hp",80)), max_hp=float(cfg.get("hp",80)),
                    attack_damage=float(cfg.get("attack",8)), speed=float(cfg.get("speed",48)),
                    width_px=float(cfg.get("w",36)), height_px=float(cfg.get("h",32)),
                    hostile=bool(cfg.get("hostile",False)), home_x=float(x),
                    patrol_radius=TILE_SIZE*(2.2+0.35*(repeat%3)), biome=habitat,
                    loot_table=tuple(cfg.get("loot",())), asset_id="creature.%s"%species,
                    locomotion=locomotion, home_y=float(y),
                    element_type=str(cfg.get("element","neutral")),
                    boss=bool(cfg.get("boss",False)),
                    boss_title=str(cfg.get("boss_title",cfg.get("name","")) or ""),
                )
                c.underground=bool(underground)
                scene.add_entity(c); out.append(c)
            already.add(species)
        return serial

    def _authored_aquatic_position(self, tx, bounds, index, body_h):
        """Find water inside one underground authored room, not surface water."""
        try:x0,y0,x1,y1=[int(v) for v in bounds[:4]]
        except Exception:return None
        x0=max(1,x0);x1=min(self.world.width_tiles-2,x1)
        y0=max(1,y0);y1=min(self.world.height_tiles-2,y1)
        width=max(1,x1-x0+1)
        wanted=max(x0,min(x1,int(tx)))
        for radius in range(width):
            for cx in ((wanted,) if radius==0 else (wanted-radius,wanted+radius)):
                if not x0<=cx<=x1:continue
                rows=[ty for ty in range(y0,y1+1) if float(self.env.water_amount(cx,ty))>0.025]
                if not rows:continue
                top=min(rows);bottom=max(rows)
                fraction=0.30+0.16*(int(index)%3)
                center=(top+fraction*max(1,bottom-top+1))*TILE_SIZE
                return (cx+0.5)*TILE_SIZE,center+float(body_h)*0.5
        return None

    def _append_underground_creatures(self, scene, out, serial_start):
        """Populate FIX27+ caverns/dungeons without altering surface ecology."""
        dungeons=self.metadata.get("underground_dungeons",())
        caverns=self.metadata.get("underground_caverns",())
        if not isinstance(dungeons,list) and not isinstance(caverns,list):
            return int(serial_start)
        serial=int(serial_start)

        def spawn(species, x, y, patrol_tiles=2.2):
            nonlocal serial
            cfg=CREATURE_ARCHETYPES[species]; serial+=1
            c=Creature(
                entity_id="creature_%s_%02d"%(species,serial), x=float(x), y=float(y),
                species=species,name=cfg["name"],hp=float(cfg["hp"]),max_hp=float(cfg["hp"]),
                attack_damage=float(cfg["attack"]),speed=float(cfg["speed"]),
                width_px=float(cfg["w"]),height_px=float(cfg["h"]),hostile=bool(cfg["hostile"]),
                home_x=float(x),patrol_radius=TILE_SIZE*float(patrol_tiles),
                biome=str(self.biome_at_world_x(x)),loot_table=tuple(cfg["loot"]),
                asset_id="creature.%s"%species,locomotion=str(cfg.get("locomotion","ground")),home_y=float(y),
                element_type=str(cfg.get("element","neutral")),
                boss=bool(cfg.get("boss",False)),boss_title=str(cfg.get("boss_title",cfg.get("name","")) or ""),
            )
            c.underground=True
            scene.add_entity(c);out.append(c)
            return c

        # FIX30: dungeons are intentionally busier. Six separated actors are
        # attempted per dungeon, rotating the elemental variants so each room
        # does not feel identical. Chunk streaming still caps per-frame AI cost.
        dungeon_cycle=(
            ("zombie","giant_spider_monster","dungeon_bandit_mage","toilet_man","plane_monster","ghost"),
            ("lightning_zombie","cave_snorble","speaker_man","cave_spider","creeper","plane_monster"),
            ("fire_zombie","giant_spider_monster","cave_snorble","toilet_man","speaker_man","plane_monster"),
        )
        for di,row in enumerate(dungeons if isinstance(dungeons,list) else ()):
            try:x0,y0,x1,y1=[int(v) for v in row.get("bounds",())]
            except Exception:continue
            width=max(8,x1-x0)
            species_row=dungeon_cycle[di%len(dungeon_cycle)]
            fractions=(0.14,0.29,0.43,0.58,0.72,0.86)
            for species,frac in zip(species_row,fractions):
                tx=max(x0+2,min(x1-2,x0+int(round(width*frac))))
                cfg=CREATURE_ARCHETYPES[species]
                if str(cfg.get("locomotion","ground")) in ("cave_fly","ghost_fly"):
                    pos=self._underground_air_pos(tx,(y0+y1)//2,body_h=float(cfg["h"]),search_radius=max(4,(y1-y0)//2+2))
                else:
                    pos=self._underground_floor_pos(tx,y1-1,body_h=float(cfg["h"]),y_min=y0,y_max=min(self.world.height_tiles-1,y1+2))
                if pos is not None:spawn(species,pos[0],pos[1],2.1)

        # FIX31: one giant boss occupies the deepest authored dungeon.  It is
        # intentionally singular so the global boss bar always has one owner.
        valid_dungeons=[row for row in (dungeons if isinstance(dungeons,list) else ()) if isinstance(row,dict) and row.get("bounds")]
        if valid_dungeons and bool(self.metadata.get("spawn_abyss_boss", True)):
            deepest=max(valid_dungeons,key=lambda row:int(row.get("bounds",[0,0,0,0])[3]))
            try:
                bx0,by0,bx1,by1=[int(v) for v in deepest.get("bounds",())]
                tx=(bx0+bx1)//2
                cfg=CREATURE_ARCHETYPES["abyss_colossus"]
                pos=self._underground_floor_pos(tx,by1-1,body_h=float(cfg["h"]),y_min=by0,y_max=min(self.world.height_tiles-1,by1+2))
                if pos is not None:spawn("abyss_colossus",pos[0],pos[1],3.0)
            except Exception:
                pass

        # Natural caverns now carry 3-4 creatures each instead of roughly one.
        # X offsets are spread across the chamber to avoid stacked spawn piles.
        ground_cycle=("giant_worm","giant_spider_monster","cave_snorble","cave_spider","blue_poop","creeper","zombie","fire_zombie","lightning_zombie")
        air_cycle=("cave_bat","plane_monster","ghost","giant_bago_bird","plane_monster","cave_bat")
        for i,row in enumerate(caverns if isinstance(caverns,list) else ()):
            try:cx,cy,rx,ry=[int(v) for v in row[:4]]
            except Exception:continue
            # Two floor creatures per cavern.
            for j,sgn in enumerate((-1,1)):
                species=ground_cycle[(i*2+j)%len(ground_cycle)]
                tx=max(1,min(self.world.width_tiles-2,cx+sgn*max(2,rx//3)))
                cfg=CREATURE_ARCHETYPES[species]
                pos=self._underground_floor_pos(tx,cy+ry+1,body_h=float(cfg["h"]),y_min=max(18,cy-ry-2),y_max=min(self.world.height_tiles-1,cy+ry+4))
                if pos is not None:spawn(species,pos[0],pos[1],max(1.6,min(3.0,rx*0.38)))
            # One guaranteed airborne/spectral inhabitant. Every third cavern
            # gets a second flyer, producing a denser but still readable cave.
            flyers=[air_cycle[i%len(air_cycle)]]
            if i%3==0:flyers.append(air_cycle[(i+1)%len(air_cycle)])
            for j,species in enumerate(flyers):
                tx=max(1,min(self.world.width_tiles-2,cx+(j*2-1)*max(1,rx//4)))
                cfg=CREATURE_ARCHETYPES[species]
                pos=self._underground_air_pos(tx,cy-(j%2),body_h=float(cfg["h"]),search_radius=max(3,ry+2))
                if pos is not None:spawn(species,pos[0],pos[1],max(1.7,min(3.6,rx*0.45)))
        return serial

    def _append_authored_creatures(self,scene,out,serial):
        for row in self.metadata.get("creature_spawns",()) if isinstance(self.metadata.get("creature_spawns",()),list) else ():
            species=str(row.get("species",""));cfg=CREATURE_ARCHETYPES.get(species)
            if not cfg:continue
            for index in range(max(1,int(row.get("count",1)))):
                tx=int(row.get("x",10))+index*int(row.get("spacing",3));floor=int(row.get("floor",20))
                try:
                    from systems.creature_attributes import effective_locomotion, normalize_habitat_mode
                    habitat_mode=normalize_habitat_mode(row.get('habitat_mode','default'))
                    locomotion=effective_locomotion(cfg,row)
                except Exception:
                    habitat_mode='default';locomotion=str(cfg.get("locomotion","ground") or "ground")
                pos=None
                water_bounds=row.get("water_bounds",())
                fixed82=str(row.get("id", "")).startswith(("fix82_","fix83_"))
                # Large authored chambers may taper between the group's first
                # and last X. Search a tiny deterministic local fan so every
                # requested member receives valid air/floor without stacking
                # all actors on the same coordinate.
                for offset in (0,1,-1,2,-2,3,-3,4,-4,5,-5,6,-6):
                    candidate=max(1,min(self.world.width_tiles-2,tx+offset))
                    if habitat_mode=='burrow':
                        # MapEditor burrow placement targets the tile directly
                        # above ``floor``.  It remains inactive until ToolSystem
                        # reports that exact cell fully excavated.
                        pos=((candidate+.5)*TILE_SIZE,float(floor)*TILE_SIZE)
                    elif fixed82:
                        # Feet anchored on the actual layered floor; ceiling
                        # mouths are authored upside-down and never use gravity.
                        x=(candidate+.5)*TILE_SIZE
                        if locomotion=='ceiling':
                            ceiling=int(row['ceiling'])
                            if not tile_def(self.world.get_tile(candidate,ceiling)).solid: continue
                            y=(ceiling+1)*TILE_SIZE+float(cfg['h'])+.05
                        elif locomotion=='sky_fly':
                            y=float(floor)*TILE_SIZE-float(row.get("air_offset",65.))
                        else:
                            top=self.world.solid_top_y(candidate,floor)
                            if top is None: continue
                            y=float(top)
                        bbox=(x-float(cfg['w'])*.5+.1,y-float(cfg['h'])+.1,
                              x+float(cfg['w'])*.5-.1,y-.1)
                        blocked=False
                        for _tx,_ty,_tid,rect in self.world.solid_cells_in_rect(bbox,padding=0):
                            if bbox[0]<rect[2] and bbox[2]>rect[0] and bbox[1]<rect[3] and bbox[3]>rect[1]:
                                blocked=True;break
                        if blocked:continue
                        pos=(x,y)
                    elif locomotion in ("fish","swim") and water_bounds:
                        pos=self._authored_aquatic_position(candidate,water_bounds,index,float(cfg["h"]))
                    elif locomotion in ("cave_fly","ghost_fly","fly_low"):
                        pos=self._underground_air_pos(candidate,max(2,floor-4),body_h=float(cfg["h"]),search_radius=11)
                    else:
                        pos=self._underground_floor_pos(candidate,floor,body_h=float(cfg["h"]),y_min=max(2,floor-12),y_max=min(self.world.height_tiles-1,floor+12))
                    if pos is not None:break
                if pos is None:continue
                serial+=1;x,y=pos;c=Creature(entity_id=(str(row["id"])+(str(index) if index else "")) if fixed82 else "authored_%s_%02d"%(species,serial),x=x,y=y,species=species,name=cfg["name"],hp=float(cfg["hp"]),max_hp=float(cfg["hp"]),attack_damage=float(cfg["attack"]),speed=float(cfg["speed"]),width_px=float(cfg["w"]),height_px=float(cfg["h"]),hostile=bool(cfg.get("hostile",True)),home_x=x,home_y=y,patrol_radius=TILE_SIZE*float(row.get("patrol_tiles",2.8)),biome="sky_island" if row.get("sky") else str(self.biome_at_world_x(x)),loot_table=tuple(cfg["loot"]),asset_id="creature.%s"%species,locomotion=locomotion,element_type=str(cfg.get("element","neutral")),boss=bool(cfg.get("boss",False)),boss_title=str(cfg.get("boss_title",cfg.get("name","")) or ""))
                c.poison_immune=bool(cfg.get('poison_immune',False))
                try:
                    from systems.editor_objects import apply_editor_ai_overrides, HIDING_TILES
                    apply_editor_ai_overrides(c,row)
                    if habitat_mode=='burrow':
                        hidden_ty=max(0,int(floor)-1)
                        c.editor_hidden_until_mined=True
                        c.editor_hidden_tile=(int(tx),hidden_ty)
                        c.editor_revealed=(int(self.world.get_tile(int(tx),hidden_ty)) not in HIDING_TILES)
                        c.active=bool(c.editor_revealed)
                except Exception:
                    pass
                c.underground=not bool(row.get('sky'))
                c.respawn_region='sky_island' if row.get('sky') else 'underground'
                if fixed82:c.respawn_delay_seconds=float(cfg.get('respawn_seconds',100))
                c.grounded=str(getattr(c,'locomotion',locomotion)) not in ('ceiling','sky_fly','cave_fly','ghost_fly','fish','swim','bird')
                scene.add_entity(c);out.append(c)
        return serial

    def _spawn_fractions(self, biome):
        if biome == BIOME_OCEAN:
            # Keep fauna near the littoral / coast side, not deep open water.
            return (0.12, 0.25, 0.38, 0.52, 0.66)
        if biome == BIOME_LAKE:
            # Lake-edge fauna hug shore or reed margins.
            return (0.10, 0.22, 0.36, 0.52, 0.68, 0.82, 0.92)
        return (0.16, 0.38, 0.62, 0.84)

    def build_creatures(self, scene, fallback_y, avoid_world_x=None):
        """Create a bounded deterministic population across every biome.

        Only creatures in active chunks are simulated, so a few actors per
        biome do not create a whole-world per-frame cost.
        """
        out = []
        serial = 0
        avoid_world_x = None if avoid_world_x is None else float(avoid_world_x)
        surface_spans = () if bool(self.metadata.get("underground_only", False)) else self.spans
        for start, end, biome in surface_spans:
            width = max(1, int(end) - int(start))
            rotation = BIOME_CREATURE_ROTATION.get(biome, ("slime",))
            base_fractions = self._spawn_fractions(biome)
            target_count = max(
                len(rotation), len(base_fractions),
                min(16, int(math.ceil(float(width) / 18.0))),
            )
            fractions = tuple((i + 1.0) / (target_count + 1.0) for i in range(target_count))
            for i, frac in enumerate(fractions):
                tx = int(start) + max(1, min(width - 2, int(round(width * frac))))
                species = rotation[i % len(rotation)]
                cfg = CREATURE_ARCHETYPES[species]
                locomotion = str(cfg.get("locomotion", "ground"))
                # Habitat-valid initialization. Fish / aquatic swimmers are never
                # created unless a real water column exists. Birds start perched
                # on dry terrain; only their AI may later take off. Low insects
                # start above the nearest local surface, never inside the water.
                if locomotion in ("fish", "swim", "waterbird"):
                    min_cells = 2 if locomotion in ("fish", "swim") else 1
                    water_tx = self._find_water_tx(tx, int(start), int(end), min_cells=min_cells)
                    if water_tx is None:
                        continue
                    tx = water_tx
                elif locomotion == "bird":
                    dry_tx = self._find_dry_tx(tx, int(start), int(end))
                    if dry_tx is not None:
                        tx = dry_tx

                if locomotion in ("fish", "swim"):
                    x, y = self._aquatic_pos(
                        tx, fallback_y, 0.30 + 0.12 * (i % 4), body_h=float(cfg["h"])
                    )
                elif locomotion == "waterbird":
                    # Feet anchor sits just below the water line so the standing
                    # pose reads as paddling with both feet submerged.
                    x, y = self._water_surface_pos(tx, fallback_y, lift_px=-2.0)
                elif locomotion == "bird":
                    # Prefer a real dry perch. In a fully open-water biome such
                    # as the lake, a kingfisher may initialise already airborne;
                    # the same AI still uses standing pose whenever it later has
                    # a valid ground perch.
                    if self._water_rows(tx):
                        x, y = self._low_air_pos(tx, fallback_y, lift_px=30.0 + 6.0 * (i % 2))
                    else:
                        x, y = self._surface_pos(tx, fallback_y)
                elif locomotion == "insect_low":
                    x, y = self._low_air_pos(tx, fallback_y, lift_px=22.0 + 8.0 * (i % 3))
                else:
                    x, y = self._surface_pos(tx, fallback_y)
                if avoid_world_x is not None and abs(x - avoid_world_x) < TILE_SIZE * 4.0:
                    tx2 = min(int(end) - 2, tx + 5)
                    if locomotion in ("fish", "swim", "waterbird"):
                        found = self._find_water_tx(tx2, int(start), int(end), min_cells=(2 if locomotion in ("fish", "swim") else 1))
                        if found is not None:
                            tx2 = found
                    elif locomotion == "bird":
                        found = self._find_dry_tx(tx2, int(start), int(end))
                        if found is not None:
                            tx2 = found
                    tx = tx2
                    if locomotion in ("fish", "swim"):
                        x, y = self._aquatic_pos(tx, fallback_y, 0.48, body_h=float(cfg["h"]))
                    elif locomotion == "waterbird":
                        x, y = self._water_surface_pos(tx, fallback_y, lift_px=-2.0)
                    elif locomotion == "bird":
                        if self._water_rows(tx):
                            x, y = self._low_air_pos(tx, fallback_y, lift_px=32.0)
                        else:
                            x, y = self._surface_pos(tx, fallback_y)
                    elif locomotion == "insect_low":
                        x, y = self._low_air_pos(tx, fallback_y, lift_px=30.0)
                    else:
                        x, y = self._surface_pos(tx, fallback_y)
                serial += 1
                c = Creature(
                    entity_id="creature_%s_%02d" % (species, serial),
                    x=x, y=y,
                    species=species,
                    name=cfg["name"],
                    hp=float(cfg["hp"]), max_hp=float(cfg["hp"]),
                    attack_damage=float(cfg["attack"]), speed=float(cfg["speed"]),
                    width_px=float(cfg["w"]), height_px=float(cfg["h"]),
                    hostile=bool(cfg["hostile"]), home_x=x,
                    patrol_radius=TILE_SIZE * (2.2 if biome == BIOME_VILLAGE else 3.2),
                    biome=str(biome), loot_table=tuple(cfg["loot"]),
                    asset_id="creature.%s" % species,
                    locomotion=locomotion, home_y=y,
                    element_type=str(cfg.get("element","neutral")),
                )
                scene.add_entity(c)
                out.append(c)
        serial=self._append_custom_creatures(scene,out,serial,fallback_y)
        if str(self.metadata.get("underground_population_mode","") or "") != "authored_only":
            serial=self._append_underground_creatures(scene,out,serial)
        serial=self._append_authored_creatures(scene,out,serial)
        return out
