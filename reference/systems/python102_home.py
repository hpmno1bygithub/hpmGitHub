# -*- coding: utf-8 -*-
"""Non-destructive optional home placement. Only adds ONE editor spawn row.
The caller explicitly runs the setup script. Does not dig terrain, replace
maps, duplicate bosses or repopulate a point removed in the map editor.
"""
import os,json,uuid,hashlib,time,shutil
from pathlib import Path
from config import TILE_SIZE as T
from map_editor.model import MapDocument
from map_editor.session import DocumentWorld
from systems.editor_objects import locate,occupied,MAX_POINTS,MAX_ACTORS
from systems.python102_defs import CREATURES,HABITAT

POINT_ID='fix102_swamp_python_home'

def find_home(root,doc):
    root=Path(root);cfg=CREATURES['giant_python']
    custom=root/'assets/custom_map_assets.json'
    raw=json.loads(custom.read_text(encoding='utf-8')) if custom.is_file() else {}
    from systems.custom_tile_rules import role_from_meta,SUPPORT_ROLES
    doc._editor_custom_solids={aid for aid,row in raw.get('assets',{}).items() if role_from_meta(row) in SUPPORT_ROLES}
    world=DocumentWorld(doc);meta=doc.metadata
    spans=[(int(a),int(b),biome) for a,b,biome in meta.get('biome_spans',[]) if biome in ('swamp','rainforest')]
    if not spans:return None
    start=max(0,int(meta.get('underground_start_row',doc.height)))
    protected=[]
    for r in meta.get('creature_spawns',[]):
        if r.get('species') in ('queen_bee','loch_ness_monster','abyss_colossus','congo_kong','trihead_dragon'):
            protected.append((float(r.get('x',0)),float(r.get('floor',0)),18))
    for r in meta.get('editor_objects',[]):protected.append((float(r.get('x',0)),float(r.get('y',0)),10))
    for r in meta.get('portals',[]):protected.append((float(r.get('x',0)),float(r.get('y',0)),8))
    choices=[]
    for a,b,biome in spans:
        for fy in range(start+5,min(doc.height-4,start+53)):
            for tx in range(max(a+6,6),min(b-6,doc.width-6)):
                if world.solid_top_y(tx,fy) is None:continue
                if any(abs(tx-x)<dist and abs(fy-y)<10 for x,y,dist in protected):continue
                try:x,y=locate(world,doc.layers['water'],cfg,tx,fy-1)
                except ValueError:continue
                if abs(y/T-fy)>1:continue
                # 11-tile-wide, 5-tile-high clear arena; conservative about
                # custom platforms and all authored liquid/hazard layers.
                if occupied(world,x,y,11*T,5*T):continue
                if any(float(doc.layers.get(layer,{}).get((xx,yy),0) or 0)>0
                       for layer in ('water','lava','honey','swamp')
                       for xx in range(tx-5,tx+6) for yy in range(fy-5,fy)):continue
                tops=[world.solid_top_y(xx,fy) for xx in range(tx-5,tx+6)]
                if any(t is None or abs(t-y)>T/3+.2 for t in tops):continue
                score=(0 if biome=='swamp' else 1,abs(tx-(a+b)*.5),fy)
                choices.append((score,dict(id=POINT_ID,kind='creature',species='giant_python',
                    name='巨型大蟒蛇',x=tx,y=fy-1,count=1,spacing=2,patrol=2.,
                    respawn=True,respawn_seconds=300.,anchor='auto',enabled=True,
                    underground=True,habitat=HABITAT,preview_feet=[x/T,y/T])))
    return min(choices,key=lambda p:p[0])[1] if choices else None

def place_home(root,map_name='editor_map.json',dry_run=False):
    root=Path(root).resolve();path=(root/'maps'/map_name).resolve()
    if path.parent != (root/'maps').resolve():raise ValueError('只允許 maps 下的地圖 JSON')
    before=path.read_bytes();data=json.loads(before.decode('utf-8'));meta=data.setdefault('metadata',{})
    points=meta.get('editor_objects',[])
    if not isinstance(points,list):raise ValueError('地圖放置點格式錯誤，未修改')
    if any(r.get('species')=='giant_python' for r in points+meta.get('creature_spawns',[])):
        return dict(status='exists',message='此地圖已有巨型大蟒蛇，保留原位置，不再新增。')
    if meta.get('fix102_home_registered'):
        return dict(status='removed',message='此地圖已設定過主場；保留你後來移除或修改的結果，不自動補回。')
    if len(points)>=MAX_POINTS or sum(int(r.get('count',1)) for r in points if r.get('kind')=='creature')>=MAX_ACTORS:
        raise ValueError('手動放置點／生物數已達上限，未修改')
    doc=MapDocument.load(str(path));row=find_home(root,doc)
    if row is None:return dict(status='no_space',message='現有沼澤／雨林地下沒有通過檢查的 11×5 格空間。未挖改地形；請在地圖編輯器手動放置。')
    result=dict(status='placed',point=row,map=map_name,dry_run=dry_run)
    if dry_run:return result
    # Preserve all JSON fields verbatim in value; keep the SAME .ptw reference.
    # Guard against concurrent editor saves before committing the new metadata.
    if path.read_bytes()!=before:raise RuntimeError('地圖在掃描期間已被修改，未寫入，請關閉編輯器再試。')
    meta['editor_objects']=points+[row];meta['fix102_home_registered']=True
    meta['editor_revision']=uuid.uuid4().hex
    backup=root/'_backups'/('FIX102_home_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True);(backup/(path.stem+'.original.json')).write_bytes(before)
    backup_data=json.loads(before.decode('utf-8'))
    storage=backup_data.get('terrain_storage',{})
    rel=storage.get('path','')
    if rel:
        binary=(path.parent/rel).resolve()
        if path.parent not in binary.parents:raise ValueError('備份地形路徑超出 maps，未修改地圖')
        if not binary.is_file():raise ValueError('備份找不到地形檔，未修改地圖')
        shutil.copy2(binary,backup/binary.name)
        storage['path']=binary.name
    (backup/path.name).write_text(json.dumps(backup_data,ensure_ascii=False),encoding='utf-8')
    blob=(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
    tmp=path.with_name(path.name+'.fix102_home.tmp')
    try:
        with tmp.open('wb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()
    result['backup']=str(backup);return result
