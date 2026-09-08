# -*- coding: utf-8 -*-
"""FIX91 single-threaded editor authority, independent of UIKit/WebKit.

Chunk streaming, bounded compressed undo, transactional two-file map saves,
shared asset index, safe validated authored spawns and multi-item treasure.
"""
import base64,copy,io,json,math,os,shutil,time,uuid,zlib
from collections import OrderedDict
from pathlib import Path
from map_editor.model import MapDocument
from map_editor.terrain_store import TerrainChunkLayer
from systems.editor_catalog import EditorAssetCatalog,read_json,safe_path
from systems.editor_objects import EDITOR_KEY,MAX_POINTS,MAX_ACTORS,MAX_PER_POINT,MAX_LOOT_ROWS,locate,finite,objects_from_metadata,normalize_editor_ai
from config import TILE_SIZE,MAP_EDITOR_DEFAULT_WIDTH,MAP_EDITOR_DEFAULT_HEIGHT
from systems.map_loader import TERRAIN_NAME_TO_ID
from world.tile_registry import tile_def

class DocumentWorld:
    def __init__(self,doc):self.doc=doc;self.width_tiles=doc.width;self.height_tiles=doc.height
    def get_tile(self,tx,ty):return TERRAIN_NAME_TO_ID.get(self.doc.get('terrain',tx,ty),0)
    def solid_top_y(self,tx,ty):
        if not(0<=tx<self.width_tiles and 0<=ty<self.height_tiles):return ty*TILE_SIZE
        solid=tile_def(self.get_tile(tx,ty)).solid
        custom=self.doc.get('custom_map',tx,ty)
        if not solid and custom and custom in getattr(self.doc,'_editor_custom_solids',set()):
            return float(ty * TILE_SIZE)
        if not solid:return None
        mask=self.doc.get('ground_layer',tx,ty,7)
        return (ty+1-float({1:1/3,3:2/3,7:1}.get(mask,1)))*TILE_SIZE

def document_snapshot(doc):
    """Packed undo snapshot; never expand a large terrain into millions of rows."""
    from world.binary_map import _to_little_u16_bytes
    result=doc.to_dict(include_terrain=False)
    layer=doc.layers['terrain']
    result['_editor_packed_terrain']={'palette':list(layer.palette),'chunk_size':layer.chunk_size,
        'chunks':[[cx,cy,base64.b64encode(_to_little_u16_bytes(codes)).decode('ascii')]
                  for cx,cy,codes,_ in layer.iter_chunks()]}
    return result

def snapshot_document(payload):
    from world.binary_map import _from_little_u16_bytes
    doc=MapDocument.from_dict(payload);saved=payload.get('_editor_packed_terrain')
    if saved:
        layer=TerrainChunkLayer(saved['chunk_size'],saved['palette'])
        for cx,cy,blob in saved['chunks']:
            layer.import_chunk(cx,cy,_from_little_u16_bytes(base64.b64decode(blob)),saved['palette'])
        doc.layers['terrain']=layer
    return doc

class History:
    """zlib JSON diffs; total compressed budget, no full map per paint tap."""
    def __init__(self,max_bytes=8*1024*1024,max_steps=40):self.undo=[];self.redo=[];self.max_bytes=max_bytes;self.max_steps=max_steps
    def push(self,entry):
        raw=zlib.compress(json.dumps(entry,ensure_ascii=False,separators=(',',':')).encode(),3)
        if len(raw)>self.max_bytes:raise ValueError('這個操作超過撤銷記憶體上限，未套用')
        self.undo.append(raw);self.redo.clear();self.trim()
    def trim(self):
        while len(self.undo)+len(self.redo)>self.max_steps or sum(map(len,self.undo+self.redo))>self.max_bytes:
            if self.undo:self.undo.pop(0)
            elif self.redo:self.redo.pop(0)
            else:break
    def clear(self):self.undo.clear();self.redo.clear()

def _atomic_map_save(doc,path):
    """Commit binary first under unique name; JSON is the single commit point."""
    from world.binary_map import write_binary_terrain
    folder=os.path.dirname(path);os.makedirs(folder,exist_ok=True)
    previous=read_json(path);prevrel=previous.get('terrain_storage',{}).get('path','')
    if os.path.isfile(path) and not os.path.exists(path+'.pre_FIX91.bak'):
        # Backup JSON is self-contained: its binary has a distinct preserved name.
        backup=copy.deepcopy(previous)
        if prevrel:
            old=safe_path(folder,prevrel)
            if os.path.isfile(old):
                backrel=os.path.basename(path)+'.pre_FIX91.ptw.bak';shutil.copy2(old,os.path.join(folder,backrel))
                backup['terrain_storage']['path']=backrel
        with open(path+'.pre_FIX91.bak','w',encoding='utf-8') as f:json.dump(backup,f,ensure_ascii=False)
    basename=os.path.splitext(os.path.basename(path))[0]
    binary=os.path.join(folder,basename+'.edit91_'+uuid.uuid4().hex[:12]+'.ptw')
    temp=path+'.edit91.tmp'
    try:
        storage=write_binary_terrain(binary,doc.layers['terrain'],doc.width,doc.height)
        payload=doc.to_dict(include_terrain=False);payload['terrain_storage']=storage
        payload['metadata']['initial_ground_layers']=[[x,y,v] for (x,y),v in doc.layers['ground_layer'].items() if v not in (0,7)]
        payload['metadata']['terrain_non_air']=len(doc.layers['terrain'])
        payload['metadata']['editor_revision']=uuid.uuid4().hex
        with open(temp,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    except Exception:
        for p in (temp,binary):
            try:os.remove(p)
            except OSError:pass
        raise
    # Only our obsolete generations; never delete legacy terrain or backups.
    if prevrel and '.edit91_' in os.path.basename(prevrel):
        try:os.remove(safe_path(folder,prevrel))
        except OSError:pass
    return path

class EditorSession:
    def __init__(self,root):
        self.root=os.path.abspath(root);self.folder=os.path.join(self.root,'maps');os.makedirs(self.folder,exist_ok=True)
        self.catalog=EditorAssetCatalog(self.root)
        self.palette={r['id']:r for r in self.catalog.palette()}
        self.map_name='editor_map.json';self.path=os.path.join(self.folder,self.map_name)
        self.doc=MapDocument.load(self.path) if os.path.isfile(self.path) else MapDocument()
        if not os.path.isfile(self.path):self.doc.build_flat_template()
        self.history=History();self.generation=1;self.dirty=False;self.thumb_cache=OrderedDict();self._asset_model=None
        self.reindex()
    def reindex(self):
        self.overlay_index={};self.object_index={}
        from map_editor.catalog import load_custom_items
        self.doc._editor_custom_solids=set()
        raw=read_json(os.path.join(self.root,'assets','custom_map_assets.json'))
        from systems.custom_tile_rules import role_from_meta, SUPPORT_ROLES
        for aid,row in raw.get('assets',{}).items():
            if role_from_meta(row) in SUPPORT_ROLES:self.doc._editor_custom_solids.add(aid)
        cs=self.doc.layers['terrain'].chunk_size
        for layer,data in self.doc.layers.items():
            if layer=='terrain':continue
            for (x,y),v in data.items():self.overlay_index.setdefault((x//cs,y//cs),[]).append([layer,x,y,v])
        for row in objects_from_metadata(self.doc.metadata):
            x=int(row.get('x',0));y=int(row.get('y',0));self.object_index.setdefault((x//cs,y//cs),[]).append(row)
    def map_files(self):return sorted(n for n in os.listdir(self.folder) if n.endswith('.json') and 'backup' not in n.lower())
    def manifest(self,full=True):
        result = dict(name=self.map_name,maps=self.map_files(),width=self.doc.width,height=self.doc.height,
            spawn=self.doc.player_spawn,chunk_size=self.doc.layers['terrain'].chunk_size,generation=self.generation,
            palette=list(self.palette.values()),loot=list(self.catalog.loot.values()),dirty=self.dirty,
            objects=copy.deepcopy(objects_from_metadata(self.doc.metadata)),legacy=self.legacy_markers(),runtime=self.runtime_markers(),
            auto_population=self.doc.metadata.get('editor_population_mode','append')!='authored_only',
            suppressed_auto=len(self.doc.metadata.get('editor_suppressed_auto_spawns',[]) or []),
            auto_chests=bool(self.doc.metadata.get('editor_auto_chests',True)),
            history={'undo':len(self.history.undo),'redo':len(self.history.redo)},
            natural_surface=self.doc.metadata.get('natural_surface',{}))
        if not full:
            result.pop('palette',None);result.pop('loot',None);result.pop('natural_surface',None)
        return result
    def legacy_markers(self):
        rows=[]
        for i,row in enumerate(self.doc.metadata.get('creature_spawns',[])[:512]):
            if not isinstance(row,dict):continue
            species=str(row.get('species',''))
            cfg=self.catalog.creatures.get(species,{})
            rows.append(dict(id='legacy:'+str(i),kind='legacy_creature',legacy_index=i,x=row.get('x',0),y=row.get('floor',0)-1,
                name=cfg.get('name',species or '生物'),species=species,count=row.get('count',1),spacing=row.get('spacing',3),
                patrol=row.get('patrol_tiles',2.8),respawn=row.get('respawn_enabled',not cfg.get('boss',False)),
                respawn_seconds=row.get('respawn_seconds',cfg.get('respawn_seconds',120)),
                habitat_mode=row.get('habitat_mode','default'),
                move_mode=row.get('move_mode','default'),behavior_mode=row.get('behavior_mode','default'),attack_mode=row.get('attack_mode','default'),
                detect_tiles=row.get('detect_tiles',0.0),detect_vertical_tiles=row.get('detect_vertical_tiles',0.0),
                speed_scale=row.get('speed_scale',1.0),attack_cooldown_scale=row.get('attack_cooldown_scale',1.0),source='地圖既有出生設定'))
        for i,row in enumerate(self.doc.metadata.get('portals',[])[:256]):
            if not isinstance(row,dict):continue
            rows.append(dict(id='portal:'+str(i),kind='portal',x=row.get('x',0),y=row.get('y',0),name=str(row.get('label',row.get('target_map','傳送門')))))
        return rows
    def runtime_markers(self):
        # Only automatic actors are listed here. Authored creature_spawns are
        # already represented by legacy_markers and remain group-editable.
        try:
            from systems.editor_objects import runtime_population_snapshot_path
            key=os.path.splitext(os.path.basename(self.map_name))[0]
            payload=read_json(runtime_population_snapshot_path(self.root,key))
        except Exception:return []
        rows=[];supp=set(str(v) for v in (self.doc.metadata.get('editor_suppressed_auto_spawns',[]) or []))
        for raw in payload.get('creatures',[]) if isinstance(payload,dict) else ():
            if not isinstance(raw,dict) or raw.get('origin')!='auto':continue
            eid=str(raw.get('entity_id',''))
            if not eid or eid in supp:continue
            rows.append(dict(id='runtime:'+eid,runtime_id=eid,kind='runtime_creature',x=float(raw.get('x',0)),y=float(raw.get('y',0)),
                name=str(raw.get('name') or raw.get('species') or '自動生物'),species=str(raw.get('species','')),count=1,spacing=1,
                patrol=float(raw.get('patrol',3)),respawn=bool(raw.get('respawn',True)),respawn_seconds=float(raw.get('respawn_seconds',120)),
                locomotion=str(raw.get('locomotion','ground')),boss=bool(raw.get('boss',False)),habitat_mode=str(raw.get('habitat_mode','default')),
                move_mode=str(raw.get('move_mode','default')),behavior_mode=str(raw.get('behavior_mode','default')),attack_mode=str(raw.get('attack_mode','default')),
                detect_tiles=float(raw.get('detect_tiles',0.0)),detect_vertical_tiles=float(raw.get('detect_vertical_tiles',0.0)),
                speed_scale=float(raw.get('speed_scale',1.0)),attack_cooldown_scale=float(raw.get('attack_cooldown_scale',1.0)),source='遊戲自動生成（可接管／移除）'))
        return rows[:512]
    def chunks(self,keys):
        layer=self.doc.layers['terrain'];cs=layer.chunk_size;out=[]
        for key in keys[:12]:
            cx,cy=map(int,key)
            if cx<0 or cy<0 or cx*cs>=self.doc.width or cy*cs>=self.doc.height:continue
            arr=layer.chunks.get((cx,cy));values=list(arr) if arr is not None else [0]*(cs*cs)
            out.append(dict(key=[cx,cy],terrain=values,overlays=self.overlay_index.get((cx,cy),[])))
        return dict(generation=self.generation,palette=list(layer.palette),chunks=out)
    def _changed(self):self.generation+=1;self.dirty=True;self.reindex()
    def _diff(self,changes):
        # Changes use [layer,x,y,before,after]. One compact history per gesture.
        if not changes:return
        entry={'cells':changes};self.history.push(entry)
        for layer,x,y,before,after in changes:
            if after is None:self.doc.erase_layer(layer,x,y)
            else:self.doc.set(layer,x,y,after)
        self._changed()
    def paint(self,data):
        tool=str(data.get('tool','paint'));points=data.get('points',[])[:4096]
        # FIX91 dedicated liquid eraser. It removes authored water/lava/honey
        # and swamp liquid without touching terrain, fire, vegetation or other
        # hazard types. This also makes erase independent of the currently
        # selected palette card, so a hidden/off-screen water card cannot trap
        # liquid in the map.
        if tool=='erase_liquid':
            changes=[];seen=set();radius=int(finite(data.get('brush',1),1,1,9))//2
            for px,py in points:
                for y in range(int(py)-radius,int(py)+radius+1):
                    for x in range(int(px)-radius,int(px)+radius+1):
                        if not self.doc.in_bounds(x,y) or (x,y) in seen:continue
                        seen.add((x,y))
                        for ln in ('water','lava','honey'):
                            old=copy.deepcopy(self.doc.get(ln,x,y))
                            if old is not None:changes.append([ln,x,y,old,None])
                        old=copy.deepcopy(self.doc.get('hazard',x,y))
                        if old=='swamp':changes.append(['hazard',x,y,old,None])
            self._diff(changes);return
        spec=self.palette.get(str(data.get('asset','')))
        if not spec:raise ValueError('請先選擇素材')
        layer=spec['kind']
        if layer not in self.doc.layers:raise ValueError('此素材請使用「放置點」或「裝飾貼圖」')
        if tool=='fill':
            if layer!='terrain':raise ValueError('填滿僅適用地形層')
            if not points:return
            tx,ty=map(int,points[0])
            if not self.doc.in_bounds(tx,ty):raise ValueError('填滿位置超出地圖')
            target=self.doc.get('terrain',tx,ty)
            if target==spec['value']:return
            # No million-tuple visited set: compact bitmap + scanline flood.
            visited=bytearray(self.doc.width*self.doc.height);stack=[(tx,ty)];found=[]
            while stack:
                x,y=stack.pop()
                if not self.doc.in_bounds(x,y):continue
                idx=y*self.doc.width+x
                if visited[idx] or self.doc.get(layer,x,y)!=target:continue
                left=x
                while left>0 and not visited[y*self.doc.width+left-1] and self.doc.get(layer,left-1,y)==target:left-=1
                x=left
                while x<self.doc.width and not visited[y*self.doc.width+x] and self.doc.get(layer,x,y)==target:
                    visited[y*self.doc.width+x]=1;found.append([layer,x,y,target,spec['value']])
                    if len(found)>65536:raise ValueError('單次填滿上限 65,536 格；請先用筆刷分區，地圖未更動')
                    if y>0:stack.append((x,y-1))
                    if y+1<self.doc.height:stack.append((x,y+1))
                    x+=1
            self._diff(found);return
        changes=[];seen=set();radius=int(finite(data.get('brush',1),1,1,9))//2
        for px,py in points:
            for y in range(int(py)-radius,int(py)+radius+1):
                for x in range(int(px)-radius,int(px)+radius+1):
                    if not self.doc.in_bounds(x,y) or (x,y) in seen:continue
                    seen.add((x,y))
                    liquid_selected=(layer in ('water','lava','honey') or (layer=='hazard' and spec.get('value')=='swamp'))
                    layers=list(self.doc.layers) if tool=='erase_all' else ((['water','lava','honey','hazard'] if tool.startswith('erase') and liquid_selected else [layer]))
                    for ln in layers:
                        old=copy.deepcopy(self.doc.get(ln,x,y))
                        if ln=='hazard' and tool.startswith('erase') and liquid_selected and old!='swamp':continue
                        value=None if tool.startswith('erase') else copy.deepcopy(spec['value'])
                        if ln=='terrain' and value=='air':value=None
                        if old!=value:changes.append([ln,x,y,old,value])
        self._diff(changes)
    def update_objects(self,new):
        self.history.push({'meta':{EDITOR_KEY:[copy.deepcopy(objects_from_metadata(self.doc.metadata)),new]}})
        self.doc.metadata[EDITOR_KEY]=new;self._changed()
    def put_object(self,data):
        raw=copy.deepcopy(data);kind=str(raw.get('kind',''));tx=int(raw.get('x',0));ty=int(raw.get('y',0))
        if not self.doc.in_bounds(tx,ty):raise ValueError('放置位置超出地圖')
        rows=copy.deepcopy(objects_from_metadata(self.doc.metadata));eid=str(raw.get('id') or uuid.uuid4().hex)
        if len(eid)>128:raise ValueError('放置點識別碼過長')
        old=next((r for r in rows if r['id']==eid),None)
        if old is None and len(rows)>=MAX_POINTS:raise ValueError('此地圖已達 512 個手動放置點上限')
        row={'id':eid,'kind':kind,'x':tx,'y':ty,'enabled':True}
        world=DocumentWorld(self.doc)
        if kind=='creature':
            species=str(raw.get('species',''));cfg=self.catalog.creatures.get(species)
            if not cfg:raise ValueError('未知生物種類')
            count=int(finite(raw.get('count',1),1,1,MAX_PER_POINT));spacing=int(finite(raw.get('spacing',2),2,1,16))
            if sum(int(r.get('count',1)) for r in rows if r['kind']=='creature' and r['id']!=eid)+count>MAX_ACTORS:raise ValueError('手動生物總數上限 256 隻')
            ai=normalize_editor_ai(raw)
            for i in range(count):locate(world,self.doc.layers['water'],cfg,tx+i*spacing,ty,raw.get('anchor','auto'),ai['habitat_mode'])
            row.update(species=species,name=str(cfg['name']),count=count,spacing=spacing,
                       patrol=finite(raw.get('patrol',3),3,0,32),respawn=bool(raw.get('respawn',not cfg.get('boss',False))),
                       respawn_seconds=finite(raw.get('respawn_seconds',120),120,1,86400),anchor=str(raw.get('anchor','auto')),
                       move_mode=ai['move_mode'],behavior_mode=ai['behavior_mode'],attack_mode=ai['attack_mode'],
                       habitat_mode=ai['habitat_mode'],
                       detect_tiles=ai['detect_tiles'],detect_vertical_tiles=ai['detect_vertical_tiles'],
                       speed_scale=ai['speed_scale'],attack_cooldown_scale=ai['attack_cooldown_scale'])
            x,y=locate(world,self.doc.layers['water'],cfg,tx,ty,row['anchor'],ai['habitat_mode']);row['preview_feet']=[x/TILE_SIZE,y/TILE_SIZE]
        elif kind=='chest':
            if not isinstance(raw.get('loot',[]),list) or len(raw.get('loot',[]))>MAX_LOOT_ROWS:raise ValueError('每個寶箱最多 24 種內容物')
            loot=[]
            for entry in raw.get('loot',[])[:MAX_LOOT_ROWS]:
                item_id=str(entry.get('id',''));item=self.catalog.loot.get(item_id)
                if not item:raise ValueError('未知寶箱道具：'+item_id)
                n=int(finite(entry.get('count',1),1,1,999));loot.append(dict(id=item_id,name=item['name'],count=n))
            if not loot:raise ValueError('寶箱至少需要一種內容物')
            x,y=locate(world,{},dict(w=30,h=24),tx,ty)
            row.update(name=str(raw.get('name') or '自訂寶箱')[:48],loot=loot,preview_feet=[x/TILE_SIZE,y/TILE_SIZE])
        elif kind=='visual':
            aid=str(raw.get('asset',''))
            if aid not in self.catalog.by_id:raise ValueError('未知圖像素材')
            row.update(asset=aid,name=self.catalog.by_id[aid]['name'],scale=finite(raw.get('scale',1),1,.25,3),flip=bool(raw.get('flip',False)),animation=str(raw.get('animation',''))[:64])
        else:raise ValueError('未知放置點種類')
        rows=[r for r in rows if r['id']!=eid];rows.append(row);self.update_objects(rows);return row
    def put_legacy_creature(self,data):
        eid=str(data.get('id',''));idx=int(str(eid).split(':',1)[1]) if eid.startswith('legacy:') else -1
        rows=copy.deepcopy(self.doc.metadata.get('creature_spawns',[]) or [])
        if not (0<=idx<len(rows)) or not isinstance(rows[idx],dict):raise ValueError('找不到原始生物出生點，請重新同步')
        species=str(data.get('species',rows[idx].get('species','')));cfg=self.catalog.creatures.get(species)
        if not cfg:raise ValueError('未知生物種類')
        before=copy.deepcopy(rows)
        row=copy.deepcopy(rows[idx]);row['species']=species;row['x']=int(finite(data.get('x',row.get('x',0)),0,0,self.doc.width-1));_edit_y=int(finite(data.get('y',row.get('floor',1)-1),0,0,self.doc.height-2));row['floor']=_edit_y+1;if_ceiling=str(cfg.get('locomotion','ground')) in ('ceiling','ceiling_drop');row['ceiling']=_edit_y if if_ceiling else row.get('ceiling',row.get('floor',1)-1)
        row['count']=int(finite(data.get('count',row.get('count',1)),1,1,MAX_PER_POINT));row['spacing']=int(finite(data.get('spacing',row.get('spacing',3)),3,1,16));row['patrol_tiles']=finite(data.get('patrol',row.get('patrol_tiles',2.8)),2.8,0,32)
        row['respawn_enabled']=bool(data.get('respawn',row.get('respawn_enabled',not cfg.get('boss',False))));row['respawn_seconds']=finite(data.get('respawn_seconds',row.get('respawn_seconds',120)),120,1,86400)
        ai=normalize_editor_ai(data)
        row.update(move_mode=ai['move_mode'],behavior_mode=ai['behavior_mode'],attack_mode=ai['attack_mode'],
                   habitat_mode=ai['habitat_mode'],
                   detect_tiles=ai['detect_tiles'],detect_vertical_tiles=ai['detect_vertical_tiles'],
                   speed_scale=ai['speed_scale'],attack_cooldown_scale=ai['attack_cooldown_scale'])
        # Validate explicit ecology overrides against the edited cell.  This is
        # especially important for aquatic and burrow modes.
        locate(DocumentWorld(self.doc),self.doc.layers['water'],cfg,int(row['x']),_edit_y,'auto',ai['habitat_mode'])
        rows[idx]=row;self.history.push({'meta':{'creature_spawns':[before,copy.deepcopy(rows)]}});self.doc.metadata['creature_spawns']=rows;self._changed();return self.legacy_markers()[idx]
    def delete_legacy_creature(self,eid):
        idx=int(str(eid).split(':',1)[1]) if str(eid).startswith('legacy:') else -1;rows=copy.deepcopy(self.doc.metadata.get('creature_spawns',[]) or [])
        if not (0<=idx<len(rows)):raise ValueError('找不到原始生物出生點')
        before=copy.deepcopy(rows);rows.pop(idx);self.history.push({'meta':{'creature_spawns':[before,copy.deepcopy(rows)]}});self.doc.metadata['creature_spawns']=rows;self._changed()
    def suppress_runtime_creature(self,runtime_id):
        rid=str(runtime_id or '').replace('runtime:','',1);before=list(self.doc.metadata.get('editor_suppressed_auto_spawns',[]) or [])
        after=list(dict.fromkeys([str(v) for v in before if v]+([rid] if rid else [])))[:1024]
        if before!=after:self.history.push({'meta':{'editor_suppressed_auto_spawns':[before,after]}});self.doc.metadata['editor_suppressed_auto_spawns']=after;self._changed()
        return rid
    def override_runtime_creature(self,data):
        rid=str(data.get('runtime_id') or data.get('id','')).replace('runtime:','',1)
        if not rid:raise ValueError('找不到遊戲自動生物識別碼')
        # Validate/create replacement first. If this raises, original remains.
        replacement=copy.deepcopy(data);replacement['id']='override:'+rid;replacement['kind']='creature';replacement.pop('runtime_id',None)
        row=self.put_object(replacement);self.suppress_runtime_creature(rid);return row
    def undo_redo(self,redo=False):
        source=self.history.redo if redo else self.history.undo;target=self.history.undo if redo else self.history.redo
        if not source:return False
        raw=source.pop();entry=json.loads(zlib.decompress(raw));idx=1 if redo else 0
        if 'snapshot' in entry:
            self.doc=snapshot_document(entry['snapshot'][idx])
        for layer,x,y,a,b in entry.get('cells',[]):
            value=b if redo else a
            if value is None:self.doc.erase_layer(layer,x,y)
            else:self.doc.set(layer,x,y,value)
        for key,(a,b) in entry.get('meta',{}).items():self.doc.metadata[key]=copy.deepcopy(b if redo else a)
        if 'spawn' in entry:self.doc.player_spawn=list(entry['spawn'][idx])
        if 'size' in entry:self.doc.resize(*entry['size'][idx])
        target.append(raw);self.history.trim();self._changed();return True
    def asset_source(self,aid):
        """Read one Base payload; thumbnails never normalize/decode every frame."""
        from systems.editor_catalog import safe_path
        row=self.catalog.by_id.get(aid)
        if not row:raise ValueError('未知素材')
        registry=read_json(os.path.join(self.root,'assets','registry.json')).get('bindings',{})
        rel=registry.get(aid,{}).get('pixel_source') or row.get('default_source')
        if not rel:
            candidate='pixel_sources/'+aid.replace('.','_')+'.json'
            if os.path.isfile(safe_path(self.catalog.assets,candidate)):rel=candidate
        if rel:
            path=safe_path(self.catalog.assets,rel)
            if os.path.isfile(path):
                payload=read_json(path)
                pixels=payload.get('pixels',[])
                if isinstance(pixels,list) and 1<=len(pixels)<=128 and all(isinstance(r,list) and len(r)==len(pixels) for r in pixels):
                    return payload
        from asset_editor.model import AssetEditorModel
        if self._asset_model is None:self._asset_model=AssetEditorModel(self.root)
        return self._asset_model.load_pixel_asset(aid)

    def thumbnail(self,aid):
        if aid in self.thumb_cache:
            self.thumb_cache.move_to_end(aid);return self.thumb_cache[aid]
        if aid not in self.catalog.by_id:return None
        from PIL import Image
        # Sources load one at a time; no retained animation atlas here.
        source=self.asset_source(aid);pixels=source['pixels'];n=len(pixels)
        image=Image.new('RGBA',(n,n));data=[]
        for row in pixels:
            for c in row:
                c=str(c).lstrip('#');c=c+'FF' if len(c)==6 else c
                try:data.append(tuple(int(c[i:i+2],16) for i in (0,2,4,6)))
                except (ValueError,IndexError):data.append((0,0,0,0))
        image.putdata(data);buf=io.BytesIO();image.save(buf,format='PNG')
        result={'id':aid,'png':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode(),'size':n}
        self.thumb_cache[aid]=result
        while len(self.thumb_cache)>96:self.thumb_cache.popitem(last=False)
        return result
    def overview(self):
        from PIL import Image
        w=min(768,self.doc.width);h=max(1,round(w*self.doc.height/self.doc.width));h=min(384,h)
        im=Image.new('RGB',(w,h),'#101c2a');p=im.load()
        for y in range(h):
            ty=min(self.doc.height-1,int(y*self.doc.height/h))
            for x in range(w):
                tx=min(self.doc.width-1,int(x*self.doc.width/w));name=self.doc.get('terrain',tx,ty)
                if name:
                    c=tile_def(TERRAIN_NAME_TO_ID.get(name,0)).color.lstrip('#');p[x,y]=tuple(int(c[i:i+2],16) for i in (0,2,4))
        b=io.BytesIO();im.save(b,format='PNG');return 'data:image/png;base64,'+base64.b64encode(b.getvalue()).decode()
    def _background_asset_id(self):
        stem=os.path.splitext(os.path.basename(self.map_name))[0].lower()
        stem=''.join(ch if ch.isalnum() or ch in '_-' else '_' for ch in stem).strip('_') or 'map'
        return 'background.map_'+stem

    def background_get(self):
        aid=str(self.doc.metadata.get('background_asset') or self._background_asset_id())
        pixels=None;size=32
        try:
            from asset_editor.model import AssetEditorModel
            model=AssetEditorModel(self.root); data=model.load_pixel_asset(aid,requested_size=None)
            if data.get('source')!='builtin':
                pixels=data.get('pixels');size=int(data.get('size') or len(pixels) or 32)
        except Exception:pass
        if size not in (16,24,32):size=32
        if not (isinstance(pixels,list) and len(pixels)==size and all(isinstance(r,list) and len(r)==size for r in pixels)):
            pixels=[['#00000000' for _x in range(size)] for _y in range(size)]
        return {'asset_id':aid,'size':size,'pixels':pixels,'name':'地圖背景・'+os.path.splitext(self.map_name)[0]}

    def background_save(self,data):
        pixels=data.get('pixels');size=len(pixels) if isinstance(pixels,list) else 0
        if size not in (16,24,32) or not all(isinstance(r,list) and len(r)==size for r in pixels):
            raise ValueError('背景畫布支援 16×16／24×24／32×32')
        normalized=[]
        for row in pixels:
            out=[]
            for c in row:
                text=str(c or '#00000000').upper()
                if not text.startswith('#'):text='#'+text
                if len(text)==7:text+='FF'
                if len(text)!=9 or any(ch not in '0123456789ABCDEF#' for ch in text):raise ValueError('背景色碼格式錯誤')
                out.append(text)
            normalized.append(out)
        aid=self._background_asset_id()
        from asset_editor.model import AssetEditorModel
        model=AssetEditorModel(self.root)
        if aid not in model.recommended_ids('background'):
            model.create_pixel_asset('background',aid.split('.',1)[1],'地圖背景・'+os.path.splitext(self.map_name)[0],size)
        model.save_pixel_asset(aid,'background',normalized,animations={},combat_bindings={})
        before=self.doc.metadata.get('background_asset')
        if before!=aid:self.history.push({'meta':{'background_asset':[before,aid]}})
        self.doc.metadata['background_asset']=aid;self._changed()
        self.catalog=EditorAssetCatalog(self.root);self.palette={r['id']:r for r in self.catalog.palette()};self.thumb_cache.clear();self._asset_model=None
        return {'asset_id':aid,'size':size,'pixels':normalized}
    def handle(self,op,data=None):
        d=data or {}
        if op=='init':return self.manifest()
        if op=='chunks':return self.chunks(d.get('keys',[]))
        if op=='thumbnails':return [x for x in (self.thumbnail(a) for a in d.get('ids',[])[:8]) if x]
        if op=='overview':return {'png':self.overview()}
        if op=='background_get':return self.background_get()
        if op=='background_save':return self.background_save(d)
        if op=='asset_info':
            aid=str(d.get('id',''))
            if aid not in self.catalog.by_id:raise ValueError('未知素材')
            src=self.asset_source(aid)
            return {'size':src['size'],'states':list(src.get('animations',{}))}

        if op=='reload_assets':
            self.catalog=EditorAssetCatalog(self.root);self.palette={r['id']:r for r in self.catalog.palette()};self.thumb_cache.clear();self._asset_model=None
            self.reindex();self.generation+=1
        elif op=='paint':self.paint(d)
        elif op=='object':self.put_object(d)
        elif op=='legacy_object':self.put_legacy_creature(d)
        elif op=='legacy_delete':self.delete_legacy_creature(str(d.get('id','')))
        elif op=='runtime_delete':self.suppress_runtime_creature(str(d.get('runtime_id') or d.get('id','')))
        elif op=='runtime_override':self.override_runtime_creature(d)
        elif op=='restore_auto':
            before=list(self.doc.metadata.get('editor_suppressed_auto_spawns',[]) or []);after=[]
            if before:self.history.push({'meta':{'editor_suppressed_auto_spawns':[before,after]}});self.doc.metadata['editor_suppressed_auto_spawns']=after;self._changed()
        elif op=='delete':
            eid=str(d.get('id',''));self.update_objects([r for r in objects_from_metadata(self.doc.metadata) if r['id']!=eid])
        elif op in ('undo','redo'):self.undo_redo(op=='redo')
        elif op=='spawn':
            x,y=locate(DocumentWorld(self.doc),{},dict(w=28,h=54),d.get('x',0),d.get('y',0))
            val=[int(x/TILE_SIZE),int(y/TILE_SIZE)];self.history.push({'spawn':[self.doc.player_spawn,val]});self.doc.player_spawn=val;self._changed()
        elif op=='settings':
            changes={}
            for key,new in (('editor_population_mode','append' if d.get('auto_population',True) else 'authored_only'),('editor_auto_chests',bool(d.get('auto_chests',True)))):
                changes[key]=[self.doc.metadata.get(key,'append' if key=='editor_population_mode' else True),new]
            self.history.push({'meta':changes})
            for key,(_,v) in changes.items():self.doc.metadata[key]=v
            self._changed()
        elif op=='resize':
            w=int(finite(d.get('width',self.doc.width),self.doc.width,self.doc.width,4096));h=int(finite(d.get('height',self.doc.height),self.doc.height,self.doc.height,512))
            self.history.push({'size':[[self.doc.width,self.doc.height],[w,h]]});self.doc.resize(w,h);self._changed()
        elif op=='save':
            _atomic_map_save(self.doc,self.path);self.dirty=False
        elif op=='load':
            name=str(d.get('name',self.map_name))
            if name not in self.map_files():raise ValueError('請選擇專案中的地圖')
            # Caller explicitly confirms save/discard. No silent overwrite.
            if self.dirty and d.get('decision') not in ('save','discard'):raise ValueError('尚未存檔，請選擇儲存或捨棄')
            if self.dirty and d.get('decision')=='save':_atomic_map_save(self.doc,self.path)
            newpath=safe_path(self.folder,name);newdoc=MapDocument.load(newpath)
            self.doc=newdoc;self.path=newpath;self.map_name=name;self.history.clear();self.generation+=1;self.dirty=False;self.reindex()
        elif op=='generate':
            if not d.get('confirmed'):raise ValueError('生成會替換目前地形，需要確認')
            from map_editor.world_generator import generate_world,add_underground_world,validate_generated_world
            before=document_snapshot(self.doc);seed=int(time.time_ns()%2147483647)
            if d.get('kind')=='underground':new=self.doc.clone_compact();add_underground_world(new,seed=seed)
            else:new=generate_world(seed,width=self.doc.width,height=self.doc.height)
            if new is None:raise ValueError('生成器未回傳地圖')
            self.history.push({'snapshot':[before,document_snapshot(new)]});self.doc=new;self._changed()
        else:raise ValueError('未知指令：'+str(op))
        return self.manifest(full=op in ('reload_assets','load','generate'))
