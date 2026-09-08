"""Install a pinned official Godot release in CI; verify release SHA-512 sums."""
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
import zipfile

VERSION = '4.5.1'
BASE = f'https://github.com/godotengine/godot/releases/download/{VERSION}-stable/'

def install(target='check'):
    dest = Path(os.environ.get('RUNNER_TEMP', '.ci')) / 'godot'
    dest.mkdir(parents=True,exist_ok=True)
    sums = urllib.request.urlopen(BASE+'SHA512-SUMS.txt').read().decode()
    checksums = {line.split()[1].lstrip('*'):line.split()[0] for line in sums.splitlines() if line.strip()}
    def download(name):
        path = dest / name
        urllib.request.urlretrieve(BASE+name,path)
        with path.open('rb') as file:
            actual = hashlib.file_digest(file,'sha512').hexdigest()
        if actual != checksums[name]: raise RuntimeError('Checksum mismatch: '+name)
        return path
    mac = platform.system() == 'Darwin'
    name = f'Godot_v{VERSION}-stable_' + ('macos.universal.zip' if mac else 'linux.x86_64.zip')
    archive = download(name)
    if mac:
        subprocess.run(['ditto','-x','-k',str(archive),str(dest)],check=True)
        binary = dest/'Godot.app/Contents/MacOS/Godot'
    else:
        with zipfile.ZipFile(archive) as z: z.extractall(dest)
        binary = dest/f'Godot_v{VERSION}-stable_linux.x86_64'
    binary.chmod(0o755)
    if target != 'check':
        tpz = download(f'Godot_v{VERSION}-stable_export_templates.tpz')
        template_dir = (Path.home()/'Library/Application Support/Godot/export_templates' if mac else
                        Path.home()/'.local/share/godot/export_templates') / (VERSION+'.stable')
        template_dir.mkdir(parents=True,exist_ok=True)
        needed = ['ios.zip'] if target == 'ios' else ['windows_release_x86_64.exe']
        with zipfile.ZipFile(tpz) as z:
            for file in needed:
                (template_dir/file).write_bytes(z.read('templates/'+file))
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'],'a') as file: file.write(f'GODOT={binary}\n')
    print(binary)

if __name__ == '__main__': install(sys.argv[1] if len(sys.argv)>1 else 'check')
