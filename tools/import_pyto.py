"""Convert FIX131 authored data without importing/executing the Python game.
Usage: python tools/import_pyto.py PATH_TO_EXTRACTED_PYTO_PROJECT
Requires Pillow. Original archive is never changed.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import zipfile
import zlib
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1]

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

def terrain(path):
    raw = path.read_bytes()
    magic, ver, cs, width, height, count, flags, chunks, surface = struct.unpack_from('<8sHHIIHHII', raw)
    assert magic == b'PTRPGW71' and ver == 1 and cs == 16
    pos = 32
    palette = []
    for _ in range(count):
        size = struct.unpack_from('<H', raw, pos)[0]; pos += 2
        palette.append(raw[pos:pos+size].decode()); pos += size
    pos += surface
    cells = {}
    for _ in range(chunks):
        cx, cy, offset, size, raw_size, non_air = struct.unpack_from('<iiQIIH2x', raw, pos); pos += 28
        data = zlib.decompress(raw[offset:offset+size])
        assert len(data) == raw_size == cs * cs * 2
        codes = struct.unpack('<256H', data)
        assert sum(v != 0 for v in codes) == non_air
        for i, code in enumerate(codes):
            if code:
                x, y = cx * cs + i % cs, cy * cs + i // cs
                assert 0 <= x < width and 0 <= y < height and code < len(palette)
                cells[x,y] = palette[code]
    return cells

def main(src):
    assets = OUT / 'assets'
    shutil.copytree(src / 'assets', assets, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('._*', '*.bak', '.DS_Store', 'inbox'))
    with zipfile.ZipFile(src / 'rpg_runtime.zip') as archive:
        registry = ast.parse(archive.read('world/tile_registry.py').decode('utf-8-sig'))
        pyfiles = [n for n in archive.namelist() if n.endswith('.py')]
        inventory = [{'path':n, 'lines':len(archive.read(n).splitlines())} for n in pyfiles]
        (OUT / 'reference').mkdir(exist_ok=True)
        (OUT / 'reference/.gdignore').write_text('')
        for n in pyfiles:
            target = OUT / 'reference' / n
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(n))
    names = {}
    definitions = {}
    for node in registry.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if isinstance(node.value, ast.Constant): names[name] = node.value.value
            if name == 'TILES':
                for call in node.value.values:
                    ident = names[call.args[0].id]
                    definitions[str(ident)] = dict(id=ident, name=ast.literal_eval(call.args[1]),
                        **{k.arg: ast.literal_eval(k.value) for k in call.keywords})
    dump(OUT / 'data/tiles.json', definitions)
    by_name = {v['name']: v['id'] for v in definitions.values()}
    pixel_manifest = {}
    generated = assets / 'converted'; generated.mkdir(exist_ok=True)
    for p in sorted((assets/'pixel_sources').glob('*.json')):
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        rows = data.get('pixels')
        if not rows: continue
        im = Image.new('RGBA', (len(rows[0]),len(rows)))
        im.putdata([tuple(bytes.fromhex(c.lstrip('#'))) if len(c.lstrip('#')) == 8
                    else tuple(bytes.fromhex(c.lstrip('#'))) + (255,) for row in rows for c in row])
        im.save(generated / (p.stem + '.png'))
        pixel_manifest[data.get('asset_id',p.stem)] = 'res://assets/converted/' + p.stem + '.png'
    dump(OUT / 'data/pixel_manifest.json', pixel_manifest)
    atlas = Image.new('RGBA',(40 * len(definitions),40 * 8))
    for key, d in definitions.items():
        if d['name'] == 'air': continue
        tile = Image.new('RGBA',(40,40),d['color']); draw = ImageDraw.Draw(tile)
        draw.rectangle((0,0,39,4), fill=d.get('top_color',d['color']))
        sprite = generated / ('tile_' + d['name'] + '.png')
        if sprite.exists(): tile = Image.open(sprite).convert('RGBA').resize((40,40),Image.Resampling.NEAREST)
        for mask in range(1,8):
            variant = tile.copy()
            for row in range(40):
                bit = 4 if row < 40/3 else 2 if row < 80/3 else 1
                if not mask & bit:
                    for x in range(40): variant.putpixel((x,row),(0,0,0,0))
            atlas.paste(variant,(int(key)*40,mask*40))
    atlas.save(generated / 'terrain_atlas.png')
    report = {'runtime':'0.7.7.7-FIX131-creature-ecology-elements', 'python_files':inventory,
              'python_lines':sum(p['lines'] for p in inventory), 'converted_pixel_assets':len(pixel_manifest), 'maps':[]}
    shutil.copytree(src/'maps', OUT/'reference/maps', dirs_exist_ok=True)
    for path in sorted((src/'maps').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        storage = data.get('terrain_storage',{})
        cells = terrain(path.parent/storage['path']) if storage else {}
        layers = data.get('layers',{})
        for x,y,name,*_ in layers.get('terrain',[]):
            if name == 'air': cells.pop((x,y),None)
            else: cells[x,y] = name
        layered = {1,2,3,5,7,8,9,10,11,12,13,14,15,16,17,18,19,20}
        masks = {}
        # Original loader: explicit positive masks override metadata; only layered solids use masks.
        for row in list(data.get('metadata',{}).get('initial_ground_layers',[])) + list(layers.get('ground_layer',[])):
            x,y,mask = row[:3]
            mask = int(mask)&7
            if mask and by_name.get(cells.get((x,y))) in layered: masks[x,y] = mask
        output = {'schema':1,'id':path.stem,'size':data['size'],'spawn':data['player_spawn'],
                  'cells':[[x,y,by_name[name],masks.get((x,y),7)] for (x,y),name in sorted(cells.items())],
                  'metadata':data.get('metadata',{}),'layers':layers}
        dump(OUT/'data/maps'/(path.stem+'.json'),output)
        report['maps'].append({'id':path.stem,'cells':len(cells),'size':data['size'],
            'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'terrain_file':storage.get('path')})
    dump(OUT/'data/maps.json',[m['id'] for m in report['maps']])
    dump(OUT/'docs/import_report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k != 'python_files'},ensure_ascii=False,indent=2))

if __name__ == '__main__': main(Path(sys.argv[1]).resolve())
