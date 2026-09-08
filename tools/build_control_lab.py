"""A separate Godot test world, never a replacement for the nine original maps."""
import json
from pathlib import Path
p=Path(__file__).resolve().parents[1]
cells=[]
for x in range(150):
 for y in range(20,30): cells.append([x,y,1 if y<23 else 3,7])
for x in range(20,25):
 for y in range(12,15): cells.append([x,y,3,7])
for y in range(15,20): cells.append([22,y,4,7])
population=[]
for i,species in enumerate(['wolf','lava_beetle_emperor','crystal_nine_tail','abyss_crab_king','thunder_roc','drill_worm','ancient_tree_demon','giant_python','congo_kong']):
 population.append({'entity_id':'lab_'+species,'species':species,'x':32+i*13,'y':20,'resolve_floor':True,'move_mode':'hold','behavior_mode':'retaliate','detect_tiles':15,'respawn':True,'respawn_seconds':8,'actions_enabled':True})
world={'size':[150,35],'spawn':[8,20],'cells':cells,'layers':{'vegetation':[[12,20,'tree',3],[15,20,'pine',3]],'decoration':[], 'custom_map':[], 'water':[], 'lava':[], 'honey':[], 'hazard':[]},'metadata':{'name':'工具與戰鬥試驗場','editor_objects':[],'portals':[]},'population':population}
(p/'data/maps/control_lab.json').write_text(json.dumps(world,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
(p/'data/populations/control_lab.json').write_text(json.dumps(population,ensure_ascii=False),encoding='utf-8')
print('CONTROL_LAB_OK')
