# -*- coding: utf-8 -*-
import json
import os
import re

from player.action_profile import (
    DEFAULT_ACTION_SETTINGS,
    load_action_settings,
    normalize_action_settings,
    save_action_settings,
)
from systems.creature_action_profile import (
    MOTION_TYPES, MOTION_LABELS, EFFECT_TYPES, EFFECT_LABELS,
    default_action, load_creature_actions, normalize_data, save_creature_actions,
)


NUMERIC_ROWS = (
    ("walk_speed", "走路速度", 5.0, 0),
    ("run_speed", "跑步速度", 5.0, 0),
    ("crouch_speed_scale", "蹲走倍率", 0.05, 2),
    ("prone_speed_scale", "趴行倍率", 0.05, 2),
    ("jump_speed", "跳躍初速", 10.0, 0),
    ("jump_height_px", "跳躍高度（px）", 2.0, 0),
    ("roll_speed", "翻滾速度", 10.0, 0),
    ("roll_time", "翻滾時間（秒）", 0.02, 2),
)

TOGGLE_ROWS = (
    ("direct_jump_from_compact", "蹲／趴按跳直接起跳", "開：不先切站姿，直接進跳躍"),
    ("restore_posture_after_jump", "落地恢復起跳前姿態", "開：蹲跳回蹲、趴跳回趴"),
    ("up_restores_stand", "按上恢復站姿", "開：只有方向上負責蹲／趴→站立"),
)

# key, Chinese label, step, digits
CREATURE_NUMERIC_ROWS = (
    ("range_min", "最短觸發距離", 10.0, 0),
    ("range_max", "最遠觸發距離", 10.0, 0),
    ("vertical_range", "垂直容許距離", 10.0, 0),
    ("cooldown", "冷卻（秒）", 0.10, 2),
    ("damage", "傷害", 1.0, 0),
    ("speed", "動作速度", 10.0, 0),
    ("lift_speed", "跳躍／上抬速度", 10.0, 0),
    ("duration", "持續（0=動畫）", 0.05, 2),
    ("knockback", "擊退", 10.0, 0),
    ("hit_radius", "命中半徑", 2.0, 0),
    ("priority", "AI 優先級", 1.0, 0),
    ("chance", "AI 選用機率", 0.10, 2),
)


def _slug(name):
    base=os.path.splitext(os.path.basename(str(name)))[0].strip().lower()
    base=re.sub(r"[^0-9a-zA-Z_\-]+","_",base).strip("_")
    return base or "asset"


class ActionEditorModel:
    def __init__(self, project_root):
        self.project_root=os.path.abspath(project_root)
        self.data=load_action_settings(self.project_root)
        self.creature_data=load_creature_actions(self.project_root)
        self._creature_ids=self._load_creature_ids()
        self._creature_names=self._load_custom_creature_names()

    # ---------------- player settings ----------------
    def reload(self):
        self.data=load_action_settings(self.project_root)
        self.creature_data=load_creature_actions(self.project_root)
        self._creature_ids=self._load_creature_ids()
        self._creature_names=self._load_custom_creature_names()
        return self.data

    def reset_defaults(self):
        self.data=normalize_action_settings(DEFAULT_ACTION_SETTINGS)
        return self.data

    def adjust(self,key,delta):
        current=float(self.data.get(key,DEFAULT_ACTION_SETTINGS[key]))
        self.data[key]=current+float(delta)
        self.data=normalize_action_settings(self.data)
        return self.data[key]

    def toggle(self,key):
        self.data[key]=not bool(self.data.get(key,False))
        self.data=normalize_action_settings(self.data)
        return bool(self.data[key])

    # ---------------- creature action settings ----------------
    def _load_creature_ids(self):
        path=os.path.join(self.project_root,"assets","binding_catalog.json")
        try:
            with open(path,"r",encoding="utf-8") as f:data=json.load(f)
            rows=data.get("categories",{}).get("creature",[])
            rows=[str(x) for x in rows if str(x).startswith("creature.")]
            if rows:return rows
        except Exception:pass
        return ["creature.boar"]

    def creature_ids(self):
        return list(self._creature_ids)

    def _load_custom_creature_names(self):
        path=os.path.join(self.project_root,"assets","custom_creatures.json")
        try:
            with open(path,"r",encoding="utf-8") as f:data=json.load(f)
            rows=data.get("creatures",{}) if isinstance(data,dict) else {}
            if not isinstance(rows,dict):return {}
            return {
                "creature."+str(species):str(row.get("name") or species)
                for species,row in rows.items() if isinstance(row,dict)
            }
        except Exception:return {}

    def creature_display_name(self,asset_id):
        # Short built-in translations; unknown/custom species remain readable.
        custom=self._creature_names.get(str(asset_id))
        if custom:return custom
        labels={
            "boar":"野豬","slime":"史萊姆","wolf":"狼","deer":"鹿","gull":"海鷗",
            "shore_crab":"岸蟹","sea_turtle":"海龜","heron":"鷺鳥","otter":"水獺",
            "crocodile":"鱷魚","jaguar":"美洲豹","snow_wolf":"雪狼","mountain_goat":"山羊",
            "kingfisher":"翠鳥","dragonfly":"蜻蜓","damselfly":"豆娘","bandit":"強盜",
            "village_dog":"村犬","villager":"村民",
            "cave_bat":"洞穴蝙蝠","cave_spider":"洞穴巨蛛","zombie":"殭屍",
            "giant_worm":"巨型蠕蟲","dungeon_bandit_mage":"地下城魔法強盜",
            "creeper":"苦力帕","blue_poop":"藍色大便","giant_bago_bird":"彩色巨型巴戈鳥",
            "fire_zombie":"火焰殭屍","lightning_zombie":"閃電殭屍","ghost":"幽靈",
            "toilet_man":"馬桶人","speaker_man":"音響人","abyss_colossus":"深淵巨像 Boss",
            "flying_imp":"飛行小惡魔","infernal_goat":"煉獄羊","burning_slime":"燃燒史萊姆",
            "flame_turtle":"火炎龜","exploding_wisp":"自爆鬼火","crystal_knight":"水晶騎士",
            "crystal_slime":"水晶史萊姆","crystal_skeleton":"水晶骷髏怪","crystal_lizard":"水晶蜥蜴",
        }
        tail=str(asset_id).split(".",1)[-1]
        return labels.get(tail,tail.replace("_"," "))

    def animation_states(self,asset_id):
        # Read the same editable source that the asset editor writes.  AI can
        # therefore discover newly named states without Python changes.
        path=os.path.join(self.project_root,"assets","pixel_sources",_slug(str(asset_id).replace(".","_"))+".json")
        rows=[]
        try:
            with open(path,"r",encoding="utf-8") as f:data=json.load(f)
            anims=data.get("animations",{})
            if isinstance(anims,dict):rows=[str(x) for x in anims.keys()]
        except Exception:pass
        ordered=[]
        for name in ("idle","move")+tuple(rows):
            if name not in ordered:ordered.append(name)
        return ordered

    def creature_entry(self,asset_id,create=True):
        creatures=self.creature_data.setdefault("creatures",{})
        entry=creatures.get(str(asset_id))
        if not isinstance(entry,dict):
            if not create:return None
            entry={"override_legacy_contact":False,"actions":{}}
            creatures[str(asset_id)]=entry
        entry.setdefault("override_legacy_contact",False); entry.setdefault("actions",{})
        return entry

    @staticmethod
    def _creature_action_key(entry,state):
        """Resolve an animation state to its authored semantic action id.

        Older actions usually used the animation name as their dictionary key.
        FIX71 keeps unique descriptive ids (for example ``cinder_burrow``)
        while all of them intentionally play the ``attack`` clip.  The editor
        must therefore consult animation_state instead of silently creating a
        second, empty action named ``attack``.
        """
        state=str(state)
        actions=entry.get("actions",{}) if isinstance(entry,dict) else {}
        if not isinstance(actions,dict):return state
        if state in actions:return state
        matches=[]
        for action_id,row in actions.items():
            if isinstance(row,dict) and str(row.get("animation_state",action_id) or action_id)==state:
                matches.append(str(action_id))
        return sorted(matches)[0] if matches else state

    def creature_action(self,asset_id,state,create=False):
        entry=self.creature_entry(asset_id,create=create)
        if not entry:return None
        actions=entry.setdefault("actions",{})
        key=self._creature_action_key(entry,state)
        raw=actions.get(key)
        if raw is None and create:
            raw=default_action(str(state)); actions[str(state)]=raw
            # First custom gameplay action should take responsibility for combat
            # instead of silently allowing the old contact bite to bypass it.
            entry["override_legacy_contact"]=True
        return raw

    def set_creature_enabled(self,asset_id,state,enabled):
        if enabled:
            action=self.creature_action(asset_id,state,create=True); action["enabled"]=True
        else:
            action=self.creature_action(asset_id,state,create=False)
            if action is not None:action["enabled"]=False
        self.creature_data=normalize_data(self.creature_data)

    def adjust_creature(self,asset_id,state,key,delta):
        action=self.creature_action(asset_id,state,create=True)
        action[key]=float(action.get(key,0.0))+float(delta)
        self.creature_data=normalize_data(self.creature_data)
        return self.creature_action(asset_id,state,create=True).get(key)

    def cycle_motion(self,asset_id,state,direction=1):
        action=self.creature_action(asset_id,state,create=True)
        current=str(action.get("motion",MOTION_TYPES[0]))
        idx=MOTION_TYPES.index(current) if current in MOTION_TYPES else 0
        action["motion"]=MOTION_TYPES[(idx+(1 if direction>=0 else -1))%len(MOTION_TYPES)]
        self.creature_data=normalize_data(self.creature_data)
        return action["motion"]

    def cycle_effect(self,asset_id,state,direction=1):
        action=self.creature_action(asset_id,state,create=True)
        current=str(action.get("effect",EFFECT_TYPES[0]))
        idx=EFFECT_TYPES.index(current) if current in EFFECT_TYPES else 0
        action["effect"]=EFFECT_TYPES[(idx+(1 if direction>=0 else -1))%len(EFFECT_TYPES)]
        self.creature_data=normalize_data(self.creature_data)
        return action["effect"]

    def toggle_creature_flag(self,asset_id,state,key):
        if key=="override_legacy_contact":
            entry=self.creature_entry(asset_id,create=True)
            entry[key]=not bool(entry.get(key,False))
        else:
            action=self.creature_action(asset_id,state,create=True)
            action[key]=not bool(action.get(key,False))
        self.creature_data=normalize_data(self.creature_data)

    def delete_creature_action(self,asset_id,state):
        entry=self.creature_entry(asset_id,create=False)
        if not entry:return False
        actions=entry.get("actions",{})
        key=self._creature_action_key(entry,state)
        if key in actions:
            actions.pop(key,None)
            return True
        return False

    def save(self):
        player_path,self.data=save_action_settings(self.data,self.project_root)
        creature_path,self.creature_data=save_creature_actions(self.creature_data,self.project_root)
        return player_path,creature_path


__all__=[
    "ActionEditorModel","NUMERIC_ROWS","TOGGLE_ROWS","CREATURE_NUMERIC_ROWS",
    "MOTION_TYPES","MOTION_LABELS","EFFECT_TYPES","EFFECT_LABELS",
]
