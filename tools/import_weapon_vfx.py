"""Bake original FIX54 renderer quads; no UIKit/Metal runtime is imported."""
import ast, json, math
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT/'reference/ios_host/metal_renderer.py').read_text(encoding='utf-8'))
block = next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='heavy and swinging and (not authored_effect)')
code = compile(ast.fix_missing_locations(ast.Module(body=[block],type_ignores=[])), 'original_fix54_heavy', 'exec')
weapons = json.loads((ROOT/'data/weapons.json').read_text(encoding='utf-8'))
def rgba(value,alpha=1):
 return tuple(int(value[i:i+2],16)/255 for i in (1,3,5))+(alpha,)
def smooth(t): return t*t*(3-2*t)
def angle(row, t):
 a,b=row['heavy_start_angle'],row['heavy_end_angle']; style=row['heavy_style']
 if style=='dagger_flurry': t=.5+.5*math.sin(t*math.pi*7)
 elif style=='ground_slam':
  if t<.36: return -58+(a+58)*smooth(t/.36)
  t=((t-.36)/.64)**2
 elif style not in ('lance_breaker','whip_spin'): t=smooth(t)
 return a+(b-a)*t
result={'source':'reference/ios_host/metal_renderer.py:8433 (FIX54)','samples':61,'weapons':{}}
for weapon,row in weapons.items():
 frames=[]
 for frame in range(61):
  t=frame/60
  env={'math':math,'heavy':True,'swinging':True,'authored_effect':False,'style':row['heavy_style'],
       'weapon':weapon,'wd':row,'progress':t,'now':t,'length':max(14,row['visual_length']),
       'attack_angle':angle(row,t),'facing':1,'hand_x':7,'hand_y':-1.2,'render_player_x':0,'render_player_y':20,
       'TILE_SIZE':40,'p':SimpleNamespace(height=lambda:40),'_smooth':smooth,
       '_basis':lambda a:(math.cos(math.radians(a)),math.sin(math.radians(a)),-math.sin(math.radians(a)),math.cos(math.radians(a))),
       'SWORD_BLADE_RGBA':rgba('#D9E0E7'),'SWORD_EDGE_RGBA':rgba('#F5F7FA'),'SWORD_HANDLE_RGBA':rgba('#76502B'),
       'MINIMAP_ROCK_RGBA':rgba('#626872',.94),'SEAGRASS_RGBA':(.12,.58,.34,.76),'SEAGRASS_TIP_RGBA':(.22,.72,.42,.70),
       'self':SimpleNamespace(_quad=lambda x,y,w,h,c:[x,y,w,h,list(c)]),'quads':[]}
  exec(code,env)
  frames.append(env['quads'])
 result['weapons'][weapon]=frames
(ROOT/'data/weapon_vfx.json').write_text(json.dumps(result,separators=(',',':')),encoding='utf-8')
print('ORIGINAL_VFX_OK',len(result['weapons']),'weapons, 61 samples each')
