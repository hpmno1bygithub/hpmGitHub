"""Validate source-to-Godot links and current editor placements. Standard library only."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
art=read(ROOT/'data/pixel_manifest.json')
definitions=read(ROOT/'data/creatures.json')
total=0
for path in (ROOT/'data/populations').glob('*.json'):
    population=read(path); ids=[row['entity_id'] for row in population]
    assert len(set(ids))==len(ids),path
    for row in population:
        assert row['species'] in definitions
        assert 'creature.'+row['species'] in art, row['species']
    world=read(ROOT/'data/maps'/path.name)
    for row in world['metadata'].get('editor_objects',[]):
        if row.get('kind')=='creature' and row.get('enabled',True):
            for i in range(min(16,int(row.get('count',1)))):
                assert f"editor:{path.stem}:{row['id']}:{i}" in ids
    for cell in world['layers'].get('custom_map',[]):assert cell[2] in art
    total+=len(population)
for path in art.values():assert (ROOT/path.removeprefix('res://')).exists(),path
animations=read(ROOT/'data/animations.json')
for entry in animations.values():
    assert (ROOT/entry['texture'].removeprefix('res://')).exists()
    assert entry['width']>0 and entry['height']>0
    for state in entry['states'].values():assert state['indices'] and state['fps']>0
bank=read(ROOT/'assets/study_questions.json')['questions'];ids=set()
for q in bank:
    assert q['id'] not in ids; ids.add(q['id'])
    assert len(q['choices'])==4 and len(set(q['choices']))==4
    assert q['choices'].count(q['answer'])==1
    assert q['tokens'].count('__')==1 and len(q['tokens'])==len(q['reference_text'])
    assert q['choice_characters'][q['choices'].index(q['answer'])]==q['reference_text'][q['tokens'].index('__')]
print(f'GAMEPLAY_DATA_OK {total} actors, {len(animations)} animated assets, {len(bank)} validated Zhuyin questions')
