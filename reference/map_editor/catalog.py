# -*- coding: utf-8 -*-
"""Extensible map-editor palette registry."""

from dataclasses import dataclass
import json
import os


@dataclass(frozen=True)
class EditorItem:
    item_id: str
    label: str
    category: str
    layer: str
    color: str
    value: object
    symbol: str = ""


ITEMS = []
ITEM_BY_ID = {}


def register(item):
    if item.item_id in ITEM_BY_ID:
        return ITEM_BY_ID[item.item_id]
    ITEMS.append(item)
    ITEM_BY_ID[item.item_id] = item
    return item


# ------------------------------------------------------------------
# Terrain
# ------------------------------------------------------------------
register(EditorItem("terrain_grass", "草皮土壤", "地形", "terrain", "#6C5238", "grass_dirt", "綠土"))
register(EditorItem("terrain_dirt", "一般土壤", "地形", "terrain", "#6C5238", "dirt", "土"))
register(EditorItem("terrain_wood", "木材圖塊", "地形", "terrain", "#87603A", "wood", "木塊"))
register(EditorItem("terrain_ladder", "梯子", "地形", "terrain", "#A9773F", "ladder", "梯"))
register(EditorItem("terrain_ice", "冰塊", "地形", "terrain", "#7EC8ED", "ice", "冰"))
register(EditorItem("terrain_ash", "灰燼土壤", "地形", "terrain", "#595959", "ash", "灰"))
register(EditorItem("terrain_stone", "一般石塊", "地形", "terrain", "#676B74", "stone", "石"))
register(EditorItem("terrain_igneous", "火成岩", "地形", "terrain", "#50494A", "igneous_rock", "火岩"))
register(EditorItem("terrain_copper", "銅礦", "地形", "terrain", "#B76E45", "copper_ore", "銅"))
register(EditorItem("terrain_iron", "鐵礦", "地形", "terrain", "#9A8174", "iron_ore", "鐵"))
register(EditorItem("terrain_gold", "金礦", "地形", "terrain", "#D5A92D", "gold_ore", "金"))
register(EditorItem("terrain_marble", "大理石塊", "地形", "terrain", "#E5E2DE", "marble", "理"))
register(EditorItem("terrain_limestone", "灰岩石塊", "地形", "terrain", "#B4AA88", "limestone", "灰岩"))
register(EditorItem("terrain_mud", "淤泥", "地形", "terrain", "#49372D", "mud", "泥"))
register(EditorItem("terrain_swamp_soil", "沼澤土壤", "地形", "terrain", "#514838", "swamp_soil", "沼土"))
register(EditorItem("terrain_sand", "沙漠沙地", "地形", "terrain", "#E2C675", "sand", "沙"))
register(EditorItem("terrain_sea_sand", "海沙", "地形", "terrain", "#D8C28A", "sea_sand", "海沙"))
register(EditorItem("terrain_jungle", "雨林土壤", "地形", "terrain", "#2F7A38", "jungle_soil", "雨土"))
register(EditorItem("terrain_rainforest_root", "雨林巨木・根基", "巨木", "terrain", "#49301E", "rainforest_root", "根"))
register(EditorItem("terrain_rainforest_trunk", "雨林巨木・樹幹", "巨木", "terrain", "#54351F", "rainforest_trunk", "幹"))
register(EditorItem("terrain_rainforest_branch", "雨林巨木・可站枝條", "巨木", "terrain", "#654225", "rainforest_branch", "枝"))
register(EditorItem("terrain_rainforest_vine", "雨林巨木・攀爬藤", "巨木", "terrain", "#2E7136", "rainforest_vine", "藤"))
register(EditorItem("terrain_rainforest_canopy", "雨林巨木・樹冠", "巨木", "terrain", "#1F642F", "rainforest_canopy", "冠"))
register(EditorItem("terrain_snow", "雪地土壤", "地形", "terrain", "#E7EEF2", "snow_dirt", "雪土"))
register(EditorItem("terrain_village_path", "村莊道路", "地形", "terrain", "#A58B64", "village_path", "路"))
register(EditorItem("terrain_honeycomb", "蜂巢圖塊", "地形", "terrain", "#F0BC3E", "honeycomb", "巢"))

# ------------------------------------------------------------------
# Shared fractional solid geometry
# ------------------------------------------------------------------
register(EditorItem("layer_low", "低位 1/3", "分層", "ground_layer", "#8E7A62", 1, "⅓"))
register(EditorItem("layer_mid", "中位 2/3", "分層", "ground_layer", "#A99170", 3, "⅔"))
register(EditorItem("layer_full", "完整 3/3", "分層", "ground_layer", "#C1AD89", 7, "1"))

# ------------------------------------------------------------------
# Water / hazards
# ------------------------------------------------------------------
register(EditorItem("water", "水體", "液體", "water", "#2E94F0", 1.0, "水"))
register(EditorItem("swamp", "沼澤", "液體", "hazard", "#476A3B", "swamp", "沼"))
register(EditorItem("lava", "岩漿", "液體", "lava", "#F04B16", 1.0, "岩漿"))
register(EditorItem("honey", "蜂蜜", "液體", "honey", "#D99A18", 1.0, "蜜"))

# ------------------------------------------------------------------
# Fire
# ------------------------------------------------------------------
register(EditorItem("fire_small", "小火焰", "火焰", "fire", "#F47C20", 1, "火1"))
register(EditorItem("fire_medium", "中火焰", "火焰", "fire", "#F05423", 2, "火2"))
register(EditorItem("fire_large", "大火焰", "火焰", "fire", "#D93A21", 3, "火3"))

# ------------------------------------------------------------------
# Grass
# ------------------------------------------------------------------
register(EditorItem("grass_low", "低草", "草", "vegetation", "#5C8D45", ("grass", 1), "草1"))
register(EditorItem("grass_mid", "中草", "草", "vegetation", "#4F9B42", ("grass", 2), "草2"))
register(EditorItem("grass_high", "高草", "草", "vegetation", "#3E8538", ("grass", 3), "草3"))
register(EditorItem("plant_cactus", "仙人掌", "生態", "vegetation", "#4E8D45", ("cactus", 2), "仙"))
register(EditorItem("plant_reed", "蘆葦", "生態", "vegetation", "#6D8F45", ("reed", 2), "葦"))
register(EditorItem("plant_fern", "雨林蕨", "生態", "vegetation", "#2F8442", ("fern", 2), "蕨"))
register(EditorItem("plant_jungle_tree", "雨林大樹", "生態", "vegetation", "#246B32", ("jungle_tree", 3), "雨木"))
register(EditorItem("plant_pine", "雪松", "生態", "vegetation", "#2F6651", ("pine", 3), "松"))

# ------------------------------------------------------------------
# Wood / tree
# ------------------------------------------------------------------
register(EditorItem("wood_low", "低木", "木", "vegetation", "#7D5834", ("tree", 1), "木1"))
register(EditorItem("wood_mid", "中木", "木", "vegetation", "#6C492D", ("tree", 2), "木2"))
register(EditorItem("wood_high", "高木", "木", "vegetation", "#5A3B25", ("tree", 3), "木3"))

# ------------------------------------------------------------------
# Decorations
# ------------------------------------------------------------------
register(EditorItem("decor_stone_small", "裝飾石頭・小", "裝飾", "decoration", "#7C8189", ("stone", 1), "飾1"))
register(EditorItem("decor_stone_mid", "裝飾石頭・中", "裝飾", "decoration", "#747981", ("stone", 2), "飾2"))
register(EditorItem("decor_stone_large", "裝飾石頭・大", "裝飾", "decoration", "#686D74", ("stone", 3), "飾3"))
register(EditorItem("decor_house", "村莊房屋", "裝飾", "decoration", "#8F6140", ("house", 2), "屋"))
register(EditorItem("decor_fence", "木柵欄", "裝飾", "decoration", "#7A5632", ("fence", 2), "欄"))
register(EditorItem("decor_lamp", "村莊燈", "裝飾", "decoration", "#D9B04B", ("lamp", 1), "燈"))
register(EditorItem("decor_glow_moss", "發光苔蘚", "裝飾", "decoration", "#58E6B5", ("glow_moss", 2), "苔光"))
register(EditorItem("decor_crystal", "巨大水晶", "裝飾", "decoration", "#84C9FF", ("crystal", 3), "晶"))
register(EditorItem("decor_portal", "洞穴入口／門", "裝飾", "decoration", "#A77CFF", ("portal", 2), "門"))
register(EditorItem("decor_boss_gate", "Boss 地牢門", "裝飾", "decoration", "#6F2038", ("boss_gate", 3), "王門"))
register(EditorItem("decor_root_arch", "苔蘚根系拱門", "裝飾", "decoration", "#397A57", ("root_arch", 2), "根拱"))
register(EditorItem("decor_mine_support", "礦坑木支架", "裝飾", "decoration", "#76502F", ("mine_support", 2), "礦架"))
register(EditorItem("decor_crystal_lantern", "水晶燈", "裝飾", "decoration", "#84DFFF", ("crystal_lantern", 1), "晶燈"))
register(EditorItem("decor_castle_banner", "地下城旗幟", "裝飾", "decoration", "#7A263B", ("castle_banner", 2), "城旗"))
register(EditorItem("decor_stone_throne", "石製王座", "裝飾", "decoration", "#55545E", ("stone_throne", 3), "王座"))
register(EditorItem("decor_rune_obelisk", "深淵符文碑", "裝飾", "decoration", "#A13B78", ("rune_obelisk", 2), "符碑"))
register(EditorItem("decor_abyss_altar", "深淵祭壇", "裝飾", "decoration", "#772D65", ("abyss_altar", 3), "祭壇"))
register(EditorItem("decor_magma_totem", "熔岩圖騰", "裝飾", "decoration", "#B53C20", ("magma_totem", 2), "火騰"))

# FIX62 authored underground landmark kit.  These entries remain visual-only;
# terrain and trap layers continue to own collision and damage.
register(EditorItem("decor_giant_mushroom", "巨型發光菇", "裝飾", "decoration", "#66F0C1", ("giant_mushroom", 3), "光菇"))
register(EditorItem("decor_hanging_glow_vine", "垂掛發光藤", "裝飾", "decoration", "#47C998", ("hanging_glow_vine", 3), "光藤"))
register(EditorItem("decor_spore_pod", "孢子囊", "裝飾", "decoration", "#8CE9B8", ("spore_pod", 2), "孢囊"))
register(EditorItem("decor_moss_stalactite", "苔蘚鐘乳石", "裝飾", "decoration", "#397E67", ("moss_stalactite", 3), "苔鐘"))
register(EditorItem("decor_root_cluster", "盤根群", "裝飾", "decoration", "#426E4D", ("root_cluster", 3), "盤根"))
register(EditorItem("decor_glow_pool", "螢光菌池", "裝飾", "decoration", "#41DCAA", ("glow_pool", 3), "螢池"))
register(EditorItem("decor_ceiling_crystal", "倒垂巨晶", "裝飾", "decoration", "#75CFFF", ("ceiling_crystal", 3), "倒晶"))
register(EditorItem("decor_crystal_arch", "水晶拱門", "裝飾", "decoration", "#88DDFF", ("crystal_arch", 3), "晶拱"))
register(EditorItem("decor_crystal_vein", "發光晶脈", "裝飾", "decoration", "#4C9BE8", ("crystal_vein", 2), "晶脈"))
register(EditorItem("decor_shattered_crystal", "碎裂晶簇", "裝飾", "decoration", "#5DAAEF", ("shattered_crystal", 2), "碎晶"))
register(EditorItem("decor_mine_cart", "廢棄礦車", "裝飾", "decoration", "#7A6655", ("mine_cart", 2), "礦車"))
register(EditorItem("decor_hoist_chain", "礦井吊鏈", "裝飾", "decoration", "#68717A", ("hoist_chain", 3), "吊鏈"))
register(EditorItem("decor_crystal_shrine", "水晶祭壇", "裝飾", "decoration", "#9BE8FF", ("crystal_shrine", 3), "晶壇"))
register(EditorItem("decor_obsidian_spire", "黑曜石尖塔", "裝飾", "decoration", "#34272E", ("obsidian_spire", 3), "黑塔"))
register(EditorItem("decor_lava_fall", "熔岩瀑布", "裝飾", "decoration", "#FF5514", ("lava_fall", 3), "熔瀑"))
register(EditorItem("decor_ember_vent", "餘燼噴口", "裝飾", "decoration", "#DF4921", ("ember_vent", 2), "火口"))
register(EditorItem("decor_bone_pile", "焦骨堆", "裝飾", "decoration", "#C4AE8D", ("bone_pile", 2), "骨堆"))
register(EditorItem("decor_hanging_chain", "地獄吊鏈", "裝飾", "decoration", "#5D5050", ("hanging_chain", 3), "獄鏈"))
register(EditorItem("decor_hell_furnace", "地獄熔爐", "裝飾", "decoration", "#A9341B", ("hell_furnace", 3), "熔爐"))
register(EditorItem("decor_demon_statue", "惡魔石像", "裝飾", "decoration", "#512F32", ("demon_statue", 3), "魔像"))
register(EditorItem("decor_hell_rune", "煉獄符文陣", "裝飾", "decoration", "#EF3A24", ("hell_rune", 3), "獄紋"))


CATEGORY_ORDER = ["地形", "巨木", "分層", "液體", "火焰", "草", "生態", "木", "裝飾", "自訂素材"]


def items_for_category(category):
    return [
        item
        for item in ITEMS
        if item.category == category
    ]


def load_custom_items(project_root):
    """Register AssetEditor-authored map tiles into the editor palette.

    Safe to call repeatedly: register() de-duplicates IDs. The metadata is
    intentionally outside runtime.zip so an edited/added map asset appears in
    MapEditor on the next open without a code rebuild.
    """
    path=os.path.join(os.path.abspath(str(project_root)),"assets","custom_map_assets.json")
    try:
        with open(path,"r",encoding="utf-8") as source:
            data=json.load(source)
    except Exception:
        return 0
    rows=data.get("assets",{}) if isinstance(data,dict) else {}
    if not isinstance(rows,dict):return 0
    n=0
    for asset_id,meta in sorted(rows.items()):
        if not isinstance(meta,dict):continue
        aid=str(asset_id)
        if not aid.startswith("tile."):continue
        slug=aid.split(".",1)[1]
        label=str(meta.get("name") or slug)
        tag=str(meta.get("tag") or "自訂")
        col=str(meta.get("color") or "#7D8790")
        symbol=(tag[:2] or "自")
        register(EditorItem("custom_"+slug,label,"自訂素材","custom_map",col,aid,symbol))
        n+=1
    return n
