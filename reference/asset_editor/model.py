# -*- coding: utf-8 -*-
"""Project-local asset + editable pixel-source model.

V0.7.7.5 keeps the old inbox/binding workflow, and adds a self-contained pixel
editor source format.  Pixel art is saved as a real PNG (no Pillow required)
plus a tiny JSON source file so it can be reopened and edited losslessly.
"""
import copy
import json
import math
import os
import re
import shutil
import struct
import zlib

from world.tile_registry import TILES
from config import PIXEL_WORLD_SCALE
from engine.pixel_art import PIXEL_MAPPING_FIXED, PIXEL_MAPPING_FIT, source_anchor_px, canvas_world_span
from asset_editor.creature_pixel_art import creature_default_grid, recommended_canvas_size as creature_recommended_canvas_size
try:
    from asset_editor.builtin_capture import capture_builtin, builtin_world_size
except Exception:
    capture_builtin = None
    builtin_world_size = None
try:
    from systems.biome_system import CREATURE_ARCHETYPES
except Exception:
    CREATURE_ARCHETYPES = {}
try:
    from systems.weapon_catalog import WEAPON_DEFS
except Exception:
    WEAPON_DEFS = {}

CATEGORIES = (
    ("tile", "圖塊", "tiles"),
    ("creature", "生物", "creatures"),
    ("plant", "植物", "plants"),
    ("player", "角色", "player"),
    ("weapon", "武器", "weapons"),
    ("equipment", "裝備", "equipment"),
    ("magic", "魔法素材", "magic"),
    ("item", "物品圖像", "items"),
    ("ui", "介面圖示", "ui"),
    ("decoration", "場景裝飾", "decorations"),
    ("effect", "效果素材", "effects"),
    ("inventory", "背包物品圖像", "inventory_icons"),
    ("background", "背景素材", "backgrounds"),
)
PIXEL_EDIT_CATEGORIES = tuple(row[0] for row in CATEGORIES)
SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
PIXEL_SOURCE_FORMAT = 8
PIXEL_MAPPING = PIXEL_MAPPING_FIXED
# FIX98: import dimensions are measured, not rounded down to a preset.
MAX_IMPORT_SIZE = 192
TILE_TEXTURE_MAPPING = "tile_texture"
PIXEL_SIZES = (16, 24, 32, 48, 64, 96, 128)
# Weapon artwork and VFX do not share the same authoring bounds. The weapon
# body remains a compact 24px source while effect flipbooks use a larger local
# workspace. This follows the usual VFX split between source art, effect
# bounds and world-space motion, and prevents wide bursts from being cropped by
# the weapon sprite's own canvas.
VFX_PIXEL_SIZES = (16, 24, 32, 48, 64, 96, 128, 160, 192)
DEFAULT_VFX_CANVAS_SIZE = 64
EFFECT_STATES = ("normal_effect", "heavy_effect")
TRANSPARENT = "#00000000"


def _slug(name):
    base = os.path.splitext(os.path.basename(str(name)))[0].strip().lower()
    base = re.sub(r"[^0-9a-zA-Z_\-]+", "_", base).strip("_")
    return base or "asset"


def _normalize_new_asset_id(category, raw_id):
    """Normalize an author-entered asset ID without treating dots as extensions.

    Accepts either a short ID (``rainforest_branch``) or a full ID
    (``tile.rainforest_branch``).  FIX94 reused the filename-oriented ``_slug``
    helper here, so ``tile.rainforest_branch`` was parsed as filename ``tile``
    with extension ``.rainforest_branch`` and collapsed to ``tile.tile``.
    """
    category=str(category or "").strip().lower()
    raw=str(raw_id or "").strip().lower()
    if not raw:
        raise ValueError("請輸入素材 ID")

    # Full IDs are allowed.  If a prefix is present it must match the selected
    # category; silently changing a creature into a tile is worse than rejecting
    # the typo.
    if "." in raw:
        prefix, tail = raw.split(".", 1)
        if prefix in PIXEL_EDIT_CATEGORIES:
            if prefix != category:
                raise ValueError("素材 ID 分類與目前分類不一致：目前是 %s，但輸入了 %s.*" % (category, prefix))
            raw = tail
        else:
            raise ValueError("完整素材 ID 的前綴無效：%s" % prefix)

    # IDs are identifiers, not filenames.  Do not call os.path.splitext here.
    slug=re.sub(r"[^0-9a-zA-Z_\-]+", "_", raw).strip("_").lower()
    if not slug:
        raise ValueError("素材 ID 必須包含英文、數字、底線或連字號")
    return category + "." + slug


class CanvasImportError(ValueError):
    """An import must fail visibly instead of silently replacing/cropping art."""


def _checked_canvas_size(value):
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        raise CanvasImportError("素材畫布尺寸無效")
    if not 1 <= n <= MAX_IMPORT_SIZE:
        raise CanvasImportError("素材尺寸 %s 超出目前 1～%s 像素支援範圍；未裁切、未覆蓋原稿" % (n, MAX_IMPORT_SIZE))
    return n


def _grid_extent(grid):
    """Keep all rows/columns, including transparent edge pixels."""
    if not isinstance(grid, list) or not grid or any(not isinstance(r, list) for r in grid):
        raise CanvasImportError("素材像素資料無效；未改動目前畫布")
    w = max((len(r) for r in grid), default=0)
    h = len(grid)
    _checked_canvas_size(w); _checked_canvas_size(h)
    return w, h


def _native_grid(grid, declared=None):
    """Square editor source; rectangular inputs are transparently padded, never sampled."""
    w, h = _grid_extent(grid)
    n = _checked_canvas_size(max(w, h, int(declared or 0)))
    out = _blank(n)
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            out[y][x] = str(c)
    return out


def _import_canvas(grid, requested_size, category):
    """Optional requested size is a minimum, not permission to discard pixels."""
    src = _native_grid(grid)
    target = _checked_canvas_size(max(len(src), int(requested_size or 0)))
    return src if len(src) == target else _resize_canvas(src, target, category)


def _rgba_hex(value, alpha=255):
    s = str(value or "#000000").strip().lstrip("#")
    if len(s) == 8:
        return "#" + s.upper()
    if len(s) != 6:
        s = "FF00FF"
    return "#" + s.upper() + ("%02X" % max(0, min(255, int(alpha))))


def _rgba_bytes(value):
    s = str(value or TRANSPARENT).strip().lstrip("#")
    if len(s) == 6:
        s += "FF"
    if len(s) != 8:
        s = "00000000"
    try:
        return bytes(int(s[i:i+2], 16) for i in (0, 2, 4, 6))
    except Exception:
        return b"\x00\x00\x00\x00"


def _blank(size):
    return [[TRANSPARENT for _x in range(int(size))] for _y in range(int(size))]


def _set(grid, x, y, color):
    if 0 <= int(y) < len(grid) and 0 <= int(x) < len(grid[int(y)]):
        grid[int(y)][int(x)] = _rgba_hex(color) if str(color) != TRANSPARENT else TRANSPARENT


def _rect(grid, x0, y0, x1, y1, color):
    h = len(grid); w = len(grid[0]) if h else 0
    x0=max(0,int(round(x0))); y0=max(0,int(round(y0)))
    x1=min(w,int(round(x1))); y1=min(h,int(round(y1)))
    c=_rgba_hex(color)
    for y in range(y0,y1):
        row=grid[y]
        for x in range(x0,x1):
            row[x]=c


def _line(grid, x0, y0, x1, y1, color, thickness=1):
    x0=int(round(x0)); y0=int(round(y0)); x1=int(round(x1)); y1=int(round(y1))
    dx=abs(x1-x0); sx=1 if x0<x1 else -1
    dy=-abs(y1-y0); sy=1 if y0<y1 else -1; err=dx+dy
    t=max(1,int(thickness)); c=_rgba_hex(color)
    while True:
        r=t//2
        for yy in range(y0-r,y0-r+t):
            for xx in range(x0-r,x0-r+t):
                if 0<=yy<len(grid) and 0<=xx<len(grid[yy]):grid[yy][xx]=c
        if x0==x1 and y0==y1:break
        e2=2*err
        if e2>=dy:err+=dy; x0+=sx
        if e2<=dx:err+=dx; y0+=sy


def _resample(grid, new_size):
    old_h=len(grid); old_w=len(grid[0]) if old_h else 0
    n=max(1,int(new_size))
    if old_h <= 0 or old_w <= 0:
        return _blank(n)
    out=_blank(n)
    for y in range(n):
        sy=min(old_h-1,int(y*old_h/n))
        for x in range(n):
            sx=min(old_w-1,int(x*old_w/n))
            out[y][x]=grid[sy][sx]
    return out

def _detect_nearest_block_scale(grid, max_factor=4):
    """Detect an exact nearest-neighbour enlargement (2x/3x/4x...).

    Only a 100% block-uniform image is collapsed automatically, so genuine
    high-resolution pixel art is never guessed smaller merely because it uses
    chunky shapes.
    """
    try:
        h=len(grid); w=len(grid[0]) if h else 0
    except Exception:
        return 1
    if h<=1 or w<=1 or any(not isinstance(r,list) or len(r)!=w for r in grid):
        return 1
    # A solid/blank rectangle is not enough evidence of an upscaled source.
    # Require at least two visible colours before performing automatic collapse.
    visible={str(c) for row in grid for c in row if not str(c).upper().endswith("00")}
    if len(visible)<2:
        return 1
    for factor in range(min(int(max_factor),w,h),1,-1):
        if w%factor or h%factor:continue
        good=True
        for y in range(0,h,factor):
            for x in range(0,w,factor):
                c=grid[y][x]
                for yy in range(y,y+factor):
                    for xx in range(x,x+factor):
                        if grid[yy][xx]!=c:
                            good=False;break
                    if not good:break
                if not good:break
            if not good:break
        if good:return factor
    return 1

def _collapse_nearest_blocks(grid, factor):
    factor=max(1,int(factor)); h=len(grid); w=len(grid[0]) if h else 0
    if factor<=1:return copy.deepcopy(grid)
    return [[str(grid[y][x]) for x in range(0,w,factor)] for y in range(0,h,factor)]

def _nearest_export_grid(grid, export_size):
    """Nearest-only preview/export scaling; logical JSON is never expanded."""
    h=len(grid); w=len(grid[0]) if h else 0
    n=max(1,int(export_size or max(w,h,1)))
    if h<=0 or w<=0:return _blank(n)
    out=_blank(n)
    for y in range(n):
        sy=min(h-1,int(y*h/n))
        for x in range(n):
            sx=min(w-1,int(x*w/n))
            out[y][x]=grid[sy][sx]
    return out

def _category_anchor_px(category, size):
    return source_anchor_px(category,size)


def _resize_canvas(grid, new_size, category):
    """Resize the logical canvas WITHOUT scaling authored pixels.

    FIX20 keeps every existing cell 1:1 and only adds/crops transparent space
    around the stable pivot.  This is fundamentally different from _resample.
    """
    old_h=len(grid); old_w=len(grid[0]) if old_h else 0
    n=max(1,int(new_size))
    if old_h<=0 or old_w<=0:
        return _blank(n)
    out=_blank(n)
    old_n=max(old_h,old_w)
    oax,oay=_category_anchor_px(category,old_n)
    nax,nay=_category_anchor_px(category,n)
    dx=int(round(nax-oax)); dy=int(round(nay-oay))
    for y in range(old_h):
        ty=y+dy
        if not (0<=ty<n): continue
        row=grid[y] if isinstance(grid[y],list) else []
        for x in range(min(old_w,len(row))):
            tx=x+dx
            if 0<=tx<n:
                out[ty][tx]=str(row[x])
    return out


def _colored_cell_bounds(grid):
    xs=[]; ys=[]
    for y,row in enumerate(grid or []):
        if not isinstance(row,list): continue
        for x,c in enumerate(row):
            raw=str(c or "").upper()
            if raw and not raw.endswith("00"):
                xs.append(x); ys.append(y)
    if not xs:
        return None
    return (min(xs),min(ys),max(xs)+1,max(ys)+1)


CREATURE_COLORS = {
    "slime":"#63C56E", "boar":"#9B6845", "wolf":"#777B82", "deer":"#A8794E",
    "gull":"#E6EAEE", "shore_crab":"#C36F49", "sea_turtle":"#5C8D56",
    "heron":"#B8C7CF", "otter":"#8C6A4C", "mangrove_crab":"#A85E3F",
    "desert_scorpion":"#8A6139", "sand_lizard":"#A28C51", "desert_snake":"#C2A55E",
    "desert_beetle":"#72543B", "swamp_slime":"#65894B", "crocodile":"#496B3E",
    "swamp_snake":"#607A42", "swamp_frog":"#73A85C", "village_rat":"#7B7169",
    "bandit":"#8B4E45", "village_dog":"#9A774D", "villager":"#B88963",
    "jungle_spider":"#493B4F", "jaguar":"#C38A39", "jungle_snake":"#4F8A46",
    "capybara":"#8C6A4C", "snow_wolf":"#CED7DE", "mountain_goat":"#D0C4A8",
    "ice_slime":"#8FD6E7", "snow_hare":"#E3E8EB", "sardine":"#8BB8C7",
    "mackerel":"#5D8FA5", "sea_bass":"#6FA09A", "carp":"#B69058",
    "crucian_carp":"#A99C70", "freshwater_bass":"#6F8B63", "mallard":"#667B52",
    "kingfisher":"#4C8FA8", "dragonfly":"#77A7A3", "damselfly":"#7F90B8",
    "flying_imp":"#A83A35", "infernal_goat":"#342C31", "burning_slime":"#F05A24",
    "flame_turtle":"#3A3030", "exploding_wisp":"#F04B38", "crystal_knight":"#315785",
    "crystal_slime":"#5CC7E8", "crystal_skeleton":"#607C9A", "crystal_lizard":"#397A91",
}

PLANT_COLORS = {
    "map_grass": ("#3A9B3D", "#56B94A"),
    "map_tree": ("#6E4724", "#2E8C38"),
    "map_jungle_tree": ("#594323", "#236F31"),
    "map_reed": ("#788F33", "#704820"),
    "map_fern": ("#2B843A", "#44A84D"),
    "map_cactus": ("#3E8F4C", "#5AAA60"),
    "map_pine": ("#5E4429", "#32694F"),
}


ASSET_NAMES_ZH = {
    "player.default": "主角",
    "player.sword": "主角武器／劍",
    "weapon.sword": "鐵劍",
    "weapon.dagger": "獵刀",
    "weapon.spear": "長矛",
    "weapon.battle_axe": "巨斧",
    "weapon.war_hammer": "鐵鎚",
    "weapon.greatsword": "巨劍",
    "weapon.energy_bow": "能量弓",
    "weapon.whip": "甩鞭",
    "weapon.laser_gun": "雷射槍",
    "weapon.flying_drone": "飛行無人機",
    "equipment.double_jump_shoes": "二段跳鞋",
    "equipment.air_dash_shoes": "空中衝刺鞋",
    "equipment.hover_shoes": "浮空鞋",
    "equipment.shield_armor": "護盾盔甲",
    "equipment.electric_mouse_helmet": "皮卡丘風格頭盔",
    "equipment.sky_puppy_helmet": "大耳狗風格頭盔",
    "tile.dirt": "土壤",
    "tile.grass_dirt": "草皮土壤",
    "tile.wood": "木材",
    "tile.stone": "石塊",
    "tile.ladder": "梯子",
    "tile.ice": "冰塊",
    "tile.ash": "灰燼土壤",
    "tile.copper_ore": "銅礦",
    "tile.iron_ore": "鐵礦",
    "tile.gold_ore": "金礦",
    "tile.marble": "大理石",
    "tile.limestone": "灰岩",
    "tile.mud": "淤泥",
    "tile.swamp_soil": "沼澤土壤",
    "tile.sand": "沙子",
    "tile.sea_sand": "海沙",
    "tile.jungle_soil": "雨林土壤",
    "tile.rainforest_root": "雨林巨木・根基",
    "tile.rainforest_trunk": "雨林巨木・樹幹",
    "tile.rainforest_branch": "雨林巨木・可站枝條",
    "tile.rainforest_vine": "雨林巨木・攀爬藤",
    "tile.rainforest_canopy": "雨林巨木・樹冠",
    "tile.snow_dirt": "雪地土壤",
    "tile.village_path": "村落道路",
    "creature.slime": "史萊姆",
    "creature.boar": "野豬",
    "creature.wolf": "狼",
    "creature.deer": "鹿",
    "creature.gull": "海鷗",
    "creature.shore_crab": "岸蟹",
    "creature.sea_turtle": "海龜",
    "creature.heron": "鷺鳥",
    "creature.otter": "水獺",
    "creature.mangrove_crab": "紅樹林蟹",
    "creature.sardine": "沙丁魚",
    "creature.mackerel": "鯖魚",
    "creature.sea_bass": "海鱸",
    "creature.carp": "鯉魚",
    "creature.crucian_carp": "鯽魚",
    "creature.freshwater_bass": "淡水鱸",
    "creature.mallard": "綠頭鴨",
    "creature.kingfisher": "翠鳥",
    "creature.dragonfly": "蜻蜓",
    "creature.damselfly": "豆娘",
    "creature.desert_scorpion": "沙漠蠍",
    "creature.sand_lizard": "沙蜥蜴",
    "creature.desert_snake": "沙漠蛇",
    "creature.desert_beetle": "沙漠甲蟲",
    "creature.swamp_slime": "沼澤史萊姆",
    "creature.crocodile": "鱷魚",
    "creature.swamp_snake": "沼澤蛇",
    "creature.swamp_frog": "沼澤蛙",
    "creature.jungle_spider": "叢林蜘蛛",
    "creature.jaguar": "美洲豹",
    "creature.jungle_snake": "叢林蛇",
    "creature.capybara": "水豚",
    "creature.snow_wolf": "雪狼",
    "creature.mountain_goat": "山羊",
    "creature.ice_slime": "冰史萊姆",
    "creature.snow_hare": "雪兔",
    "creature.village_rat": "村落老鼠",
    "creature.bandit": "強盜",
    "creature.village_dog": "村犬",
    "creature.villager": "村民",
    "creature.cave_bat": "洞穴蝙蝠",
    "creature.cave_spider": "洞穴巨蛛",
    "creature.zombie": "殭屍",
    "creature.giant_worm": "巨型蠕蟲",
    "creature.dungeon_bandit_mage": "地下城魔法強盜",
    "creature.creeper": "苦力帕",
    "creature.blue_poop": "藍色大便",
    "creature.giant_bago_bird": "彩色巨型巴戈鳥",
    "creature.fire_zombie": "火焰殭屍",
    "creature.lightning_zombie": "閃電殭屍",
    "creature.ghost": "幽靈",
    "creature.toilet_man": "馬桶人",
    "creature.speaker_man": "音響人",
    "creature.abyss_colossus": "深淵巨像 Boss",
    "creature.giant_spider_monster": "巨型蜘蛛怪",
    "creature.cave_snorble": "洞洞傻呼嚕",
    "creature.plane_monster": "飛機怪獸",
    "creature.flying_imp": "飛行小惡魔",
    "creature.infernal_goat": "煉獄羊",
    "creature.burning_slime": "燃燒史萊姆",
    "creature.flame_turtle": "火炎龜",
    "creature.exploding_wisp": "自爆鬼火",
    "creature.crystal_knight": "水晶騎士",
    "creature.crystal_slime": "水晶史萊姆",
    "creature.crystal_skeleton": "水晶骷髏怪",
    "creature.crystal_lizard": "水晶蜥蜴",
    "plant.map_grass": "草",
    "plant.map_tree": "樹木",
    "plant.map_jungle_tree": "雨林樹",
    "plant.map_reed": "蘆葦",
    "plant.map_fern": "蕨類",
    "plant.map_cactus": "仙人掌",
    "plant.map_pine": "松樹"
}


# FIX88: names match the drop/inventory and remain visible in the editor.
ASSET_NAMES_ZH.update({'weapon.'+key:row['name'] for key,row in WEAPON_DEFS.items()})

class AssetEditorModel:
    FORMAT = 3

    def __init__(self, project_root):
        self.project_root = os.path.abspath(project_root)
        self.assets_root = os.path.join(self.project_root, "assets")
        self.inbox = os.path.join(self.assets_root, "inbox")
        self.pixel_sources = os.path.join(self.assets_root, "pixel_sources")
        self.registry_path = os.path.join(self.assets_root, "registry.json")
        for _, _, folder in CATEGORIES:
            os.makedirs(os.path.join(self.assets_root, folder), exist_ok=True)
        os.makedirs(self.inbox, exist_ok=True)
        os.makedirs(self.pixel_sources, exist_ok=True)
        self.data = self._load()
        self.catalog = self._load_catalog()
        self.custom_creature_names = self._load_custom_creature_names()
        # Shared ID discovery only: never pre-load hundreds of image frames.
        from systems.editor_catalog import EditorAssetCatalog
        self.shared_catalog = EditorAssetCatalog(self.project_root)
        self.catalog = self.shared_catalog.categories()
        self.editor_manifest = self.shared_catalog.by_id
        self.last_import_scale = 1

    def _load_custom_creature_names(self):
        """Resolve editor labels from the same data used by the runtime.

        FIX71 adds a large data-authored roster.  Keeping those labels in
        custom_creatures.json avoids another hard-coded 50-name table and also
        makes future editor-created species immediately readable.
        """
        path = os.path.join(self.assets_root, "custom_creatures.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("creatures", {}) if isinstance(data, dict) else {}
            if not isinstance(rows, dict):
                return {}
            return {
                "creature." + str(species): str(row.get("name") or species)
                for species, row in rows.items()
                if isinstance(row, dict)
            }
        except Exception:
            return {}

    def _load_catalog(self):
        path = os.path.join(self.assets_root, "binding_catalog.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cats = data.get("categories", {}) if isinstance(data, dict) else {}
            return cats if isinstance(cats, dict) else {}
        except Exception:
            return {}

    def recommended_ids(self, category):
        rows = self.catalog.get(str(category), [])
        if not isinstance(rows, list):
            return []
        return [str(x) for x in rows if str(x).strip()]

    def display_name(self, asset_id):
        """Chinese display name while preserving the stable English asset ID."""
        aid=str(asset_id)
        label=self.editor_manifest.get(aid,{}).get("name") or ASSET_NAMES_ZH.get(aid) or self.custom_creature_names.get(aid)
        if label:
            return label
        tail=aid.split('.',1)[-1].replace('_',' ').strip()
        return tail or aid

    def asset_world_size(self, asset_id):
        """Approximate current game draw size, in world pixels (tile = 40 px)."""
        if str(asset_id).startswith(("weapon.", "equipment.")):
            return (60.0,60.0)
        if builtin_world_size is not None:
            try:
                w,h=builtin_world_size(str(asset_id))
                return (max(1.0,float(w)),max(1.0,float(h)))
            except Exception:
                pass
        return (40.0,40.0)

    def _load(self):
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data["format"] = self.FORMAT
                data.setdefault("bindings", {})
                return data
        except Exception:
            pass
        return {"format": self.FORMAT, "bindings": {}}

    def save(self):
        self.data["format"] = self.FORMAT
        tmp = self.registry_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.registry_path)


    def _catalog_path(self):
        return os.path.join(self.assets_root, "binding_catalog.json")

    def _save_catalog(self):
        path=self._catalog_path()
        payload={"format":4,"categories":self.catalog}
        tmp=path+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)

    def create_pixel_asset(self, category, short_id, display_name="", size=None, map_tag="", map_role="solid", map_color="#7D8790"):
        """Create a brand-new editable asset and immediately register it.

        FIX32 makes the pixel editor a real authoring entry point instead of a
        built-in-only editor. Creature assets also receive a small runtime
        profile so the game can spawn them without any Python changes.
        """
        category=str(category).strip()
        if category not in PIXEL_EDIT_CATEGORIES:
            raise ValueError("此類別目前不支援直接新增像素材")
        asset_id=_normalize_new_asset_id(category, short_id)
        from systems.custom_tile_rules import collision_fields
        # Validate before writing even a blank image: unknown roles must not
        # leave a half-created asset or silently become an impassable block.
        tile_fields = collision_fields(map_role) if category == 'tile' else {}
        slug=asset_id.split(".",1)[1]
        path=self._pixel_source_path(asset_id)
        if os.path.isfile(path):
            raise ValueError("素材已存在："+asset_id)
        n=int(size or (24 if category=="creature" else self.recommended_canvas_size(asset_id)))
        if category=="tile": n=16
        if n not in PIXEL_SIZES: n=24 if category in ("creature","equipment") else 16
        _base_blank=_blank(n)
        _anims={}
        if category=="creature":
            for _st in self.default_animation_states("creature"):
                _anims[_st]={"fps":8.0 if _st in ("move","attack","special") else 6.0,"loop":_st in ("idle","move"),
                             "frames":[copy.deepcopy(_base_blank)],"events":({"action":0,"effect":0} if _st in ("attack","special") else {})}
        elif category=="effect":
            _anims={"effect":{"fps":12.0,"loop":False,"frames":[copy.deepcopy(_base_blank)],"events":{},"frame_events":{}}}
        payload={
            "format":"pyto_pixel_source", "version":PIXEL_SOURCE_FORMAT,
            "asset_id":asset_id, "category":category, "size":n,
            "pixel_mapping":PIXEL_MAPPING_FIT if category in ("creature","effect") else PIXEL_MAPPING, "pixel_world_scale":float(PIXEL_WORLD_SCALE),
            "world_fit":"actor_bounds_trimmed" if category=="creature" else ("effect_box" if category=="effect" else ""),
            "pixels":_base_blank, "animations":_anims, "combat_bindings":{},
        }
        with open(path,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,indent=2)
        rows=self.catalog.setdefault(category,[])
        if asset_id not in rows: rows.append(asset_id)
        self._save_catalog()
        label=str(display_name or slug.replace("_"," ")).strip()
        if label:
            ASSET_NAMES_ZH[asset_id]=label
            if category=="creature":
                self.custom_creature_names[asset_id]=label

        # FIX94: persist the authoring category/name for every new asset.  The
        # map editor uses the same manifest + registry, so user-authored art no
        # longer loses its Chinese label after reopening either editor.
        manifest_path=os.path.join(self.assets_root,"editor_asset_manifest.json")
        try:
            with open(manifest_path,"r",encoding="utf-8") as source:
                manifest=json.load(source)
            if not isinstance(manifest,dict):manifest={}
        except Exception:
            manifest={}
        manifest.setdefault("version",1)
        entries=manifest.setdefault("assets",{})
        meta=entries.setdefault(asset_id,{})
        meta.update({
            "name":label or slug, "size":n, "category":category,
            "user_authored":True, "map_sync":True,
        })
        tmp=manifest_path+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(manifest,f,ensure_ascii=False,indent=2)
        os.replace(tmp,manifest_path)
        self.editor_manifest[asset_id]=dict(self.editor_manifest.get(asset_id,{}) or {},**meta)

        # Keep one lightweight map-facing metadata index for *all* categories.
        # Tile entries retain collision semantics; every other category is
        # explicitly a visual placement unless another game system (creature,
        # chest item, etc.) gives it functional behaviour.
        mpath=os.path.join(self.assets_root,"custom_map_assets.json")
        try:
            with open(mpath,"r",encoding="utf-8") as source:
                mdata=json.load(source)
            if not isinstance(mdata,dict):mdata={}
        except Exception:
            mdata={}
        defs=mdata.setdefault("assets",{})
        role=str(map_role or "solid").strip().lower() if category=="tile" else "visual"
        color=str(map_color or "#7D8790").strip()
        defs[asset_id]={
            "name":label or slug,
            "tag":str(map_tag or ("自訂圖塊" if category=="tile" else "素材編輯器")).strip() or "素材編輯器",
            "role":role,
            "collision":"solid" if (category=="tile" and role=="solid") else "passable",
            "color":color, "category":category, "visual":category!="tile",
        }
        defs[asset_id].update(tile_fields)
        tmp=mpath+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(mdata,f,ensure_ascii=False,indent=2)
        os.replace(tmp,mpath)

        if category=="creature":
            cpath=os.path.join(self.assets_root,"custom_creatures.json")
            try:
                with open(cpath,"r",encoding="utf-8") as source:
                    data=json.load(source)
                if not isinstance(data,dict): data={}
            except Exception:
                data={}
            defs=data.setdefault("creatures",{})
            defs[slug]={
                "name":label or slug, "hp":80, "attack":8, "speed":48,
                "w":36, "h":32, "hostile":False, "locomotion":"ground",
                "habitat":"plains", "loot":[]
            }
            tmp=cpath+".tmp"
            with open(tmp,"w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False,indent=2)
            os.replace(tmp,cpath)
        # Create the PNG + registry binding immediately. The artist may then
        # edit/overwrite it, but the ID is already a first-class runtime asset.
        self.save_pixel_asset(asset_id, category, payload["pixels"], animations=payload.get("animations",{}), combat_bindings=payload.get("combat_bindings",{}))
        return asset_id

    def get_tile_map_properties(self, asset_id):
        """Properties of an existing custom tile, independent of its pixels."""
        aid = str(asset_id)
        if not aid.startswith('tile.'):
            raise ValueError('請先選擇自訂圖塊')
        path = os.path.join(self.assets_root, 'custom_map_assets.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except FileNotFoundError:
            raise ValueError('目前沒有自訂圖塊屬性')
        rows = payload.get('assets', {}) if isinstance(payload, dict) else {}
        row = rows.get(aid) if isinstance(rows, dict) else None
        if not isinstance(row, dict):
            raise ValueError('此為內建圖塊；圖塊屬性僅修改已新增的自訂圖塊')
        from systems.custom_tile_rules import role_from_meta
        result = copy.deepcopy(row)
        result['asset_id'] = aid
        result['role'] = role_from_meta(row)
        return result

    def set_tile_map_role(self, asset_id, role):
        """Atomic metadata-only change. Keeps ID, artwork, animation and maps.

        The stable asset ID is used by every existing placement. Restarting the
        game re-resolves that ID with these rules; no repaint or save reset.
        """
        import shutil
        import tempfile
        from systems.custom_tile_rules import collision_fields
        fields = collision_fields(role)
        aid = str(asset_id)
        self.get_tile_map_properties(aid)  # validate target, never synthesize it
        path = os.path.join(self.assets_root, 'custom_map_assets.json')
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        rows = payload['assets']
        if not isinstance(rows.get(aid), dict):
            raise ValueError('找不到這個自訂圖塊，請重新載入')
        rows[aid].update(fields)
        backup = path + '.pre_FIX96.bak'
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.assets_root, prefix='.tile_rules_', suffix='.tmp',
                    delete=False) as f:
                tmp = f.name
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try: os.remove(tmp)
                except OSError: pass
        return copy.deepcopy(rows[aid])

    # ------------------------------------------------------------------
    # Existing inbox binding workflow
    # ------------------------------------------------------------------
    def scan_inbox(self):
        rows = []
        try:
            for name in sorted(os.listdir(self.inbox)):
                path = os.path.join(self.inbox, name)
                if os.path.isfile(path) and os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                    rows.append(name)
        except Exception:
            pass
        return rows

    def default_asset_id(self, category, filename):
        return "%s.%s" % (str(category), _slug(filename))

    @staticmethod
    def _parse_nonnegative_int(value, default=0):
        try:
            return max(0, int(str(value).strip() or default))
        except Exception:
            return int(default)

    def bind_from_inbox(self, filename, category, asset_id=None, frame_width=0, frame_height=0):
        filename = os.path.basename(str(filename))
        src = os.path.join(self.inbox, filename)
        if not os.path.isfile(src):
            raise FileNotFoundError(src)
        category = str(category)
        folder = next((row[2] for row in CATEGORIES if row[0] == category), None)
        if folder is None:
            raise ValueError("未知素材類別：" + category)
        asset_id = str(asset_id or self.default_asset_id(category, filename)).strip()
        ext = os.path.splitext(filename)[1].lower()
        dest_name = _slug(asset_id.replace(".", "_")) + ext
        dest_abs = os.path.join(self.assets_root, folder, dest_name)
        shutil.copy2(src, dest_abs)
        rel = os.path.relpath(dest_abs, self.assets_root).replace(os.sep, "/")
        fw = self._parse_nonnegative_int(frame_width, 0)
        fh = self._parse_nonnegative_int(frame_height, 0)
        defaults = self._binding_defaults(category)
        row = {
            "category": category, "file": rel, "source_name": filename,
            "frame_width": fw, "frame_height": fh, "frame_index": 0,
        }
        row.update(defaults)
        self.data.setdefault("bindings", {})[asset_id] = row
        self.save()
        return asset_id, row

    def _binding_defaults(self, category):
        return {
            "tile": {"anchor": "tile", "preserve_aspect": False, "flip_with_facing": False},
            "player": {"anchor": "feet", "preserve_aspect": True, "flip_with_facing": True},
            "creature": {"anchor": "feet", "preserve_aspect": True, "flip_with_facing": True},
            "plant": {"anchor": "feet", "preserve_aspect": True, "flip_with_facing": False},
            "weapon": {"anchor": "center", "preserve_aspect": True, "flip_with_facing": True},
            "equipment": {"anchor": "feet", "preserve_aspect": True, "flip_with_facing": True},
            "magic": {"anchor": "center", "preserve_aspect": True, "flip_with_facing": False},
            "item": {"anchor": "center", "preserve_aspect": True, "flip_with_facing": False},
            "ui": {"anchor": "center", "preserve_aspect": True, "flip_with_facing": False},
        }.get(str(category), {"anchor":"center","preserve_aspect":True,"flip_with_facing":False})

    # ------------------------------------------------------------------
    # Editable built-in pixel templates
    # ------------------------------------------------------------------
    def _pixel_source_path(self, asset_id):
        return os.path.join(self.pixel_sources, _slug(str(asset_id).replace(".", "_")) + ".json")

    def _category_for_asset(self, asset_id):
        aid=str(asset_id)
        return aid.split(".",1)[0] if "." in aid else ""

    def recommended_canvas_size(self, asset_id):
        """Preferred authored canvas size for this asset.

        FIX22 keeps the fixed global pixel density, but creatures can now use
        16/24/32 canvases according to body type so artists can preserve more
        identifying features in the editor.
        """
        aid=str(asset_id)
        if aid.startswith("tile."):
            return 16
        preferred = self.editor_manifest.get(aid,{}).get("size")
        if preferred in PIXEL_SIZES:
            return int(preferred)
        if aid.startswith("weapon."):
            from systems.boss_weapon_defs import BOSS_WEAPON_DEFS
            wid=aid.split(".",1)[1]
            if wid in BOSS_WEAPON_DEFS:
                return 24 if wid=="bee_swarm" else 32
            return 24
        if aid.startswith("equipment."):
            return 24
        if aid.startswith("creature."):
            try:
                species=aid.split(".",1)[1]
                preferred=int(creature_recommended_canvas_size(species))
                if preferred in PIXEL_SIZES:
                    return preferred
            except Exception:
                pass
        span=40.0
        if builtin_world_size is not None:
            try:
                w,h=builtin_world_size(aid); span=max(1.0,float(w),float(h))
            except Exception:
                span=40.0
        need=max(1,int(__import__("math").ceil(span/max(1e-6,float(PIXEL_WORLD_SCALE))-1e-9)))
        for n in PIXEL_SIZES:
            if n>=need:
                return int(n)
        return int(PIXEL_SIZES[-1])

    def canvas_world_size(self, size):
        span=canvas_world_span(size)
        return (span,span)

    def visible_world_size(self, pixels):
        bounds=_colored_cell_bounds(pixels)
        if bounds is None:
            return (0.0,0.0)
        x0,y0,x1,y1=bounds
        scale=float(PIXEL_WORLD_SCALE)
        return ((x1-x0)*scale,(y1-y0)*scale)

    def _legacy_anchor_fraction(self, asset_id, category):
        if str(category)=="tile":
            return (0.0,0.0)
        w=h=40.0
        if builtin_world_size is not None:
            try:w,h=builtin_world_size(str(asset_id))
            except Exception:pass
        span=max(1.0,float(w),float(h))
        if str(category) in ("creature","plant","player","equipment"):
            return (0.5, max(0.0,min(1.0,0.5+float(h)/(2.0*span))))
        return (0.5,0.5)

    def _migrate_legacy_grid(self, grid, asset_id, category, target_size=None):
        """Convert FIX19 normalized-square art into FIX20 fixed-density cells."""
        old_n=len(grid) if isinstance(grid,list) else 0
        if old_n<=0:return _blank(int(target_size or self.recommended_canvas_size(asset_id)))
        w=h=40.0
        if builtin_world_size is not None:
            try:w,h=builtin_world_size(str(asset_id))
            except Exception:pass
        span=max(1.0,float(w),float(h))
        fixed_span_cells=max(1,int(round(span/max(1e-6,float(PIXEL_WORLD_SCALE)))))
        scaled=_resample(grid,fixed_span_cells)
        n=int(target_size or self.recommended_canvas_size(asset_id))
        n=max(int(PIXEL_SIZES[0]),min(int(PIXEL_SIZES[-1]),n))
        out=_blank(n)
        fax,fay=self._legacy_anchor_fraction(asset_id,category)
        sax=float(fixed_span_cells)*fax; say=float(fixed_span_cells)*fay
        tax,tay=_category_anchor_px(category,n)
        dx=int(round(tax-sax)); dy=int(round(tay-say))
        for y,row in enumerate(scaled):
            ty=y+dy
            if not (0<=ty<n):continue
            for x,c in enumerate(row):
                tx=x+dx
                if 0<=tx<n:out[ty][tx]=str(c)
        return out

    def _migrate_legacy_animations(self, animations, asset_id, category, target_size):
        out={}
        if not isinstance(animations,dict):return out
        for state,raw in animations.items():
            if not isinstance(raw,dict):continue
            state=str(state); is_vfx=(str(category)=="weapon" and state in EFFECT_STATES)
            if is_vfx:
                # VFX frames describe a centre-pivoted effect volume, not the
                # weapon body's old normalized draw box. By this point
                # _normalize_animations has already centred old 24px effects in
                # their independent 64px workspace; preserve that result.
                try:frame_size=int(raw.get("canvas_size",DEFAULT_VFX_CANVAS_SIZE))
                except Exception:frame_size=DEFAULT_VFX_CANVAS_SIZE
                frame_size=_checked_canvas_size(max(frame_size,max((max(_grid_extent(f)) for f in raw.get("frames",[]) if isinstance(f,list) and f),default=1)))
                frames=[]
                for frame in raw.get("frames",[]):
                    if not isinstance(frame,list):continue
                    source_n=max(1,len(frame)); source=self._normalize_frame(frame,source_n)
                    frames.append(source if source_n==frame_size else _resize_canvas(source,frame_size,"weapon"))
            else:
                frame_size=int(target_size)
                frames=[self._migrate_legacy_grid(frame,asset_id,category,target_size) for frame in raw.get("frames",[]) if isinstance(frame,list)]
            if not frames:continue
            out[state]={
                "fps":float(raw.get("fps",6.0) or 6.0),
                "loop":bool(raw.get("loop",state not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect"))),
                "frames":frames,
                "events":self._normalize_animation_events(raw.get("events",{}),len(frames)),
                "frame_events":self._normalize_frame_events(raw.get("frame_events",{}),len(frames)),
            }
            if is_vfx:out[state]["canvas_size"]=frame_size
        return out

    def _equipment_editor_metadata(self, asset_id, payload=None):
        """Return equipment-only authoring metadata without normalising it.

        Overlay synchronisation and the gameplay equipment definition are
        intentionally opaque to the pixel editor.  Keeping the dictionaries
        lossless prevents an art-only save from severing the runtime binding.
        """
        if self._category_for_asset(asset_id)!="equipment":
            return {}
        payload=payload if isinstance(payload,dict) else {}
        row=self.data.get("bindings",{}).get(str(asset_id),{})
        row=row if isinstance(row,dict) else {}
        overlay=payload.get("overlay")
        if not isinstance(overlay,dict):overlay=row.get("overlay",{})
        equipment=payload.get("equipment",{})
        if not isinstance(equipment,dict):equipment={}
        out={"overlay":overlay,"equipment":equipment}
        if "equipment_slot" in row:out["equipment_slot"]=row.get("equipment_slot")
        if "icon_file" in row:out["icon_file"]=row.get("icon_file")
        return out

    def _read_source_payload(self, path):
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            raise CanvasImportError("素材來源無法讀取，未以預設圖覆蓋：%s" % exc)
        if not isinstance(payload, dict) or "pixels" not in payload:
            raise CanvasImportError("素材來源沒有有效 pixels，未改動原稿")
        return payload

    def _read_raster_pixels(self, rel, binding=None):
        """Decode the native image or explicitly declared sprite-sheet frame 1:1."""
        from systems.editor_catalog import safe_path
        try:
            from PIL import Image
        except ImportError as exc:
            raise CanvasImportError("匯入圖片需要 Pyto 的 Pillow；未改動原稿") from exc
        binding = binding if isinstance(binding, dict) else {}
        try:
            with Image.open(safe_path(self.assets_root, rel)) as im:
                w, h = im.size
                fw = int(binding.get("frame_width", 0) or 0)
                fh = int(binding.get("frame_height", 0) or 0)
                if fw > 0 and fh > 0:
                    if fw > w or fh > h or w % fw or h % fh:
                        raise CanvasImportError("圖集幀尺寸與來源圖片不吻合；未裁切原稿")
                    _checked_canvas_size(fw); _checked_canvas_size(fh)
                    index = int(binding.get("frame_index", 0) or 0)
                    columns = w // fw
                    if not 0 <= index < columns * (h // fh):
                        raise CanvasImportError("圖集幀索引超出來源圖片範圍")
                    x = (index % columns) * fw; y = (index // columns) * fh
                    im = im.crop((x, y, x + fw, y + fh))
                else:
                    _checked_canvas_size(w); _checked_canvas_size(h)
                rgba = im.convert("RGBA")
                w, h = rgba.size
                vals = list(rgba.getdata())
                grid=[["#%02X%02X%02X%02X" % tuple(vals[y*w+x]) for x in range(w)] for y in range(h)]
                factor=_detect_nearest_block_scale(grid)
                self.last_import_scale=int(factor)
                if factor>1:
                    grid=_collapse_nearest_blocks(grid,factor)
                return grid
        except CanvasImportError:
            raise
        except (OSError, ValueError) as exc:
            raise CanvasImportError("圖片匯入失敗，未以空白圖取代：%s" % exc) from exc

    def _native_source_for_template(self, asset_id):
        from systems.editor_catalog import safe_path
        meta = self.editor_manifest.get(asset_id, {})
        # Immutable built-in capture is preferred to an edited thumbnail.
        rel = meta.get("default_source")
        if rel:
            payload = self._read_source_payload(safe_path(self.assets_root, rel))
            if payload:
                return _native_grid(payload["pixels"], payload.get("size"))
        binding = self.data.get("bindings", {}).get(asset_id, {})
        rel = binding.get("file") or meta.get("file")
        if rel:
            return _native_grid(self._read_raster_pixels(rel, binding))
        if asset_id.startswith("equipment."):
            payload = self._read_source_payload(self._pixel_source_path(asset_id))
            if payload:
                return _native_grid(payload["pixels"], payload.get("size"))
        return None

    def load_pixel_asset(self, asset_id, requested_size=None):
        asset_id = str(asset_id); category = self._category_for_asset(asset_id)
        from systems.editor_catalog import safe_path
        effect_meta = self.editor_manifest.get(asset_id, {}) if category == "effect" else {}
        parent_id = effect_meta.get("effect_source_asset")
        effect_state = effect_meta.get("effect_source_state")
        if parent_id and effect_state:
            parent_binding = self.data.get("bindings", {}).get(str(parent_id), {})
            rel = parent_binding.get("pixel_source") or "pixel_sources/" + _slug(str(parent_id).replace(".", "_")) + ".json"
            parent = self._read_source_payload(safe_path(self.assets_root, rel))
            raw = (parent or {}).get("animations", {}).get(str(effect_state), {})
            if isinstance(raw, dict) and raw.get("frames"):
                animation = self._normalize_animations({str(effect_state): raw}, int((parent or {}).get("size", 24)))[str(effect_state)]
                native = int(animation.get("canvas_size", len(animation["frames"][0])))
                target = _checked_canvas_size(max(native, int(requested_size or 0)))
                animation["frames"] = [_import_canvas(f, target, "effect") for f in animation["frames"]]
                animation["canvas_size"] = target
                representative = max(animation["frames"], key=lambda f:sum(not str(c).upper().endswith("00") for r in f for c in r))
                effect_binding=self.data.get("bindings",{}).get(asset_id,{})
                export_size=int(effect_binding.get("export_size",raw.get("export_size",target)) or target) if isinstance(effect_binding,dict) else target
                return {"asset_id":asset_id, "size":target, "logical_size":target,
                        "export_size":export_size, "export_scale":float(export_size)/max(1,target),
                        "pixels":copy.deepcopy(representative),
                        "animations":{"effect":animation}, "combat_bindings":{}, "source":"linked_effect",
                        "pixel_mapping":str(effect_binding.get("pixel_mapping",PIXEL_MAPPING) if isinstance(effect_binding,dict) else PIXEL_MAPPING),
                        "world_fit":str(effect_binding.get("world_fit","") if isinstance(effect_binding,dict) else ""),
                        "linked_effect_asset":str(parent_id),
                        "linked_effect_state":str(effect_state)}
        binding = self.data.get("bindings", {}).get(asset_id, {})
        path = self._pixel_source_path(asset_id)
        if binding.get("pixel_source"):
            path = safe_path(self.assets_root, binding["pixel_source"])
        if not os.path.isfile(path):
            rel = self.editor_manifest.get(asset_id, {}).get("default_source")
            if rel:
                path = safe_path(self.assets_root, rel)
        data = self._read_source_payload(path)
        if data is not None:
            grid = _native_grid(data["pixels"], data.get("size"))
            source_size = len(grid)
            target = _checked_canvas_size(max(source_size, int(requested_size or 0)))
            animations = self._normalize_animations(data.get("animations", {}), source_size)
            combat_bindings = self._normalize_combat_bindings(data.get("combat_bindings", {}))
            if category == "weapon" and not combat_bindings:
                combat_bindings = self._default_combat_bindings(asset_id)
            # FIX98: importing is never an implicit density conversion. Sources
            # without mapping retain their original on-disk scale until saved.
            # Existing fixed-density sources are already at 1:1 authoring pixels.
            grid = _import_canvas(grid, target, category)
            if target != source_size:
                animations = self._resize_canvas_animations(animations, target, category)
            mapping = str(data.get("pixel_mapping", data.get("mapping", "")) or PIXEL_MAPPING)
            if category == "tile" and target != 16:
                mapping = TILE_TEXTURE_MAPPING
            export_size=int(data.get("export_size",binding.get("export_size",source_size) if isinstance(binding,dict) else source_size) or source_size)
            result = {"asset_id":asset_id, "size":target, "logical_size":target,
                      "export_size":export_size, "export_scale":float(data.get("export_scale",float(export_size)/max(1,source_size)) or 1.0),
                      "pixels":grid, "animations":animations,
                      "combat_bindings":combat_bindings, "source":"saved", "pixel_mapping":mapping,
                      "world_fit":str(data.get("world_fit",binding.get("world_fit","") if isinstance(binding,dict) else "") or "")}
            result.update(self._equipment_editor_metadata(asset_id, data))
            return result
        # Crucially do NOT pass the category recommendation as a crop size to
        # make_builtin_template. File-backed defaults choose their native size.
        base = self.make_builtin_template(asset_id, requested_size)
        size = len(base)
        animations = self._default_weapon_animations(asset_id, base) if category == "weapon" else {}
        combat_bindings = self._default_combat_bindings(asset_id) if category == "weapon" else {}
        result = {"asset_id":asset_id, "size":size, "logical_size":size, "export_size":size, "export_scale":1.0,
                  "pixels":base, "animations":animations,
                  "combat_bindings":combat_bindings, "source":"builtin", "pixel_mapping":PIXEL_MAPPING, "world_fit":""}
        result.update(self._equipment_editor_metadata(asset_id, {}))
        return result

    def make_builtin_template(self, asset_id, size=None):
        asset_id=str(asset_id)
        native = None if asset_id.startswith("background.theme_") else self._native_source_for_template(asset_id)
        if native is not None:
            return _import_canvas(native, size, self._category_for_asset(asset_id))
        size = _checked_canvas_size(size or self.recommended_canvas_size(asset_id))
        # FIX91: procedural world backgrounds become editable without forcing
        # the game to ship duplicate full-screen images.  The first import is a
        # compact pixel-art approximation of the current theme; after the artist
        # saves it, Metal reads the authored source as an override.
        if asset_id.startswith("background.theme_"):
            theme=asset_id.split("background.theme_",1)[1]
            palettes={
                "ocean":("#79BED8FF","#6D9CB4FF","#386F8BFF"),"lake":("#86C3CFFF","#6F9DA2FF","#3F7470FF"),
                "swamp":("#75968AFF","#516F62FF","#294B3BFF"),"plains":("#8FC8E8FF","#75A16BFF","#477B43FF"),
                "village":("#A7C8DBFF","#778B71FF","#4D6251FF"),"rainforest":("#6EAA91FF","#356A55FF","#173F32FF"),
                "snow_mountain":("#B9D3E3FF","#A9BBC8FF","#6E8795FF"),"desert":("#E7C27BFF","#D8A85BFF","#B87836FF"),
                "underground":("#252A2FFF","#353B40FF","#1D2024FF"),"dungeon":("#23232CFF","#303039FF","#1B1C24FF"),
                "magma":("#321318FF","#4B1716FF","#241014FF"),"crystal":("#1D2940FF","#263B58FF","#182238FF"),
                "pyramid":("#46301EFF","#9A7041FF","#332417FF"),"glow_moss":("#102724FF","#245447FF","#143B32FF"),
                "crystal_mine":("#111C31FF","#29496AFF","#172A47FF"),"underground_castle":("#171720FF","#302E3AFF","#1E1B29FF"),
                "boss_dungeon":("#100D17FF","#35213AFF","#211426FF"),"magma_hell":("#260D12FF","#581814FF","#2F1010FF"),
            }
            sky,far,near=palettes.get(theme,("#243342FF","#41586AFF","#263644FF"))
            n=max(16,int(size)); grid=[[sky for _x in range(n)] for _y in range(n)]
            horizon=max(2,int(n*.58))
            for y in range(horizon,n):
                col=far if y<horizon+max(1,n//7) else near
                for x in range(n):grid[y][x]=col
            # Pixel silhouettes mirror the existing parallax language without
            # pretending to be a screenshot of the procedural renderer.
            for x in range(0,n,max(3,n//8)):
                h=2+(x//max(1,n//8))%4
                for y in range(max(0,horizon-h),horizon):
                    for xx in range(x,min(n,x+max(1,n//12))):grid[y][xx]=far
            if theme in ("rainforest","swamp","village","glow_moss"):
                for x in range(2,n,6):
                    for y in range(max(0,horizon-7),horizon+2):grid[y][x]=near
            elif theme in ("crystal","crystal_mine"):
                for x in range(3,n,7):
                    for k in range(5):
                        yy=horizon+1-k; xx=x+(k//2)
                        if 0<=yy<n and 0<=xx<n:grid[yy][xx]="#5FA6DFFF"
            elif theme in ("magma","magma_hell"):
                for x in range(1,n,5):
                    if horizon+2<n:grid[horizon+2][x]="#E34B2FFF"
            return grid
        # FIX21: capture the actual editable default art used by the game.
        # Keep this lightweight: no Metal/UIKit/game-scene import on the editor
        # UI thread, so switching category/object stays responsive.
        if capture_builtin is not None and not asset_id.startswith("weapon."):
            try:
                grid=capture_builtin(asset_id,size)
                if isinstance(grid,list) and len(grid)==size:
                    return grid
            except Exception:
                pass
        grid=_blank(size)
        if asset_id.startswith("tile."):
            self._draw_tile_template(grid,asset_id.split(".",1)[1])
        elif asset_id.startswith("creature."):
            self._draw_creature_template(grid,asset_id.split(".",1)[1])
        elif asset_id.startswith("plant."):
            self._draw_plant_template(grid,asset_id.split(".",1)[1])
        elif asset_id.startswith("player."):
            self._draw_player_template(grid,asset_id.split(".",1)[1])
        elif asset_id.startswith("weapon."):
            self._draw_weapon_template(grid,asset_id.split(".",1)[1])
        return grid

    def _draw_tile_template(self, grid, name):
        td=next((v for v in TILES.values() if str(getattr(v,"name",""))==str(name)),None)
        if td is None:
            _rect(grid,0,0,len(grid),len(grid),"#777777"); return
        n=len(grid)
        _rect(grid,0,0,n,n,getattr(td,"color","#777777"))
        top=getattr(td,"top_color",getattr(td,"color","#777777"))
        _rect(grid,0,0,n,max(1,n//5),top)
        # Small deterministic material accents approximate the current built-in
        # geometric palette without pretending to be a final art asset.
        if "ore" in str(name):
            accent=top
            for i in range(max(4,n//2)):
                x=(i*7+3)%n; y=(i*11+5)%n
                _rect(grid,x,y,min(n,x+2),min(n,y+2),accent)
        elif name in ("stone","marble","limestone"):
            accent=top
            for i in range(max(3,n//4)):
                x=(i*9+2)%n; y=(i*5+n//3)%n
                _rect(grid,x,y,min(n,x+3),min(n,y+1),accent)
        elif name in ("dirt","grass_dirt","mud","swamp_soil","sand","sea_sand","jungle_soil","snow_dirt"):
            accent=top
            for i in range(max(3,n//5)):
                x=(i*6+1)%n; y=min(n-1,n//3+(i*7)%(max(1,n*2//3)))
                _set(grid,x,y,accent)

    def _draw_creature_template(self, grid, species):
        # FIX21 fallback uses the same hand-authored default pixel generator as
        # builtin_capture. Copy cell-for-cell so the editor never drops back to
        # the old rectangle demonstration when capture import is unavailable.
        art=creature_default_grid(str(species),len(grid),CREATURE_ARCHETYPES)
        for y,row in enumerate(art):
            if y>=len(grid):break
            for x,color in enumerate(row):
                if x>=len(grid[y]):break
                grid[y][x]=str(color)

    def _draw_plant_template(self, grid, species):
        n=len(grid); brown,green=PLANT_COLORS.get(species,("#6E4724","#3F9345"))
        ground=n-2
        if species in ("map_tree","map_jungle_tree"):
            _rect(grid,n//2-1,int(n*0.36),n//2+2,ground,brown)
            _rect(grid,int(n*0.25),int(n*0.16),int(n*0.75),int(n*0.42),green)
        elif species=="map_pine":
            _rect(grid,n//2-1,int(n*0.35),n//2+2,ground,brown)
            for i,(yy,ww) in enumerate(((0.18,0.22),(0.30,0.34),(0.43,0.46))):
                half=int(n*ww/2); y=int(n*yy)
                _rect(grid,n//2-half,y,n//2+half,y+max(2,n//10),green)
        elif species=="map_reed":
            for off in (-3,0,3):
                x=n//2+off
                _rect(grid,x,int(n*0.32),x+1,ground,green)
                _rect(grid,x-1,int(n*0.27),x+2,int(n*0.36),brown)
        elif species=="map_fern":
            for off in (-5,-3,-1,1,3,5):
                x=n//2+off
                top=int(n*(0.42-0.02*abs(off)))
                _rect(grid,x,top,x+2,ground,green)
        elif species=="map_cactus":
            _rect(grid,n//2-2,int(n*0.22),n//2+3,ground,green)
            _rect(grid,n//2-6,int(n*0.48),n//2-1,int(n*0.58),green)
            _rect(grid,n//2+2,int(n*0.40),n//2+7,int(n*0.50),green)
        else:  # grass
            for off in (-4,-2,0,2,4):
                x=n//2+off
                _rect(grid,x,int(n*(0.55+0.03*abs(off))),x+1,ground,green)

    def _draw_player_template(self, grid, name):
        n=len(grid)
        if str(name)=="sword":
            # Current built-in sword: bright blade with a brown grip.
            _rect(grid,n//2-1,max(1,n//6),n//2+2,int(n*0.72),"#D9E0E7")
            _rect(grid,n//2-3,int(n*0.70),n//2+4,int(n*0.77),"#76502B")
            _rect(grid,n//2-1,int(n*0.75),n//2+2,min(n-1,int(n*0.94)),"#76502B")
        else:
            # Mirrors the current debug player body: one feet-anchored yellow
            # rectangle. Artists can turn this into the final character style.
            _rect(grid,max(1,int(n*0.25)),1,min(n-1,int(n*0.75)),n-1,"#F1CD57")

    def _draw_weapon_template(self, grid, weapon_id):
        """FIX54 editable default weapon artwork.

        The silhouettes follow the same pixel vocabulary as the accepted FIX50
        UI icons but use the AssetEditor palette so the artist can directly
        refine the in-game weapon source.  The canvas is center-anchored at the
        hand; attack states are authored as full frames around that pivot.
        """
        n=len(grid)
        # Keep the accepted FIX50 pixel density instead of scaling the icon to
        # fill the entire 24px canvas. The canvas centre is the runtime hand
        # pivot; offset the source so the guard/shaft crosses that pivot and the
        # blade grows outward from it.
        scale=1 if n <= 24 else 2
        ox=int(round(n*0.5-4.0*scale)); oy=int(round(n*0.5-8.0*scale))
        blade="#D1DBE6"; edge="#F5FAFF"; dark="#4D5763"; handle="#5C3822"; guard="#AD853D"
        parts={
            "sword": ((8,0,2,2,edge),(7,2,2,2,blade),(6,4,2,2,blade),(5,6,2,2,blade),(4,8,2,1,blade),(2,8,5,1,guard),(3,9,2,2,handle),(2,11,3,1,guard)),
            "dagger": ((8,2,2,1,edge),(7,3,2,1,edge),(6,4,2,1,blade),(5,5,2,1,blade),(4,6,2,1,blade),(3,7,4,1,guard),(3,8,1,2,handle),(2,10,3,1,guard),(8,1,1,1,edge)),
            "spear": ((9,0,1,1,edge),(8,1,2,1,edge),(7,2,3,1,blade),(7,3,2,1,blade),(6,4,1,1,guard),(5,5,1,1,handle),(5,6,1,1,handle),(4,7,1,1,handle),(4,8,1,1,handle),(3,9,1,1,handle),(3,10,1,1,handle),(2,11,2,1,guard)),
            "battle_axe": ((7,1,2,1,edge),(6,2,4,1,blade),(5,3,5,1,blade),(5,4,4,1,blade),(6,5,2,1,dark),(5,5,1,1,guard),(4,6,1,1,handle),(4,7,1,1,handle),(3,8,1,1,handle),(3,9,1,1,handle),(2,10,1,1,handle),(2,11,2,1,guard)),
            "war_hammer": ((5,1,5,2,dark),(6,0,3,1,edge),(4,3,5,1,blade),(5,4,2,1,guard),(5,5,1,1,handle),(4,6,1,1,handle),(4,7,1,1,handle),(3,8,1,1,handle),(3,9,1,1,handle),(2,10,1,1,handle),(2,11,2,1,guard)),
            "greatsword": ((8,0,3,2,edge),(7,2,3,2,blade),(6,4,3,2,blade),(5,6,3,2,blade),(4,8,3,1,blade),(2,8,7,1,guard),(3,9,2,2,handle),(2,11,4,1,guard),(9,2,1,4,edge)),
            "energy_bow": ((8,1,1,1,edge),(7,2,2,1,blade),(6,3,2,1,"#43D9FFFF"),(5,4,2,1,"#43D9FFFF"),(4,5,2,1,"#267FB6FF"),(4,6,1,2,dark),(5,8,1,1,"#267FB6FF"),(6,9,1,1,"#43D9FFFF"),(7,10,1,1,blade),(8,11,1,1,edge),(4,6,5,1,"#BFF7FFFF"),(7,5,1,3,"#BFF7FFFF")),
            "whip": ((2,10,3,1,guard),(3,8,2,2,handle),(4,7,2,1,"#7A4B2AFF"),(5,6,2,1,"#9A6238FF"),(6,5,2,1,"#7A4B2AFF"),(7,4,2,1,"#9A6238FF"),(8,3,2,1,"#7A4B2AFF"),(9,3,2,1,"#9A6238FF"),(10,4,1,2,"#7A4B2AFF"),(9,6,1,1,"#D1A166FF"),(8,7,1,1,edge)),
            "laser_gun": ((2,7,2,2,handle),(3,5,2,3,dark),(4,4,5,3,"#354C67FF"),(8,4,3,2,"#58DDF5FF"),(10,4,1,1,edge),(5,7,2,2,"#233044FF"),(6,3,2,1,"#A7F6FFFF"),(3,4,2,1,"#5F7691FF")),
            "flying_drone": ((3,5,6,3,"#172838FF"),(4,4,4,1,"#2B4B62FF"),(5,6,2,2,"#42DDF5FF"),(1,4,3,1,"#9EB8C8FF"),(8,4,3,1,"#9EB8C8FF"),(0,3,4,1,"#D8F7FFFF"),(8,3,4,1,"#D8F7FFFF"),(2,8,2,1,"#FF6B2BFF"),(8,8,2,1,"#FF6B2BFF"),(5,3,2,1,"#63EEFFFF")),
        }.get(str(weapon_id),())
        for x,y,w,h,c in parts:
            _rect(grid,ox+x*scale,oy+y*scale,ox+(x+w)*scale,oy+(y+h)*scale,c)

    @staticmethod
    def _transform_weapon_frame(grid, angle_deg=0.0, shift_x=0, shift_y=0):
        """Nearest-neighbour rotate/translate around the canvas centre."""
        n=len(grid); out=_blank(n)
        if n<=0:return out
        cx=(n-1)*0.5; cy=(n-1)*0.5
        rad=math.radians(float(angle_deg)); cs=math.cos(rad); sn=math.sin(rad)
        for y,row in enumerate(grid):
            for x,c in enumerate(row):
                raw=str(c or TRANSPARENT)
                if raw.upper().endswith("00"):continue
                dx=float(x)-cx; dy=float(y)-cy
                tx=int(round(cx+dx*cs-dy*sn+int(shift_x)))
                ty=int(round(cy+dx*sn+dy*cs+int(shift_y)))
                if 0<=tx<n and 0<=ty<n:out[ty][tx]=raw
        return out

    def _default_weapon_effect_frames(self, weapon_id, heavy=False, count=None):
        """Create editable reference frames matching FIX54's weapon effects.

        FIX58 keeps the exact procedural renderer as the untouched game
        default.  These frames expose the same visual language in AssetEditor:
        sword crescents, hunter-knife flurry, spear helix, axe leaf flecks and
        hammer stone/dust burst.  Normal attacks were effect-free in FIX54, so
        their default track intentionally remains transparent.
        """
        wid=str(weapon_id); n=DEFAULT_VFX_CANVAS_SIZE
        count=max(1,int(count or (6 if heavy else 5)))
        rows=[]; cx=(n-1)*0.5; cy=(n-1)*0.5
        row=WEAPON_DEFS.get(wid) or {}; style=str(row.get("heavy_style", ""))
        delivery=str(row.get("delivery","melee") or "melee")
        edge="#F5F7FAFF"
        leaf="#1F9457C2"; leaf_tip="#38B86BB3"; rock="#626872F0"; dust="#DBCCADAA"
        pixel_scale=max(1.0,float(PIXEL_WORLD_SCALE))

        def dot(grid,x,y,col,size=1):
            radius=max(0,int(size)//2)
            _rect(grid,int(round(x))-radius,int(round(y))-radius,int(round(x))+radius+1,int(round(y))+radius+1,col)

        def smooth(value):
            value=max(0.0,min(1.0,float(value))); return value*value*(3.0-2.0*value)

        if not heavy:
            for i in range(count):
                g=_blank(n); p=i/max(1,count-1)
                if delivery=="energy_arrow":
                    glow="#43D9FF%02X"%max(70,int(220*(1.0-abs(.5-p)*.7)))
                    _line(g,cx-8,cy,cx+8,cy,glow,2)
                    dot(g,cx+10,cy,"#E9FFFFFF",2)
                    dot(g,cx-10,cy,"#267FB6AA",1)
                elif delivery=="laser":
                    alpha=max(90,int(245*(1.0-.35*p)))
                    _line(g,cx-13,cy,cx+14,cy,"#43E8FF%02X"%alpha,2)
                    _line(g,cx-9,cy,cx+15,cy,"#F4FFFF%02X"%min(255,alpha+10),1)
                elif wid=="whip":
                    for q in range(9):
                        a=-0.8+q*.18+p*.35
                        x=cx+5+q*2.2; y=cy+math.sin(a*math.pi)*8
                        dot(g,x,y,"#D1A166%02X"%max(80,230-q*12),1)
                elif delivery=="companion_drone":
                    # Local dive exhaust. World-space travel and collision are
                    # previewed by DroneSystem, so the effect canvas only owns
                    # the readable thrust silhouette around the drone.
                    reach=5.0+13.0*p
                    for trail in range(4):
                        alpha=max(55,int(225*(1.0-trail*.19)*(1.0-.28*p)))
                        dot(g,cx-reach-trail*3.0,cy+(trail%2-.5)*3.0,"#27DDFB%02X"%alpha,2 if trail<2 else 1)
                    dot(g,cx+8.0,cy,"#F4FFFFFF",2)
                rows.append(g)
            return rows

        for i in range(count):
            g=_blank(n); p=i/max(1,count-1)
            a0=float(row.get("heavy_start_angle",-90.0) or -90.0)
            a1=float(row.get("heavy_end_angle",45.0) or 45.0)
            attack_angle=a0+(a1-a0)*smooth(p)
            rad=math.radians(attack_angle); dx=math.cos(rad); dy=math.sin(rad); pxv=-dy; pyv=math.cos(rad)

            if style in ("crescent_flash","flying_crescent"):
                visible=(p>=0.10) if style=="flying_crescent" else (0.20<=p<=0.82)
                if visible:
                    if style=="flying_crescent":
                        # The local flipbook contains the moon blade only. Its
                        # long forward travel is a separate projectile motion
                        # track, so it never has to fit inside this bitmap.
                        radius=max(7.0,float(row.get("heavy_projectile_radius",14.0) or 14.0)*1.50/pixel_scale)
                        for oy in (-0.92,-0.64,-0.30,0.08,0.44,0.76,0.98):
                            curve=(1.0-abs(oy))*7.0/pixel_scale
                            dot(g,cx+curve,cy+oy*radius,edge,2 if abs(oy)<0.5 else 1)
                    else:
                        length=max(14.0,float(row.get("visual_length",31.0) or 31.0))
                        radius=min((n-3)*0.5,length*1.08/pixel_scale)
                        for ai in range(-3,4):
                            ar=math.radians(attack_angle+ai*9.0)
                            dot(g,cx+math.cos(ar)*radius,cy+math.sin(ar)*radius,edge,2 if abs(ai)<=1 else 1)

            elif style=="dagger_flurry":
                # Same stagger and seven-cycle hand angle as FIX54. The 64px
                # VFX canvas preserves the full attack volume without the old
                # 24px compression/crop.
                for fi in range(11):
                    local=p*1.72-fi*0.105
                    if not (0.0<=local<=0.58):continue
                    fade=max(0.0,math.sin((local/0.58)*math.pi))
                    ga=-34.0+((fi*29)%58)-10.0*math.sin(local*math.pi*2.0)
                    gr=math.radians(ga); gdx=math.cos(gr); gdy=math.sin(gr); gpx=-gdy; gpy=gdx
                    forward=(13.0+(fi%5)*7.0+local*24.0)/pixel_scale
                    lateral=((fi%4)-1.5)*4.0/pixel_scale
                    gx=cx+gdx*forward+gpx*lateral; gy=cy+gdy*forward+gpy*lateral+(7.0+((fi%3)-1)*3.0)/pixel_scale
                    alpha=max(72,min(250,int(round(76+174*fade))))
                    bcol="#D9E0E7%02X"%alpha; ecol="#F5F7FA%02X"%min(255,alpha+16); hcol="#76502B%02X"%max(64,alpha-10)
                    dot(g,gx-gdx*1.5,gy-gdy*1.5,hcol,1)
                    _line(g,gx,gy,gx+gdx*3.6,gy+gdy*3.6,bcol,1)
                    dot(g,gx+gdx*4.2,gy+gdy*4.2,ecol,1)

            elif style=="lance_breaker":
                # FIX61 spear heavy: one readable piercing core plus expanding
                # pressure rings.  This replaces the small helix dots and makes
                # direction, range and impact timing legible at phone scale.
                pulse=math.sin(math.pi*p); reach=25.0+25.0*smooth(p)
                _line(g,cx-2,cy,cx+reach/pixel_scale,cy,"#D8F7FFFF",2)
                dot(g,cx+reach/pixel_scale+2,cy,"#FFFFFFFF",3)
                for ring in range(3):
                    local=p-ring*.11
                    if not (0.0<=local<=.82):continue
                    rcx=cx+(12.0+ring*10.0+local*22.0)/pixel_scale
                    rr=(4.0+local*11.0)/pixel_scale
                    alpha=max(42,int(220*(1.0-local/.82)))
                    col="#55DFFB%02X"%alpha
                    for ai in range(12):
                        ar=math.pi*2.0*ai/12.0
                        dot(g,rcx+math.cos(ar)*rr,cy+math.sin(ar)*rr,col,1)
                for streak in range(4):
                    off=(streak-1.5)*3.0/pixel_scale
                    start=cx+((5.0+streak*4.0)*pulse)/pixel_scale
                    _line(g,start,cy+off,start+(10.0+8.0*pulse)/pixel_scale,cy+off,"#A5F0FF88",1)

            elif style=="ground_slam" and p>=0.52:
                burst=max(0.0,min(1.0,(p-0.52)/0.48)); impact_y=cy+7.0
                for ri in range(10):
                    side=-1.0 if ri%2==0 else 1.0
                    spread=(ri//2+1)*4.8*side/pixel_scale
                    lift=math.sin(burst*math.pi)*(10.0+(ri%4)*3.5)/pixel_scale
                    dot(g,cx+spread,impact_y-lift,rock,2 if ri%3==0 else 1)
                for di in range(6):
                    dxo=(di-2.5)*7.0/pixel_scale; dyo=math.sin(burst*math.pi)*(2.5+di*0.6)/pixel_scale
                    _line(g,cx+dxo-1,impact_y-dyo,cx+dxo+1,impact_y-dyo,dust,1)

            elif style=="horizontal_cleave" and 0.18<=p<=0.82:
                for fi in range(8):
                    dist=(12.0+fi*4.2)/pixel_scale
                    lift=(math.sin(p*math.pi+fi*0.7)*7.0+(fi%3-1)*4.0)/pixel_scale
                    dot(g,cx+dist,cy+lift,leaf if fi%2 else leaf_tip,1)
            elif style=="energy_arrow_rain":
                # One local arrow/core per gameplay projectile.  Heavy density
                # comes from the twelve authoritative ballistic instances; a
                # three-arrow flipbook here would falsely render 36 hit bodies.
                _line(g,cx-2,cy-7,cx+2,cy+7,"#43D9FFCC",1)
                dot(g,cx+2,cy+8,"#F0FFFFFF",2)
                for gi in range(6):
                    ar=gi*math.pi/3.0+p*math.pi
                    dot(g,cx+math.cos(ar)*10,cy+math.sin(ar)*10,"#267FB688",1)
            elif style=="whip_spin":
                radius=(10.0+20.0*smooth(p))/pixel_scale
                alpha=max(55,int(235*(1.0-.55*p)))
                for ai in range(32):
                    ar=math.pi*2.0*(ai/32.0)+p*math.pi*2.0
                    wobble=2.0*math.sin(ai*.9+p*math.pi*4.0)
                    dot(g,cx+math.cos(ar)*(radius+wobble),cy+math.sin(ar)*(radius+wobble),"#D1A166%02X"%alpha,1)
                dot(g,cx+math.cos(p*math.pi*2.0)*radius,cy+math.sin(p*math.pi*2.0)*radius,"#F5E0B8FF",2)
            elif style=="laser_ricochet_10":
                # Direction-neutral local core.  The live projectile owns and
                # renders its true reflected world polyline separately.
                alpha=max(70,int(245*(1.0-.45*p)))
                for ring in range(3):
                    rr=3.0+ring*3.5+p*2.0
                    for ai in range(8):
                        ar=ai*math.pi*.25+p*math.pi*(1.0+ring*.2)
                        dot(g,cx+math.cos(ar)*rr,cy+math.sin(ar)*rr,"#3BCDF2%02X"%max(48,alpha-ring*38),1)
                dot(g,cx,cy,"#F2FFFFFF",3)
            elif style=="drone_kamikaze":
                # Explosion flipbook is deliberately 64x64: damage radius is
                # metadata, while this larger visual workspace prevents the
                # phone-sized 24px weapon canvas from clipping the burst.
                radius=(4.0+25.0*smooth(p))
                alpha=max(42,int(245*(1.0-.72*p)))
                for ring in range(3):
                    rr=max(2.0,radius-ring*4.0)
                    col=("#FF4A18%02X" if ring==0 else ("#FFAA22%02X" if ring==1 else "#FFF4B2%02X"))%alpha
                    for ai in range(16):
                        ar=math.pi*2.0*ai/16.0+ring*.15
                        dot(g,cx+math.cos(ar)*rr,cy+math.sin(ar)*rr,col,2 if ring==0 else 1)
                if p<.42:dot(g,cx,cy,"#FFFFFFFF",max(2,int(7.0*(1.0-p))))
            rows.append(g)
        return rows

    def _default_combat_bindings(self, asset_id):
        wid=str(asset_id).split(".",1)[1] if "." in str(asset_id) else str(asset_id)
        style=str((WEAPON_DEFS.get(wid) or {}).get("heavy_style", ""))
        row=WEAPON_DEFS.get(wid) or {}
        delivery=str(row.get("delivery","melee") or "melee")
        is_projectile=style in ("flying_crescent","energy_arrow_rain","laser_ricochet_10","drone_kamikaze") or delivery in ("energy_arrow","laser","companion_drone")
        heavy_anchor="ground" if style=="ground_slam" else ("projectile" if is_projectile else "weapon")
        normal_anchor="projectile" if delivery in ("energy_arrow","laser","companion_drone") else "weapon"
        motion="projectile" if is_projectile else "follow"
        default_mode="runtime" if wid in ("energy_bow","whip","laser_gun","flying_drone") else "fix54"
        return {
            # FIX58 keeps FIX54's proven procedural presentation until the
            # artist actually changes this VFX pair.  AssetEditor then switches
            # only that pair to render_mode=authored, preserving data-driven
            # timing/binding without silently replacing the shipped effects.
            "normal_attack": {"effect_state":"normal_effect","trigger_event":"effect","anchor":normal_anchor,"offset_x":0.0,"offset_y":0.0,"scale":1.0,"render_mode":default_mode,"motion":"projectile" if delivery in ("energy_arrow","laser","companion_drone") else "follow"},
            "heavy_attack": {"effect_state":"heavy_effect","trigger_event":"runtime_impact" if wid=="flying_drone" else "effect","anchor":heavy_anchor,"offset_x":0.0,"offset_y":0.0,"scale":1.0,"render_mode":default_mode,"motion":motion},
        }

    @staticmethod
    def _normalize_combat_bindings(bindings):
        out={}
        if not isinstance(bindings,dict):return out
        for attack_state,raw in bindings.items():
            if not isinstance(raw,dict):continue
            state=str(attack_state)
            effect_asset=str(raw.get("effect_asset","") or "")
            effect_state=str(raw.get("effect_state",("effect" if effect_asset else ("heavy_effect" if state in ("heavy_attack","special") else "normal_effect"))))
            trigger_event=str(raw.get("trigger_event","effect") or "effect")
            anchor=str(raw.get("anchor","actor" if state in ("attack","special") else "weapon") or "actor")
            if anchor not in ("hand","weapon","player","actor","creature","ground","ground_forward","target","projectile","action_box"):anchor="actor"
            def num(key,default,lo,hi):
                try:return max(lo,min(hi,float(raw.get(key,default) or default)))
                except Exception:return float(default)
            ox=num("offset_x",0.,-320.,320.);oy=num("offset_y",0.,-320.,320.);scale=num("scale",1.,.1,8.)
            render_mode=str(raw.get("render_mode","authored") or "authored").lower()
            if render_mode not in ("fix54","runtime","authored"):render_mode="authored"
            motion=str(raw.get("motion","follow") or "follow").lower()
            if motion not in ("follow","projectile","world"):motion="follow"
            pixel_density_mode=str(raw.get("pixel_density_mode","") or "").lower()
            if pixel_density_mode not in ("","match_owner"):pixel_density_mode=""
            segments=[]
            if isinstance(raw.get("segments"),list):
                for seg in raw.get("segments",[])[:16]:
                    if not isinstance(seg,dict):continue
                    clean={}
                    for key in ("delay","phase","frame","hold","dx_tiles","dy_tiles","scale","start_frame","clip_frames"):
                        if key in seg and isinstance(seg.get(key),(int,float)):clean[key]=seg.get(key)
                    if clean:segments.append(clean)
            world_motion_mode=str(raw.get("world_motion_mode","") or "").lower()
            if world_motion_mode not in ("stationary","segments","distance"):
                world_motion_mode="segments" if segments else "stationary"
            motion_repeat=str(raw.get("motion_repeat","once") or "once").lower()
            if motion_repeat not in ("once","loop"):motion_repeat="once"
            # FIX129 continuous-distance motion is bounded and visual-only.  The
            # authored FX moves from a start offset to start+distance during one
            # finite travel duration.  ``motion_repeat`` repeats animation frames
            # inside that path; it never creates an infinite gameplay attack.
            out[state]={"effect_asset":effect_asset,"effect_state":effect_state,"trigger_event":trigger_event,
                        "anchor":anchor,"offset_x":ox,"offset_y":oy,"scale":scale,"render_mode":render_mode,"motion":motion,
                        "world_width_tiles":num("world_width_tiles",0.,0.,32.),"world_height_tiles":num("world_height_tiles",0.,0.,32.),
                        "forward_tiles":num("forward_tiles",0.,-16.,16.),"follow_owner":bool(raw.get("follow_owner",motion=="follow")),
                        "pixel_density_mode":pixel_density_mode,"pixel_density_scale":num("pixel_density_scale",1.,.25,2.5),
                        "segments":segments,
                        "segment_playback":(str(raw.get("segment_playback","phase") or "phase").lower() if str(raw.get("segment_playback","phase") or "phase").lower() in ("phase","clip","full_clip","animate") else "phase"),
                        "segment_start_frame":int(num("segment_start_frame",0.,0.,192.)),
                        "segment_clip_frames":int(num("segment_clip_frames",0.,0.,192.)),
                        "segment_source_fps":num("segment_source_fps",0.,0.,60.),
                        "world_motion_mode":world_motion_mode,"motion_repeat":motion_repeat,
                        "travel_start_delay":num("travel_start_delay",0.,0.,4.),
                        "travel_start_x_tiles":num("travel_start_x_tiles",0.,-16.,16.),
                        "travel_start_y_tiles":num("travel_start_y_tiles",0.,-16.,16.),
                        "travel_distance_tiles":num("travel_distance_tiles",4.,-32.,32.),
                        "travel_y_tiles":num("travel_y_tiles",0.,-16.,16.),
                        "travel_duration":num("travel_duration",1.,.05,8.)}
        return out

    # FIX127 ---------------------------------------------------------------
    # Effect-frame artwork and world-space choreography are two independent
    # authoring tracks.  A direct boss ``effect.*`` asset can discover the
    # creature attack state that consumes it, then read/write the exact
    # ``combat_bindings`` row used by gameplay.  This is intentionally kept in
    # the model so UIKit never needs to guess file names or mutate JSON itself.
    def effect_gameplay_owner(self, asset_id):
        aid=str(asset_id or "")
        if not aid.startswith("effect."):
            return "", ""
        row=self.editor_manifest.get(aid,{}) if isinstance(self.editor_manifest,dict) else {}
        owner=str(row.get("linked_owner_asset","") or "") if isinstance(row,dict) else ""
        state=str(row.get("linked_owner_state","") or "") if isinstance(row,dict) else ""
        if not owner and isinstance(row,dict):
            shared=row.get("shared_by",())
            if isinstance(shared,(list,tuple)) and shared:
                owner=str(shared[0] or "")
        if state not in ("attack","special"):
            if aid.endswith("_special"):state="special"
            elif aid.endswith("_attack"):state="attack"
        if not owner and state:
            suffix="_"+state
            stem=aid.split(".",1)[-1]
            if stem.endswith(suffix):
                owner="creature."+stem[:-len(suffix)]
        if not owner.startswith("creature.") or state not in ("attack","special"):
            return "", ""
        return owner,state

    def effect_gameplay_motion(self, asset_id):
        owner,state=self.effect_gameplay_owner(asset_id)
        if not owner:return None
        path=self._pixel_source_path(owner)
        try:
            with open(path,"r",encoding="utf-8") as f:data=json.load(f)
        except Exception:return None
        rows=data.get("combat_bindings",{}) if isinstance(data,dict) else {}
        raw=rows.get(state,{}) if isinstance(rows,dict) else {}
        if not isinstance(raw,dict):return None
        normalized=self._normalize_combat_bindings({state:raw}).get(state,{})
        if not isinstance(normalized,dict):return None
        # Preserve exact editable segment ordering and extra future-safe fields
        # while still returning bounded numeric defaults for missing data.
        exact=copy.deepcopy(raw)
        exact.update({k:v for k,v in normalized.items() if k not in ("segments",)})
        if isinstance(raw.get("segments"),list):
            exact["segments"]=copy.deepcopy(raw.get("segments",[])[:16])
        else:exact["segments"]=[]
        return {"owner_asset":owner,"owner_state":state,"binding":exact,"source_path":path}

    def save_effect_gameplay_motion(self, asset_id, binding):
        owner,state=self.effect_gameplay_owner(asset_id)
        if not owner or state not in ("attack","special"):
            raise ValueError("此效果素材沒有可編輯的 BOSS／生物遊戲綁定")
        path=self._pixel_source_path(owner)
        with open(path,"r",encoding="utf-8") as f:data=json.load(f)
        if not isinstance(data,dict):raise ValueError("生物素材資料格式無效")
        rows=data.setdefault("combat_bindings",{})
        old=rows.get(state,{}) if isinstance(rows.get(state),dict) else {}
        incoming=copy.deepcopy(binding if isinstance(binding,dict) else {})
        # Never let the motion panel silently point gameplay at another image.
        incoming["effect_asset"]=str(old.get("effect_asset") or asset_id)
        incoming["effect_state"]=str(old.get("effect_state") or "effect")
        incoming["render_mode"]="authored"
        normalized=self._normalize_combat_bindings({state:incoming}).get(state,{})
        if not isinstance(normalized,dict):raise ValueError("FX 運動設定無效")
        # Keep explicit effect identity/trigger and the complete sanitized motion
        # schema.  Segment rows are copied in display order and limited to 16.
        result=copy.deepcopy(old)
        for key in ("effect_asset","effect_state","trigger_event","anchor","offset_x","offset_y","scale","render_mode","motion",
                    "world_width_tiles","world_height_tiles","forward_tiles","follow_owner","pixel_density_mode","pixel_density_scale",
                    "segment_playback","segment_start_frame","segment_clip_frames","segment_source_fps",
                    "world_motion_mode","motion_repeat","travel_start_delay","travel_start_x_tiles","travel_start_y_tiles",
                    "travel_distance_tiles","travel_y_tiles","travel_duration"):
            if key in normalized:result[key]=normalized[key]
        segments=[]
        for seg in incoming.get("segments",[]) if isinstance(incoming.get("segments"),list) else []:
            if not isinstance(seg,dict):continue
            clean={}
            for key in ("delay","phase","frame","hold","dx_tiles","dy_tiles","scale","start_frame","clip_frames"):
                if key not in seg:continue
                try:value=float(seg.get(key))
                except Exception:continue
                if key in ("frame","start_frame","clip_frames"):value=int(round(value))
                clean[key]=value
            if clean:segments.append(clean)
            if len(segments)>=16:break
        result["segments"]=segments
        rows[state]=result
        tmp=path+".fix127_tmp"
        try:
            with open(tmp,"w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False,separators=(",",":"));f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):
                try:os.remove(tmp)
                except Exception:pass
        return self.effect_gameplay_motion(asset_id)

    def _default_weapon_animations(self, asset_id, base_pixels):
        """Editable starting clips matching each built-in weapon's intent.

        These are authoring defaults only. Runtime keeps the proven procedural
        path until the artist presses Save for the weapon asset. Once saved,
        these frames/keyframes become authoritative and can be changed locally.
        """
        wid=str(asset_id).split(".",1)[1] if "." in str(asset_id) else str(asset_id)
        normal_angles={
            "sword":(-22,-4,18,42), "dagger":(-8,8,-2,15),
            "spear":(-3,-1,1,2), "battle_axe":(-24,-8,10,28),
            "war_hammer":(-30,-8,25,58), "greatsword":(-38,-12,18,52),
            "energy_bow":(-8,-3,2,7), "whip":(-42,-18,8,28),
            "laser_gun":(-4,-1,2,4),
            "flying_drone":(-5,-2,2,5),
        }.get(wid,(-20,0,20,40))
        heavy_angles={
            "sword":(-68,-42,-12,24,58,92),
            "dagger":(-18,10,-12,18,-5,28),
            "spear":(-10,-7,-4,-1,1,2,2,1),
            "battle_axe":(-18,-10,-3,5,12,20),
            "war_hammer":(-72,-52,-25,12,58,102),
            "greatsword":(-78,-48,-16,24,66,108),
            "energy_bow":(-82,-78,-74,-70,-66,-62,-58,-54),
            "whip":(-150,-96,-38,20,84,146,205,255),
            "laser_gun":(-18,-12,-5,4,12,18,10,2),
            "flying_drone":(-8,-5,-2,2,5,8,4,0),
        }.get(wid,(-60,-30,0,30,60,90))
        normal=[]; heavy=[]
        for i,a in enumerate(normal_angles):
            sx=(i-1) if wid=="spear" else 0
            normal.append(self._transform_weapon_frame(base_pixels,a,shift_x=sx))
        for i,a in enumerate(heavy_angles):
            # Keep the entire 24px spear silhouette inside its authoring
            # canvas.  World-space thrust distance is a separate track; large
            # per-frame bitmap shifts used to clip the final three frames.
            sx=(i-3) if wid=="spear" else 0
            heavy.append(self._transform_weapon_frame(base_pixels,a,shift_x=sx))
        charge=[
            self._transform_weapon_frame(base_pixels,0),
            self._transform_weapon_frame(base_pixels,heavy_angles[0]*0.55),
            self._transform_weapon_frame(base_pixels,heavy_angles[0]),
        ]
        normal_fx=self._default_weapon_effect_frames(wid,False,5)
        # The greatsword projectile uses a near one-second 12 FPS flipbook so
        # custom moon-blade art can cover its whole authoritative lifetime.
        heavy_count={"greatsword":12,"spear":8,"energy_bow":12,"whip":8,"laser_gun":10,"flying_drone":10}.get(wid,6)
        heavy_fx=self._default_weapon_effect_frames(wid,True,heavy_count)
        heavy_events={"action":3,"effect":3}
        if wid=="war_hammer":heavy_events["impact"]=3
        elif wid=="battle_axe":heavy_events["impact"]=3
        elif wid=="greatsword":heavy_events["projectile"]=3
        elif wid in ("energy_bow","laser_gun"):heavy_events["projectile"]=3
        normal_events={"action":2,"effect":2}
        if wid in ("energy_bow","laser_gun"):normal_events["projectile"]=2
        return {
            "idle":{"fps":4.0,"loop":True,"frames":[[[str(c) for c in row] for row in base_pixels]],"events":{}},
            "normal_attack":{"fps":10.0,"loop":False,"frames":normal,"events":normal_events},
            "heavy_charge":{"fps":8.0,"loop":True,"frames":charge,"events":{"loop_start":2,"loop_end":2}},
            "heavy_attack":{"fps":10.0,"loop":False,"frames":heavy,"events":heavy_events},
            "normal_effect":{"fps":12.0,"loop":False,"canvas_size":DEFAULT_VFX_CANVAS_SIZE,"frames":normal_fx,"events":{},"frame_events":{}},
            "heavy_effect":{"fps":12.0,"loop":False,"canvas_size":DEFAULT_VFX_CANVAS_SIZE,"frames":heavy_fx,"events":{},"frame_events":{}},
        }

    @staticmethod
    def _normalize_frame(frame,size):
        n=max(1,int(size)); out=_blank(n)
        if not isinstance(frame,list):return out
        for y in range(min(n,len(frame))):
            row=frame[y] if isinstance(frame[y],list) else []
            for x in range(min(n,len(row))):out[y][x]=str(row[x])
        return out

    @staticmethod
    def _normalize_animation_events(events, frame_count):
        """Normalize frame-bound animation events.

        Events are intentionally tiny frame markers. FIX17 uses the generic
        names ``link``, ``loop_start``, ``loop_end`` and ``action`` on every
        animation while preserving ``takeoff`` for jump. They never contain
        pixel data, so runtime can synchronize visual transitions/gameplay
        actions without coupling physics to editor internals.
        """
        out={}
        count=max(0,int(frame_count))
        if count<=0 or not isinstance(events,dict):
            return out
        for name,value in events.items():
            try:index=int(value)
            except Exception:continue
            out[str(name)]=max(0,min(count-1,index))
        return out

    @staticmethod
    def _normalize_frame_events(frame_events, frame_count):
        """Preserve per-frame FX metadata without mixing it with single markers.

        FIX120 uses this for editable VFX damage/segment frames.  Keys are frame
        indexes and values are tiny JSON dictionaries; pixel data never lives
        here. Unknown scalar fields are retained so later editor versions can
        extend the metadata without destroying current projects.
        """
        out={}
        count=max(0,int(frame_count))
        if count<=0 or not isinstance(frame_events,dict):
            return out
        for key,meta in frame_events.items():
            try:index=max(0,min(count-1,int(key)))
            except Exception:continue
            if not isinstance(meta,dict):continue
            clean={}
            for name,value in meta.items():
                if isinstance(value,(bool,int,float,str)) or value is None:
                    clean[str(name)]=value
            if clean:out[str(index)]=clean
        return out

    @classmethod
    def _normalize_animations(cls, animations, size):
        out = {}
        if not isinstance(animations, dict):
            return out
        for state, raw in animations.items():
            if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list) or not raw["frames"]:
                continue
            frames = raw["frames"]; state = str(state)
            measured = max(max(_grid_extent(f)) for f in frames)
            is_vfx = state in EFFECT_STATES
            if is_vfx:
                default_size = measured if measured in VFX_PIXEL_SIZES or measured > DEFAULT_VFX_CANVAS_SIZE else DEFAULT_VFX_CANVAS_SIZE
            else:
                default_size = int(size)
            frame_size = _checked_canvas_size(max(measured, int(raw.get("canvas_size") or default_size)))
            normalized = []
            for frame in frames:
                src = _native_grid(frame)
                # VFX pivots are centred; ordinary sparse/malformed sources
                # retain their original top-left coordinates, never trim edges.
                normalized.append(_resize_canvas(src, frame_size, "weapon") if is_vfx and len(src) != frame_size else cls._normalize_frame(src, frame_size))
            try: fps = max(1.0, min(30.0, float(raw.get("fps", 6.0) or 6.0)))
            except (ValueError, TypeError): fps = 6.0
            result = dict(raw)
            result.update({"fps":fps, "loop":bool(raw.get("loop", state not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect"))),
                           "frames":normalized, "events":cls._normalize_animation_events(raw.get("events",{}),len(normalized)),
                           "frame_events":cls._normalize_frame_events(raw.get("frame_events",{}),len(normalized))})
            if is_vfx or "canvas_size" in raw or frame_size != int(size):
                result["canvas_size"] = frame_size
            out[state] = result
        return out

    @classmethod
    def _resample_animations(cls, animations, new_size):
        out={}
        for state,raw in (animations or {}).items():
            frames=[_resample(frame,new_size) for frame in raw.get("frames",[])]
            if frames:
                out[str(state)]={
                    "fps":float(raw.get("fps",6.0)),
                    "loop":bool(raw.get("loop",str(state) not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect"))),
                    "frames":frames,
                    "events":cls._normalize_animation_events(raw.get("events",{}),len(frames)),
                    "frame_events":cls._normalize_frame_events(raw.get("frame_events",{}),len(frames)),
                }
        return out

    @classmethod
    def _resize_canvas_animations(cls, animations, new_size, category):
        out={}
        for state,raw in (animations or {}).items():
            state=str(state); is_vfx=(str(category)=="weapon" and state in EFFECT_STATES)
            if is_vfx:
                try:frame_size=int(raw.get("canvas_size",len(raw.get("frames",[[]])[0])))
                except Exception:frame_size=DEFAULT_VFX_CANVAS_SIZE
                frame_size=_checked_canvas_size(max(frame_size,max((max(_grid_extent(f)) for f in raw.get("frames",[]) if isinstance(f,list) and f),default=1)))
                frames=[]
                for frame in raw.get("frames",[]):
                    if not isinstance(frame,list):continue
                    source_n=len(frame); source=cls._normalize_frame(frame,max(1,source_n))
                    frames.append(source if source_n==frame_size else _resize_canvas(source,frame_size,"weapon"))
            else:
                frame_size=max(int(new_size),int(raw.get("canvas_size",0) or 0),max((max(_grid_extent(f)) for f in raw.get("frames",[]) if isinstance(f,list) and f),default=1))
                _checked_canvas_size(frame_size)
                frames=[_resize_canvas(frame,frame_size,category) for frame in raw.get("frames",[]) if isinstance(frame,list)]
            if frames:
                out[state]={
                    "fps":float(raw.get("fps",6.0)),
                    "loop":bool(raw.get("loop",state not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect"))),
                    "frames":frames,
                    "events":cls._normalize_animation_events(raw.get("events",{}),len(frames)),
                    "frame_events":cls._normalize_frame_events(raw.get("frame_events",{}),len(frames)),
                }
                if is_vfx or frame_size!=int(new_size) or "canvas_size" in raw:out[state]["canvas_size"]=frame_size
        return out

    def default_animation_states(self, category):
        return {
            # FIX15: posture/motion clips are separate authored states.  A
            # crouched body is no longer produced by shrinking the ordinary
            # walk/run artwork. ``crawl`` is floor crawling; ``climb`` remains
            # ladder/wall climbing so the two motions cannot be confused.
            "player": (
                "idle","walk","run",
                "crouch","crouch_walk","crouch_run",
                "prone","crawl",
                "roll",
                "jump","fall","climb",
            ),
            "creature": ("idle","move","attack","special","hurt","death"),
            "plant": ("sway",),
            # Weapon authoring keeps art, pose animation and attack effects in
            # one asset. The effect states are played from keyframe bindings in
            # normal_attack/heavy_attack, so no code edits are needed later.
            "weapon": ("idle","normal_attack","heavy_charge","heavy_attack","normal_effect","heavy_effect"),
            # Equipment overlays mirror the player's state/frame index so the
            # shoes, armour and helmets stay locked to the authored body pose.
            "equipment": (
                "idle","walk","run",
                "crouch","crouch_walk","crouch_run",
                "prone","crawl","roll","jump","fall","climb",
            ),
            # Linked effect entries expose the parent's complete VFX flipbook
            # under one neutral state so the animation editor can edit every
            # effect frame without pretending the effect is another weapon.
            "effect": ("effect",),
        }.get(str(category),())

    def resample_pixels(self, pixels, new_size):
        # Kept only for legacy migration/import. Editor canvas changes use
        # resize_canvas_pixels so authored pixel density never changes.
        return _resample(pixels,new_size)

    def resample_preview_pixels(self,pixels,new_size):
        """Coverage-aware preview downsample for sparse VFX pixels.

        Nearest-only downsampling can miss every pixel in a thin knife, spiral
        or projectile trail. For editor preview only, choose the highest-alpha
        source cell covered by each destination cell. Saved art is untouched.
        """
        old_h=len(pixels) if isinstance(pixels,list) else 0
        old_w=len(pixels[0]) if old_h and isinstance(pixels[0],list) else 0
        n=max(1,int(new_size))
        if old_h<=0 or old_w<=0:return _blank(n)
        if max(old_h,old_w)<=n:return _resample(pixels,n)
        out=_blank(n)
        for y in range(n):
            y0=int(math.floor(y*old_h/n)); y1=max(y0+1,int(math.ceil((y+1)*old_h/n)))
            for x in range(n):
                x0=int(math.floor(x*old_w/n)); x1=max(x0+1,int(math.ceil((x+1)*old_w/n)))
                best=TRANSPARENT; best_alpha=-1
                for sy in range(y0,min(old_h,y1)):
                    row=pixels[sy] if isinstance(pixels[sy],list) else []
                    for sx in range(x0,min(old_w,x1,len(row))):
                        raw=str(row[sx] or TRANSPARENT); body=raw.lstrip("#")
                        try:alpha=int(body[6:8],16) if len(body)>=8 else 255
                        except Exception:alpha=0
                        if alpha>best_alpha:best=raw; best_alpha=alpha
                out[y][x]=best
        return out

    def resize_canvas_pixels(self, pixels, new_size, category):
        return _resize_canvas(pixels,new_size,category)

    def resize_canvas_animations(self, animations, new_size, category):
        return self._resize_canvas_animations(animations,new_size,category)

    @staticmethod
    def flood_fill_pixels(pixels, x, y, new_color):
        h=len(pixels); w=len(pixels[0]) if h else 0
        x=int(x); y=int(y)
        if not (0<=x<w and 0<=y<h): return
        target=pixels[y][x]; new_color=str(new_color)
        if target==new_color: return
        stack=[(x,y)]; seen=set()
        while stack:
            cx,cy=stack.pop()
            if (cx,cy) in seen or not (0<=cx<w and 0<=cy<h): continue
            seen.add((cx,cy))
            if pixels[cy][cx]!=target: continue
            pixels[cy][cx]=new_color
            stack.extend(((cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1)))

    def save_pixel_asset(self, asset_id, category, pixels, animations=None, combat_bindings=None, export_size_override=None):
        category=str(category); asset_id=str(asset_id)
        size=len(pixels)
        # FIX91: save linked effect editing back into the parent weapon's actual
        # animation state.  The optional effects PNG is only a lightweight
        # thumbnail; gameplay continues to consume the canonical weapon source.
        effect_meta=self.editor_manifest.get(asset_id,{}) if category=="effect" else {}
        parent_id=effect_meta.get("effect_source_asset") if isinstance(effect_meta,dict) and not bool(effect_meta.get("standalone_shared_effect",False)) else None
        effect_state=effect_meta.get("effect_source_state") if isinstance(effect_meta,dict) and parent_id else None
        if parent_id and effect_state:
            _checked_canvas_size(size)
            if any(not isinstance(r,list) or len(r)!=size for r in pixels):
                raise ValueError("請先以來源尺寸載入完整畫布再儲存")
            from systems.editor_catalog import safe_path
            parent_binding=self.data.get("bindings",{}).get(str(parent_id),{})
            src_rel=parent_binding.get("pixel_source") if isinstance(parent_binding,dict) else None
            if not src_rel:
                src_rel="pixel_sources/"+_slug(str(parent_id).replace(".","_"))+".json"
            source=safe_path(self.assets_root,src_rel)
            with open(source,"r",encoding="utf-8") as f:
                parent=json.load(f)
            states=parent.setdefault("animations",{})
            old=states.get(str(effect_state),{})
            old=old if isinstance(old,dict) else {}
            incoming=animations.get("effect") if isinstance(animations,dict) else None
            if isinstance(incoming,dict) and isinstance(incoming.get("frames"),list) and incoming.get("frames"):
                frames=[]
                for frame in incoming.get("frames",[]):
                    frames.append(self._normalize_frame(frame,size))
                newraw=dict(old)
                newraw.update({
                    "fps":float(incoming.get("fps",old.get("fps",12.0))),
                    "loop":bool(incoming.get("loop",old.get("loop",False))),
                    "frames":frames,
                    "events":self._normalize_animation_events(incoming.get("events",old.get("events",{})),len(frames)),
                    "frame_events":self._normalize_frame_events(incoming.get("frame_events",old.get("frame_events",{})),len(frames)),
                    "canvas_size":size,
                })
            else:
                frames=list(old.get("frames",[])) if isinstance(old.get("frames"),list) else []
                if frames: frames[0]=self._normalize_frame(pixels,size)
                else: frames=[self._normalize_frame(pixels,size)]
                newraw=dict(old); newraw.update({"frames":frames,"canvas_size":size})
                newraw.setdefault("fps",12.0); newraw.setdefault("loop",False); newraw.setdefault("events",{}); newraw.setdefault("frame_events",{})
            states[str(effect_state)]=newraw
            tmp=source+".tmp"
            with open(tmp,"w",encoding="utf-8") as f:
                json.dump(parent,f,ensure_ascii=False,separators=(",",":"))
            os.replace(tmp,source)
            folder=os.path.join(self.assets_root,"effects"); os.makedirs(folder,exist_ok=True)
            png_abs=os.path.join(folder,_slug(asset_id.replace(".","_"))+".png")
            effect_binding=self.data.get("bindings",{}).get(asset_id,{})
            export_size=int(export_size_override or (effect_binding.get("export_size") if isinstance(effect_binding,dict) else None) or old.get("export_size") or size)
            if export_size_override is not None: export_size=int(export_size_override)
            self._write_png(png_abs,_nearest_export_grid(newraw["frames"][0],export_size))
            row={"category":"effect","file":os.path.relpath(png_abs,self.assets_root).replace(os.sep,"/"),
                 "linked_effect_asset":str(parent_id),"linked_effect_state":str(effect_state),
                 "pixel_size":size,"logical_size":size,"export_size":export_size,
                 "export_scale":float(export_size)/max(1,size),"has_animations":True}
            return png_abs,row
        _checked_canvas_size(size)
        if any(not isinstance(r,list) or len(r)!=size for r in pixels):
            raise ValueError("畫布資料尺寸不一致，未儲存")
        mapping = TILE_TEXTURE_MAPPING if category=="tile" and size!=16 else PIXEL_MAPPING
        # A high-resolution tile is still one terrain cell, not a larger collider.
        world_scale = 40.0 / size if mapping == TILE_TEXTURE_MAPPING else float(PIXEL_WORLD_SCALE)
        folder=next((row[2] for row in CATEGORIES if row[0]==category),None)
        if folder is None: raise ValueError("未知素材類別："+category)
        source=self._pixel_source_path(asset_id)
        try:
            with open(source,"r",encoding="utf-8") as f:old_payload=json.load(f)
            if not isinstance(old_payload,dict):old_payload={}
        except Exception:
            old_payload={}
        old_row=self.data.get("bindings",{}).get(asset_id,{})
        old_row=old_row if isinstance(old_row,dict) else {}
        if category!="tile" and old_payload:
            old_mapping=str(old_payload.get("pixel_mapping",old_payload.get("mapping","")) or "legacy_normalized")
            if old_mapping!=PIXEL_MAPPING:
                mapping=old_mapping
        # Validate every incoming state BEFORE replacing even the PNG preview.
        checked_animations=self._normalize_animations(old_payload.get("animations",{}) if animations is None else animations,size)
        if export_size_override is not None:
            export_size=int(export_size_override)
        else:
            export_size=int(old_payload.get("export_size",old_row.get("export_size",size)) or size)
            export_size=max(size,export_size)
        _checked_canvas_size(export_size)
        export_scale=float(export_size)/max(1,size)
        os.makedirs(os.path.join(self.assets_root,folder),exist_ok=True)
        png_name=_slug(asset_id.replace(".","_"))+".png"
        png_abs=os.path.join(self.assets_root,folder,png_name)
        self._write_png(png_abs,_nearest_export_grid(pixels,export_size))
        rel=os.path.relpath(png_abs,self.assets_root).replace(os.sep,"/")
        row={
            "category":category,"file":rel,"source_name":"builtin-pixel-editor",
            "frame_width":0,"frame_height":0,"frame_index":0,
            "pixel_source":os.path.relpath(self._pixel_source_path(asset_id),self.assets_root).replace(os.sep,"/"),
            "pixel_size":size,
            "logical_size":size,
            "export_size":export_size,
            "export_scale":export_scale,
            "pixel_mapping":mapping,
            "pixel_world_scale":world_scale,
            "world_fit":str(old_payload.get("world_fit",old_row.get("world_fit","") if isinstance(old_row,dict) else "") or ""),
        }
        row.update(self._binding_defaults(category))
        # FIX71 generated creatures carry stable expansion provenance in the
        # registry.  An art-only save must not discard that marker because the
        # offline validator and future catalog migrations use it to distinguish
        # the 50-piece roster from unrelated user assets.
        if category=="creature" and "generation" in old_row:
            row["generation"]=old_row.get("generation")
        equipment_overlay={}
        equipment_metadata={}
        if category=="equipment":
            raw_overlay=old_payload.get("overlay")
            if not isinstance(raw_overlay,dict):raw_overlay=old_row.get("overlay",{})
            equipment_overlay=raw_overlay if isinstance(raw_overlay,dict) else {}
            raw_equipment=old_payload.get("equipment",{})
            equipment_metadata=raw_equipment if isinstance(raw_equipment,dict) else {}
            if "icon_file" in old_row:row["icon_file"]=old_row.get("icon_file")
            if "equipment_slot" in old_row:
                row["equipment_slot"]=old_row.get("equipment_slot")
            else:
                slot=equipment_metadata.get("slot",equipment_overlay.get("slot"))
                if slot is not None:row["equipment_slot"]=slot
            row["overlay"]=equipment_overlay
        # Preserve existing animations when saving only the Base image.
        if animations is None:
            animations=old_payload.get("animations",{})
        animations=checked_animations
        if combat_bindings is None:
            combat_bindings=old_payload.get("combat_bindings",{})
        combat_bindings=self._normalize_combat_bindings(combat_bindings)
        if category=="weapon" and not combat_bindings:combat_bindings=self._default_combat_bindings(asset_id)
        # Creature bindings are authored explicitly by VFX-track controls and
        # preserved here; no species-specific Python branch is required.
        row["has_animations"]=bool(animations)
        row["has_combat_bindings"]=bool(combat_bindings)
        row["animation_states"]=sorted(animations.keys())
        row = dict(old_row, **row)
        self.data.setdefault("bindings",{})[asset_id]=row
        payload={
            "format":"pyto_rpg_pixel_asset","version":PIXEL_SOURCE_FORMAT,
            "asset_id":asset_id,"category":category,"size":size,
            "logical_size":size,"export_size":export_size,"export_scale":export_scale,
            "pixel_mapping":mapping,
            "pixel_world_scale":world_scale,
            "world_fit":str(old_payload.get("world_fit",old_row.get("world_fit","") if isinstance(old_row,dict) else "") or ""),
            "pixels":pixels,"animations":animations,"combat_bindings":combat_bindings,
        }
        payload = dict(old_payload, **payload)
        if category=="creature":
            # Preserve opaque data-driven attack/VFX linkage.  Gameplay owns
            # these catalogs; the pixel editor merely keeps their provenance
            # losslessly while changing artwork or animation frames.
            if isinstance(old_payload.get("authoring"),dict):
                payload["authoring"]=old_payload.get("authoring")
            if old_payload.get("auto_animation_profile") is not None:
                payload["auto_animation_profile"]=old_payload.get("auto_animation_profile")
        if category=="equipment":
            payload["overlay"]=equipment_overlay
            payload["equipment"]=equipment_metadata
        # Write the pixel source first, then touch registry last. Runtime hot
        # reload therefore never observes a new registry with an old frame file.
        tmp=source+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
        os.replace(tmp,source)
        control_path=self.editor_manifest.get(asset_id,{}).get("control_file")
        if control_path:
            from systems.editor_catalog import safe_path
            self._write_png(safe_path(self.assets_root,control_path),_nearest_export_grid(pixels,export_size))
        self.save()
        return png_abs,row

    @staticmethod
    def _png_chunk(kind, payload):
        body=kind+payload
        return struct.pack(">I",len(payload))+body+struct.pack(">I",zlib.crc32(body)&0xffffffff)

    def _write_png(self,path,pixels):
        h=len(pixels); w=len(pixels[0]) if h else 0
        if w<=0 or h<=0: raise ValueError("空白畫布")
        raw=bytearray()
        for row in pixels:
            raw.append(0)  # PNG filter None
            for c in row[:w]: raw.extend(_rgba_bytes(c))
        data=b"\x89PNG\r\n\x1a\n"
        data+=self._png_chunk(b"IHDR",struct.pack(">IIBBBBB",w,h,8,6,0,0,0))
        data+=self._png_chunk(b"IDAT",zlib.compress(bytes(raw),9))
        data+=self._png_chunk(b"IEND",b"")
        tmp=path+".tmp"
        with open(tmp,"wb") as f:f.write(data)
        os.replace(tmp,path)

    def unbind(self, asset_id, delete_file=False):
        row = self.data.setdefault("bindings", {}).pop(str(asset_id), None)
        if row and delete_file:
            rel = str(row.get("file", "")); path = os.path.join(self.assets_root, *rel.split("/"))
            try: os.remove(path)
            except Exception: pass
        self.save(); return row

    def bindings(self):
        return sorted(self.data.get("bindings", {}).items())
