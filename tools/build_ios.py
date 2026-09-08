"""macOS/Xcode only: export and compile a real unsigned arm64 IPA for sideload signing."""
import hashlib,json,os,plistlib,shutil,subprocess,sys,zipfile
from pathlib import Path
if sys.platform!='darwin': raise SystemExit('Use GitHub Actions macOS runner to build this IPA.')
root=Path(__file__).resolve().parents[1]; os.chdir(root)
output=root/'build'; (output/'ios').mkdir(parents=True,exist_ok=True)
def run(args): subprocess.run([str(a) for a in args],check=True)
run([os.environ['GODOT'],'--headless','--export-release','iOS',output/'ios/PytoRPG.zip'])
project=output/'ios/PytoRPG.xcodeproj'
if not project.is_dir(): raise SystemExit('Godot did not produce the Xcode project')
run(['xcodebuild','-version'])
run(['xcodebuild','-project',project,'-scheme','PytoRPG','-configuration','Release','-sdk','iphoneos','-destination','generic/platform=iOS','-derivedDataPath',output/'derived','CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO','CODE_SIGN_IDENTITY=','DEVELOPMENT_TEAM=','CURRENT_PROJECT_VERSION='+os.environ.get('GITHUB_RUN_NUMBER','1'),'TARGETED_DEVICE_FAMILY=1,2','build'])
app=output/'derived/Build/Products/Release-iphoneos/PytoRPG.app'
with (app/'Info.plist').open('rb') as file: info=plistlib.load(file)
executable=app/info['CFBundleExecutable']
if not executable.is_file(): raise SystemExit('Missing compiled app executable')
run(['lipo',executable,'-verify_arch','arm64'])
if not {1,2}.issubset(set(info.get('UIDeviceFamily',[]))): raise SystemExit('iPhone/iPad device families missing')
payload=output/'package/Payload'; payload.mkdir(parents=True,exist_ok=True)
shutil.copytree(app,payload/app.name,dirs_exist_ok=True)
ipa=output/'PytoRPG-unsigned.ipa'
run(['ditto','-c','-k','--sequesterRsrc','--keepParent',payload,ipa])
with zipfile.ZipFile(ipa) as archive:
    if archive.testzip(): raise SystemExit('IPA ZIP integrity check failed')
    if 'Payload/PytoRPG.app/Info.plist' not in archive.namelist(): raise SystemExit('Invalid IPA structure')
checksum=hashlib.sha256(ipa.read_bytes()).hexdigest()
(output/'PytoRPG-unsigned.ipa.sha256').write_text(checksum+'  '+ipa.name+'\n',encoding='utf8')
(output/'build-info.json').write_text(json.dumps({'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'workflow_commit':os.environ.get('GITHUB_SHA'),'run':os.environ.get('GITHUB_RUN_NUMBER'),'version':info.get('CFBundleShortVersionString'),'bundle_id':info.get('CFBundleIdentifier'),'device_families':info.get('UIDeviceFamily'),'sha256':checksum,'signed':False},indent=2),encoding='utf8')
print('IPA_OK '+str(ipa),flush=True)
