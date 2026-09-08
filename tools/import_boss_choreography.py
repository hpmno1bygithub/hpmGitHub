"""Bake the original pure FIX116 hit-box functions; no Pyto runtime import."""
import ast
import json
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'reference/systems/fix103_combat.py'
tree=ast.parse(path.read_text(encoding='utf-8-sig'))
source=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Fix103Combat')
names={'_front_span','_normal_boxes','_effect_box','_world_box','_pulse_boxes'}
body=[n for n in source.body if isinstance(n,ast.Assign) or isinstance(n,ast.FunctionDef) and n.name in names]
module=ast.Module(body=[ast.ClassDef(name='Geometry',bases=[],keywords=[],body=body,decorator_list=[])],type_ignores=[])
env={'T':40.0}
exec(compile(ast.fix_missing_locations(module),str(path),'exec'),env)
geometry=env['Geometry']()

def data_eval(node):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='dict' and not node.args:
        return {k.arg:data_eval(k.value) for k in node.keywords}
    if isinstance(node,ast.Dict): return {data_eval(k):data_eval(v) for k,v in zip(node.keys,node.values)}
    return ast.literal_eval(node)
defs={}
for n in ast.parse((ROOT/'reference/systems/fix103_defs.py').read_text(encoding='utf-8-sig')).body:
    if isinstance(n,ast.Assign):
        for t in n.targets:
            if isinstance(t,ast.Name) and t.id in {'WEAPONS','REWARDS'}: defs[t.id]=data_eval(n.value)

result={'provenance':'reference/systems/fix103_combat.py (FIX116)','rewards':defs['REWARDS'],'weapons':defs['WEAPONS'],'patterns':{}}
owner=types.SimpleNamespace(x=0,y=0,facing=1,width=lambda:0)
for weapon in defs['WEAPONS']:
    event={'weapon':weapon,'sx':0,'base':0,'facing':1}
    beats=geometry.HEAVY_BEATS[weapon]
    result['patterns'][weapon]={'life':geometry.HEAVY_LIFE[weapon],'beats':beats,'normal':geometry._normal_boxes(owner,weapon)[0],
        'envelope':geometry._effect_box(event),'pulses':[geometry._pulse_boxes(event,i) for i in range(len(beats))],
        'effect':geometry.HEAVY_EFFECT_ASSETS[weapon]}
(ROOT/'data/boss_choreography.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
base=ROOT/'data/weapons.json'; weapons=json.loads(base.read_text(encoding='utf-8')); weapons.update(defs['WEAPONS'])
base.write_text(json.dumps(weapons,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print('BOSS_CHOREOGRAPHY_OK 6 source families; exact normal boxes and heavy pulse geometry')
