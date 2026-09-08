"""Run Godot tests with marker checks, compiler warnings, and a hard process timeout."""
import os, subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
godot=os.environ['GODOT']
logs=root/'build/logs'; logs.mkdir(parents=True,exist_ok=True)
cases=[('smoke','SMOKE_OK'),('gameplay','PHASE2_OK'),('phase3','PHASE3_OK'),('phase4','PHASE4_OK'),('phase5','PHASE5_OK'),('phase6','PHASE6_OK')]
for name,marker in [('import',''),*cases]:
    args=['--headless','--path',str(root)]
    args+=['--editor','--import','--quit'] if name=='import' else ['--debug','--ignore-error-breaks','--fixed-fps','60','--quit-after','7000','--script',f'res://tests/{name}.gd']
    result=subprocess.run([godot,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
    output=result.stdout.decode('utf-8',errors='replace')
    (logs/f'{name}.log').write_text(output,encoding='utf-8')
    errors=[line for line in output.splitlines() if any(term in line for term in ['ERROR:','WARNING:','SCRIPT ERROR','Parse Error']) and 'Failed to read the root certificate store' not in line]
    if result.returncode or (marker and marker not in output) or errors:
        print(output)
        raise SystemExit(f'{name} FAILED ({result.returncode})')
    print(f'{name}: {marker or "IMPORT_OK"}',flush=True)
