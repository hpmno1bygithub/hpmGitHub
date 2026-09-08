"""Extract FIX131 gameplay data and authored animation atlases. No game imports."""
import ast
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,ensure_ascii=False,separators=(',',':')),encoding='utf-8')

def extract(path, target):
    values = {}
    def value(n):
        if isinstance(n,ast.Name): return values[n.id]
        if isinstance(n,ast.Dict): return {value(k):value(v) for k,v in zip(n.keys,n.values)}
        if isinstance(n,(ast.Tuple,ast.List)): return [value(v) for v in n.elts]
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='dict':
            return {k.arg:value(k.value) for k in n.keywords}
        return ast.literal_eval(n)
    for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
            try: values[name]=value(node.value)
            except (ValueError,KeyError,TypeError): continue
    return values[target]

def rgba(rows):
    im=Image.new('RGBA',(len(rows[0]),len(rows)))
    im.putdata([tuple(bytes.fromhex(c[1:])) if len(c)==9 else tuple(bytes.fromhex(c[1:]))+(255,)
                for row in rows for c in row])
    return im

def main():
    definitions=extract(ROOT/'reference/systems/biome_system.py','CREATURE_ARCHETYPES')
    for file in ['python102_defs','fix103_defs','fauna99_defs']:
        for name,cfg in extract(ROOT/f'reference/systems/{file}.py','CREATURES').items():
            definitions.setdefault(name,cfg)
    backgrounds=read(ROOT/'assets/underground_background_catalog.json')
    for path in (ROOT/'assets/secondary_worlds').glob('*.json'):
        data=read(path); definitions.update(data.get('creature_archetypes',{}))
        backgrounds['profiles'].update(data.get('background_profiles',{}))
    definitions.update(read(ROOT/'assets/custom_creatures.json')['creatures'])
    write(ROOT/'data/creatures.json',definitions)
    # Extract only the original pure silhouette functions; no runtime/module initialization.
    tree=ast.parse((ROOT/'reference/systems/underground_background.py').read_text(encoding='utf-8'))
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_part','_rgba_alpha','_primitive_parts')]
    scope={};exec(compile(ast.Module(body=functions,type_ignores=[]),'original_silhouettes','exec'),scope)
    def color(raw): return tuple(v/255 for v in bytes.fromhex(raw.lstrip('#'))) + (1.,)
    for profile in backgrounds['profiles'].values():
        for layer in profile['layers']:
            colors=layer.get('colors',['#334455']);accent=color(layer.get('accent',colors[0]))
            for motif in layer['motifs']:
                if 'primitive' in motif:
                    motif['parts_variants']=[scope['_primitive_parts'](motif['primitive'],1,color(c),accent) for c in colors]
                else:
                    palette={'base':color(colors[0]),'accent':accent}
                    palette.update({'color'+str(i):color(c) for i,c in enumerate(colors)})
                    motif['parts_variants']=[[[*p[:4],palette.get(p[4],accent)] for p in motif['parts']]]
    write(ROOT/'data/backgrounds.json',backgrounds)
    write(ROOT/'data/weapons.json',extract(ROOT/'reference/systems/weapon_catalog.py','WEAPON_DEFS'))
    manifest={}; anchors={}; total_frames=0
    for path in sorted((ROOT/'assets/pixel_sources').glob('*.json')):
        data=read(path); states=data.get('animations',{}); all_frames=[]; animations={}
        base=rgba(data['pixels']); bounds=base.getbbox() or (0,0,base.width,base.height)
        anchors[data['asset_id']]={'width':base.width,'height':base.height,'foot_y':bounds[3]}
        for state,anim in states.items():
            frames=anim.get('frames',[])
            if not frames: continue
            indices=[]
            for frame in frames:
                indices.append(len(all_frames)); all_frames.append(rgba(frame))
            animations[state]={'indices':indices,'fps':anim.get('fps',6),'loop':anim.get('loop',True)}
        if not all_frames: continue
        w=max(f.width for f in all_frames);h=max(f.height for f in all_frames)
        columns=min(16,len(all_frames)); sheet=Image.new('RGBA',(w*columns,h*((len(all_frames)+columns-1)//columns)))
        for i,frame in enumerate(all_frames): sheet.paste(frame,((i%columns)*w,(i//columns)*h))
        dest=ROOT/'assets/animated'/f'{path.stem}.png';dest.parent.mkdir(exist_ok=True);sheet.save(dest)
        manifest[data['asset_id']]={'texture':'res://assets/animated/'+dest.name,'width':w,'height':h,
            'columns':columns,'states':animations,'scale':data.get('pixel_world_scale',2.5),
            'mapping':data.get('pixel_mapping','fixed_world_pixel')}
        total_frames+=len(all_frames)
    write(ROOT/'data/animations.json',manifest)
    write(ROOT/'data/anchors.json',anchors)
    report={'species':len(definitions),'animated_assets':len(manifest),'frames':total_frames,'maps':{},'missing_species':[]}
    for path in sorted((ROOT/'data/maps').glob('*.json')):
        data=read(path); pop_path=ROOT/'reference/maps'/f'.runtime_population_{path.stem}.pop'
        pop=read(pop_path)
        # Cache contains generated fauna BEFORE editor objects are spawned.
        # Keep generated fauna, rebuild explicit spawns from current metadata.
        assert pop['count']==len(pop['creatures'])
        ids=[c['entity_id'] for c in pop['creatures']];assert len(ids)==len(set(ids))
        creatures=[c for c in pop['creatures'] if c['origin']=='auto' and not c['entity_id'].startswith('editor_spawn:')]
        spawns=data['metadata'].get('creature_spawns',[])
        if not spawns: creatures += [c for c in pop['creatures'] if c['origin']=='authored']
        for n,row in enumerate(spawns):
            for i in range(int(row.get('count',1))):
                creatures.append(dict(row,entity_id=f'authored:{path.stem}:{n}:{i}',origin='authored',
                    x=float(row['x'])+.5+i*float(row.get('spacing',2)),y=float(row.get('floor',row.get('y',12))),
                    patrol=row.get('patrol_tiles',3),resolve_floor=True))
        for row in data['metadata'].get('editor_objects',[]):
            if row.get('kind')!='creature' or not row.get('enabled',True):continue
            for i in range(min(16,int(row.get('count',1)))):
                feet=row.get('preview_feet',[row['x']+.5,row['y']+1])
                creatures.append(dict(row,entity_id=f"editor:{path.stem}:{row['id']}:{i}",origin='editor',
                    x=float(feet[0])+i*float(row.get('spacing',2)),y=float(feet[1]),resolve_floor=i>0))
        missing=sorted({c['species'] for c in creatures} - definitions.keys())
        report['missing_species']+=missing
        write(ROOT/'data/populations'/path.name,creatures)
        report['maps'][path.stem]={'creatures':len(creatures),'custom_cells':len(data['layers'].get('custom_map',[])),
            'generated_fauna_source':'original population snapshot','snapshot_revision_matches':pop.get('editor_revision','')==data['metadata'].get('editor_revision','')}
    assert not report['missing_species'],report['missing_species']
    write(ROOT/'docs/gameplay-import.json',report)
    print(json.dumps(report,ensure_ascii=True))

if __name__=='__main__': main()
