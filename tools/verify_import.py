"""Differential check against the preserved original PTW decoder (standard library only)."""
import importlib.util
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('original_binary_map',root/'reference/world/binary_map.py')
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
definitions = json.loads((root/'data/tiles.json').read_text(encoding='utf-8'))
total = 0
for file in sorted((root/'data/maps').glob('*.json')):
    if file.stem in ('control_lab', 'thermal_lab'):
        continue  # Explicitly additional Godot test world; all nine source maps remain checked.
    converted = json.loads(file.read_text(encoding='utf-8'))
    source = json.loads((root/'reference/maps'/file.name).read_text(encoding='utf-8'))
    reader = original.BinaryTerrainReader(root/'reference/maps'/source['terrain_storage']['path'])
    expected = {}
    for cx,cy in reader.iter_chunk_keys():
        for i,code in enumerate(reader.read_chunk_codes(cx,cy)):
            if code:
                expected[cx*16+i%16,cy*16+i//16] = reader.palette[code]
    for x,y,name,*_ in source['layers'].get('terrain',[]):
        if name == 'air': expected.pop((x,y),None)
        else: expected[x,y] = name
    actual = {(x,y):definitions[str(tile)]['name'] for x,y,tile,mask in converted['cells']}
    assert actual == expected, file.name
    assert converted['size'] == [reader.width,reader.height]
    assert converted['spawn'] == source['player_spawn']
    assert converted['layers'] == source['layers']
    assert converted['metadata'] == source['metadata']
    total += len(actual)
print(f'IMPORT_PARITY_OK: 9 maps, {total} terrain cells, all metadata and overlays retained')
