# -*- coding: utf-8 -*-
"""FIX90: shared, renderer-free discovery for both editors.

No image/animation frames are loaded while listing assets. Existing catalog
order is preserved, then runtime definitions, registry and loose sources merge.
"""
import json
import os
from collections import OrderedDict
from types import SimpleNamespace

CATEGORY_LABELS = {
    'tile':'地形圖塊','creature':'生物／魔物','plant':'植物','decoration':'場景裝飾',
    'player':'角色','weapon':'武器','equipment':'裝備','magic':'魔法素材',
    'item':'物品圖像','ui':'介面圖示','effect':'效果素材',
    'inventory':'背包物品圖像','background':'背景素材',
}


BACKGROUND_THEME_NAMES = {
    'ocean':'海洋遠景','lake':'湖泊遠景','swamp':'沼澤遠景','plains':'草原遠景','village':'村莊遠景',
    'rainforest':'雨林遠景','snow_mountain':'雪地高山遠景','desert':'沙漠遠景','underground':'地下遠景',
    'dungeon':'地下城遠景','magma':'岩漿區遠景','crystal':'水晶區遠景','pyramid':'金字塔遠景',
    'glow_moss':'發光苔蘚遠景','crystal_mine':'水晶礦坑遠景','underground_castle':'地下城堡遠景',
    'boss_dungeon':'BOSS 地牢遠景','magma_hell':'地獄岩漿遠景',
}

def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError, TypeError): return {} if default is None else default

def safe_path(root, rel):
    root=os.path.realpath(root); path=os.path.realpath(os.path.join(root,str(rel)))
    if os.path.commonpath((root,path)) != root: raise ValueError('素材路徑不可離開專案')
    return path

def all_archetypes(root):
    from systems.biome_system import CREATURE_ARCHETYPES
    from systems.custom_creatures import load_custom_creatures
    rows={k:dict(v) for k,v in CREATURE_ARCHETYPES.items()}
    rows.update(load_custom_creatures(root))
    from systems.secondary_world import SecondaryWorldRuntime
    folder=os.path.join(root,'maps')
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if not name.endswith('.json') or 'backup' in name: continue
        path=os.path.join(folder,name); payload=read_json(path)
        if not isinstance(payload,dict): continue
        try: SecondaryWorldRuntime.from_map_loader(root,SimpleNamespace(path=path,payload=payload)).install_archetypes(rows)
        except (ValueError,KeyError,TypeError,OSError): continue
    return rows

class EditorAssetCatalog:
    def __init__(self, root):
        self.root=os.path.abspath(root); self.assets=os.path.join(self.root,'assets')
        self.by_id=OrderedDict(); self.creatures=all_archetypes(self.root); self.loot={}
        self.reload()

    def reload(self):
        self.by_id.clear(); self.loot.clear()
        base=read_json(os.path.join(self.assets,'binding_catalog.json')).get('categories',{})
        names=read_json(os.path.join(self.assets,'editor_asset_manifest.json')).get('assets',{})
        registry=read_json(os.path.join(self.assets,'registry.json')).get('bindings',{})
        # Import labels only; AssetEditorModel is never instantiated here.
        from asset_editor.model import ASSET_NAMES_ZH
        def add(aid,label='',**extra):
            aid=str(aid)
            if '.' not in aid: return
            cat,slug=aid.split('.',1)
            if cat not in CATEGORY_LABELS: return
            meta=names.get(aid,{}) if isinstance(names,dict) else {}
            row=self.by_id.setdefault(aid,{'id':aid,'category':cat,
                'name':str(label or ASSET_NAMES_ZH.get(aid) or meta.get('name') or slug),
                'kind':'visual','value':aid,'color':'#6e8294','asset':aid})
            if label: row['name']=str(label)
            for k in ('name','color','file','size','default_source','control_file',
                      'effect_source_asset','effect_source_state','effect_canvas_size'):
                if k in meta: row[k]=meta[k]
            row.update(extra)
            if isinstance(registry.get(aid),dict):
                row['bound']=True
                if registry[aid].get('file'):row['file']=registry[aid]['file']
            return row
        for cat,ids in base.items() if isinstance(base,dict) else ():
            if isinstance(ids,list):
                for aid in ids:add(aid)
        for aid in names:add(aid)
        for aid in registry:add(aid)
        # FIX91: all procedural game background themes are visible in the
        # asset editor as editable override assets.  They remain procedural
        # until the artist saves one, so merely opening FIX91 does not change
        # existing world art or add memory cost to gameplay.
        for theme,label in BACKGROUND_THEME_NAMES.items():
            add('background.theme_'+theme,label,kind='visual',value='background.theme_'+theme,size=32,background_theme=theme)
        folder=os.path.join(self.assets,'pixel_sources')
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            if not name.endswith('.json'):continue
            prefix,_,slug=name[:-5].partition('_')
            if prefix in CATEGORY_LABELS and slug:add(prefix+'.'+slug)
        # FIX91: discover every shipped raster instead of relying on a hand-maintained
        # binding list.  The editor only records paths here; image bytes are loaded
        # lazily when the user opens one asset.  This keeps startup bounded even
        # with hundreds of sprites.
        image_ext={'.png','.jpg','.jpeg','.webp'}
        def raster_alias(rel):
            low=rel.lower(); stem=os.path.splitext(os.path.basename(rel))[0]
            folder=rel.split('/',1)[0].lower() if '/' in rel else ''
            if folder=='creatures' or stem.startswith('creature_'): cat='creature'; slug=stem.removeprefix('creature_')
            elif folder=='weapons' or stem.startswith('weapon_'): cat='weapon'; slug=stem.removeprefix('weapon_')
            elif folder=='equipment': cat='equipment'; slug=stem.removeprefix('equipment_')
            elif folder in ('equipment_icons','inventory_icons'):
                cat='inventory'; slug=stem
            elif folder in ('backgrounds','background') or 'background' in stem or 'backdrop' in stem:
                cat='background'; slug=stem.removeprefix('background_')
            elif folder in ('effects','vfx') or 'effect' in stem or stem.startswith('vfx_'):
                cat='effect'; slug=stem.removeprefix('effect_').removeprefix('vfx_')
            elif folder=='control_icons' and stem.startswith('magic_'):
                cat='magic'; slug=stem.removeprefix('magic_')
            elif folder=='control_icons':
                cat='ui'; slug=stem
            elif folder in ('items','item_icons'):
                cat='item'; slug=stem.removeprefix('item_')
            elif folder=='tiles' or stem.startswith('tile_'):
                cat='tile'; slug=stem.removeprefix('tile_')
            elif folder in ('decorations','decoration'):
                cat='decoration'; slug=stem
            else:
                # Unknown shipped art is still reachable instead of silently
                # disappearing from the editor.  Put it in the generic item
                # image group and preserve the original file through control_file.
                cat='item'; slug=stem
            return cat,slug
        for root_dir,dirs,files in os.walk(self.assets):
            dirs[:]=[d for d in dirs if d not in ('pixel_sources','audio','__pycache__')]
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() not in image_ext: continue
                path=os.path.join(root_dir,name); rel=os.path.relpath(path,self.assets).replace(os.sep,'/')
                cat,slug=raster_alias(rel)
                aid=cat+'.'+slug
                row=add(aid,kind='visual',value=aid)
                if row is not None:
                    row.setdefault('file',rel); row.setdefault('control_file',rel); row['shipped_raster']=True
                    # Expose a second inventory-facing alias for equipment and
                    # weapon artwork when no dedicated inventory icon exists.
                    if cat in ('weapon','equipment'):
                        inv=add('inventory.'+cat+'_'+slug, row.get('name',slug), kind='visual', value='inventory.'+cat+'_'+slug)
                        if inv is not None:
                            inv.setdefault('file',rel); inv.setdefault('control_file',rel); inv['inventory_alias_of']=aid
        from world.tile_registry import TILES
        for tile in TILES.values():
            if tile.name=='air':continue
            add('tile.'+tile.name,ASSET_NAMES_ZH.get('tile.'+tile.name,''),kind='terrain',value=tile.name,color=tile.color)
        from map_editor.catalog import ITEMS,load_custom_items
        load_custom_items(self.root)
        self.tools=[]
        for item in ITEMS:
            aid=''
            if item.layer=='terrain':aid='tile.'+str(item.value)
            elif item.layer=='decoration':aid='decoration.'+str(item.value[0])
            elif item.layer=='vegetation':aid='plant.map_'+str(item.value[0])
            elif item.layer=='custom_map':aid=str(item.value)
            self.tools.append({'id':item.item_id,'name':item.label,'category':'地圖筆刷／'+item.category,'kind':item.layer,
                               'value':item.value,'color':item.color,'asset':aid,'symbol':item.symbol})
            if aid:
                add(aid,item.label if item.layer!='vegetation' else '',kind=item.layer,value=item.value,color=item.color)
        for species,cfg in self.creatures.items():
            creature_id='creature.'+species
            crow=add(creature_id,cfg.get('name',species),kind='creature',value=species,
                hostile=bool(cfg.get('hostile')),boss=bool(cfg.get('boss')),locomotion=cfg.get('locomotion','ground'),
                habitat=cfg.get('habitat',''),element=str(cfg.get('element','earth') or 'earth'))
            # FIX119: expose boss/creature authored attack effects as direct,
            # per-frame editable entries in the effect category.  These are
            # linked aliases to the real effect asset/state; opening one in the
            # AssetEditor edits the original flipbook instead of a detached copy.
            src_rel='pixel_sources/creature_'+species+'.json'
            try:src_payload=read_json(safe_path(self.assets,src_rel))
            except ValueError:src_payload={}
            binds=src_payload.get('combat_bindings',{}) if isinstance(src_payload,dict) else {}
            for state,zh in (('attack','一般攻擊特效'),('special','重攻擊特效')):
                raw=binds.get(state,{}) if isinstance(binds,dict) else {}
                parent_id=str(raw.get('effect_asset','') or '')
                parent_state=str(raw.get('effect_state','effect') or 'effect')
                if not parent_id:
                    continue
                effect_id='effect.'+species+'_'+state
                source_binding=registry.get(parent_id,{}) if isinstance(registry.get(parent_id),dict) else {}
                source_rel=source_binding.get('pixel_source') or ('pixel_sources/'+parent_id.replace('.','_')+'.json')
                try:parent_payload=read_json(safe_path(self.assets,source_rel))
                except ValueError:parent_payload={}
                prow=((parent_payload.get('animations',{}) if isinstance(parent_payload,dict) else {}).get(parent_state,{}) if isinstance(parent_payload,dict) else {})
                try:canvas_size=int(prow.get('canvas_size') or parent_payload.get('size') or raw.get('effect_canvas_size') or 64)
                except Exception:canvas_size=64
                label=(crow.get('name',species) if isinstance(crow,dict) else cfg.get('name',species))+'・'+zh
                # FIX121: once a boss has its own effect.<species>_<state> source,
                # expose that file directly.  Do not wrap it back into a linked
                # alias to itself; that old path made the editor look like a
                # composite/shared storyboard instead of a normal flipbook.
                if parent_id==effect_id:
                    add(effect_id,label,kind='visual',value=effect_id,
                        effect_canvas_size=canvas_size,linked_owner_asset=creature_id,linked_owner_state=state)
                else:
                    add(effect_id,label,kind='linked_effect',value=effect_id,
                        effect_source_asset=parent_id,effect_source_state=parent_state,
                        effect_canvas_size=canvas_size,linked_owner_asset=creature_id,linked_owner_state=state)
            for drop in cfg.get('loot',()):
                if not isinstance(drop,(list,tuple)) or len(drop)<3:continue
                self.loot[str(drop[0])]={'id':str(drop[0]),'name':str(drop[1])}
        from systems.weapon_catalog import WEAPON_DEFS
        for key,cfg in WEAPON_DEFS.items():
            item_id=str(cfg.get('item_id') or 'weapon_'+key)
            weapon_aid='weapon.'+key
            row=add(weapon_aid,cfg.get('name',key),kind='chest_item',value=item_id)
            self.loot[item_id]={'id':item_id,'name':row['name'],'asset':row['id']}
            # FIX91: expose every image-authored weapon VFX state as its own
            # effect entry.  The asset editor opens the real parent frames and
            # writes them back to the weapon source, so this is not a detached
            # preview or duplicate PNG.
            bind=registry.get(weapon_aid,{}) if isinstance(registry.get(weapon_aid),dict) else {}
            src_rel=bind.get('pixel_source') or ('pixel_sources/weapon_'+key+'.json')
            try: src_payload=read_json(safe_path(self.assets,src_rel))
            except ValueError: src_payload={}
            anims=src_payload.get('animations',{}) if isinstance(src_payload,dict) else {}
            for state,zh in (('normal_effect','一般攻擊特效'),('heavy_effect','重攻擊特效')):
                raw=anims.get(state) if isinstance(anims,dict) else None
                if not isinstance(raw,dict) or not isinstance(raw.get('frames'),list) or not raw.get('frames'): continue
                effect_id='effect.weapon_'+key+'_'+state
                add(effect_id, str(cfg.get('name',key))+'・'+zh, kind='linked_effect', value=effect_id,
                    effect_source_asset=weapon_aid,effect_source_state=state,
                    effect_canvas_size=int(raw.get('canvas_size') or len(raw.get('frames',[[]])[0]) or 64))
        eq=read_json(os.path.join(self.assets,'equipment_catalog.json')).get('items',{})
        for key,cfg in eq.items() if isinstance(eq,dict) else ():
            aid=str(cfg.get('asset_id') or key)
            if not aid.startswith('equipment.'):aid='equipment.'+aid.replace('equipment_','')
            row=add(aid,cfg.get('display_name_zh',cfg.get('name',aid)),kind='chest_item',value=str(cfg.get('item_id') or aid))
            self.loot[row['value']]={'id':row['value'],'name':row['name'],'asset':aid}
        # Known ordinary loot IDs and externally bound items remain editable.
        for aid,row in tuple(self.by_id.items()):
            if row['category']=='item':
                item_id=aid.split('.',1)[1]
                if item_id.startswith('chest_'):continue
                row.update(kind='chest_item',value=item_id)
                self.loot.setdefault(item_id,{'id':item_id,'name':row['name'],'asset':aid})
            elif row['category']=='player':row.update(kind='spawn')
        for item_id,entry in tuple(self.loot.items()):
            if not item_id.startswith(('weapon_','equipment.')):
                row=add('item.'+item_id,entry['name'],kind='chest_item',value=item_id)
                entry.setdefault('asset',row['id'])
        self.loot.setdefault('tool_grapple',{'id':'tool_grapple','name':'鉤爪','asset':'ui.tool_grapple'})
        return self

    def categories(self):
        return {cat:[a for a,r in self.by_id.items() if r['category']==cat] for cat in CATEGORY_LABELS}

    def palette(self):
        return list(self.tools)+[dict(row,category=CATEGORY_LABELS[row['category']]) for row in self.by_id.values()]


def placed_secondary_runtimes(root, metadata):
    """Only include action catalogs actually referenced by authored actors."""
    from systems.editor_objects import objects_from_metadata
    wanted={str(r.get('species','')) for r in objects_from_metadata(metadata) if r.get('kind')=='creature'}
    if not wanted:return []
    from systems.secondary_world import SecondaryWorldRuntime
    folder=os.path.join(root,'maps');out=[];seen=set()
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if not name.endswith('.json'):continue
        path=os.path.join(folder,name);data=read_json(path)
        if not isinstance(data,dict):continue
        sec=SecondaryWorldRuntime.from_map_loader(root,SimpleNamespace(path=path,payload=data))
        if sec.active and sec.world_id not in seen and wanted.intersection(sec.archetypes):
            seen.add(sec.world_id);out.append(sec)
    return out
