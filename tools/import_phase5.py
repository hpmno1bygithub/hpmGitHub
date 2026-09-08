"""Extract source constants and tool tables without importing the Pyto runtime."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / 'reference'

def constants(path):
    values = {}
    for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
    return values

cfg = constants(REF/'config.py')
tiles = constants(REF/'world/tile_registry.py')
tree = ast.parse((REF/'systems/tool_system.py').read_text(encoding='utf-8-sig'))
class ReplaceNames(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Constant(tiles[node.id]), node) if node.id in tiles else node

tables = {}
for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
        continue
    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
    if names and names[0] in ['HARDNESS','SOIL_TILES','ROCK_TILES','WOOD_TILES','drops']:
        value = ast.literal_eval(ReplaceNames().visit(node.value))
        tables[names[0].lower()] = sorted(value) if isinstance(value, set) else value

data = {'provenance': ['reference/config.py','reference/systems/tool_system.py'],
        'constants': {k:v for k,v in cfg.items() if k.startswith(('MAGIC_', 'FIREBALL_', 'WATERBALL_', 'ICEBALL_', 'ELECTRICBALL_', 'MANA_', 'TOOL_', 'ICE_LEVEL'))},
        'tools': tables}
(ROOT/'data/phase5_rules.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print('PHASE5_SOURCE_RULES_OK')
