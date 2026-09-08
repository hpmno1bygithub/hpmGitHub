# -*- coding: utf-8 -*-
"""Project-local custom creature definitions created by the asset editor."""
import json, os

DEFAULT = dict(name="自訂生物", hp=80, attack=8, speed=48, w=36, h=32,
               hostile=False, locomotion="ground", habitat="plains", element="earth", loot=(),
               respawn_enabled=True, respawn_seconds=None)


def load_custom_creatures(project_root):
    path=os.path.join(os.path.abspath(project_root or "."),"assets","custom_creatures.json")
    try:
        with open(path,"r",encoding="utf-8") as f:data=json.load(f)
    except Exception:
        return {}
    raw=data.get("creatures",{}) if isinstance(data,dict) else {}
    out={}
    if not isinstance(raw,dict):return out
    for species,row in raw.items():
        if not isinstance(row,dict):continue
        cfg=dict(DEFAULT);cfg.update(row)
        cfg["name"]=str(cfg.get("name") or species)
        for k in ("hp","attack","speed","w","h"):
            try:cfg[k]=float(cfg[k])
            except Exception:cfg[k]=float(DEFAULT[k])
        cfg["hostile"]=bool(cfg.get("hostile",False))
        cfg["locomotion"]=str(cfg.get("locomotion","ground") or "ground")
        cfg["habitat"]=str(cfg.get("habitat","plains") or "plains")
        try:
            from systems.creature_attributes import normalize_element
            cfg["element"]=normalize_element(cfg.get("element","earth"),"earth")
        except Exception:
            cfg["element"]="earth"
        cfg["respawn_enabled"]=bool(cfg.get("respawn_enabled",True))
        raw_respawn=cfg.get("respawn_seconds")
        if raw_respawn is not None:
            try:cfg["respawn_seconds"]=max(0.25,min(3600.0,float(raw_respawn)))
            except Exception:cfg["respawn_seconds"]=None
        cfg["_custom"] = True
        loot=cfg.get("loot",())
        cfg["loot"]=tuple(tuple(x) for x in loot if isinstance(x,(list,tuple)) and len(x)>=3)
        out[str(species)]=cfg
    return out
