"""Build clean-room Windows payload + one-click per-user installer. CI owns execution."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'dynamic_teleprompter'
sys.path.insert(0, str(APP))
from version import VERSION, RELEASE_NOTES, APP_NAME
from update_core import sha256, PACKAGE_NAME


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def build():
    if os.name != 'nt':
        raise RuntimeError('Windows builds must run on Windows')
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    (ROOT / 'build').mkdir(exist_ok=True)
    version_info = ROOT / 'build/suite-version.txt'
    version_info.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers=(0,5,0,18), prodvers=(0,5,0,18), "
        "mask=0x3f, flags=0x2, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', 'Cosmere Archives'), "
        "StringStruct('FileDescription', '" + APP_NAME + "'), StringStruct('ProductName', '" + APP_NAME + "'), "
        "StringStruct('FileVersion', '" + VERSION + "'), StringStruct('ProductVersion', '" + VERSION + "')])]), "
        "VarFileInfo([VarStruct('Translation', [1033,1200])])])", encoding='utf-8')
    # Build from enumerated sources/dependencies, never copy an existing install.
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'CosmereCamLauncher', '--version-file', version_info, '--paths', APP, APP / 'windows_launcher.py')
    run('cmake', '-S', APP / 'native', '-B', ROOT / 'build/native', '-A', 'x64')
    run('cmake', '--build', ROOT / 'build/native', '--config', 'Release')
    native = ROOT / 'build/native/Release/dt_engine.dll'
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', 'CosmereCam', '--version-file', version_info, '--paths', APP,
        '--collect-all', 'vosk', '--collect-all', 'sounddevice',
        '--collect-all', 'imageio_ffmpeg', '--collect-all', 'spellchecker',
        '--collect-all', 'pedalboard', '--collect-all', 'cv2',
        '--hidden-import', 'keyring.backends.Windows', '--hidden-import', 'pygrabber.dshow_graph',
        '--add-binary', str(native) + ';native',
        '--add-binary', str(ROOT / 'build/native/Release/cosmere_vst2_host.exe') + ';native', APP / 'app_entry.py')
    payload = out / 'CosmereCam'
    shutil.copyfile(out / 'CosmereCamLauncher.exe', payload / 'CosmereCamLauncher.exe')
    license_dir = payload / 'licenses'
    license_dir.mkdir(exist_ok=True)
    helper_license = license_dir / 'CosmereVST2Host'
    helper_license.mkdir(exist_ok=True)
    for name in ('COPYING', 'README.md', 'UPSTREAM.md'):
        shutil.copyfile(APP / 'native/vst2' / name, helper_license / name)
    # Dependency notices/metadata accompany bundled open-source components.
    import importlib.metadata
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        if not name:
            continue
        destination = license_dir / name
        destination.mkdir(exist_ok=True)
        (destination / 'METADATA.txt').write_text(
            distribution.read_text('METADATA') or distribution.read_text('PKG-INFO') or
            (name + ' ' + distribution.version), encoding='utf-8')
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ('license', 'copying', 'notice')):
                source = distribution.locate_file(file)
                if source.is_file() and source.stat().st_size < 2_000_000:
                    shutil.copyfile(source, destination / (hashlib.sha256(str(file).encode()).hexdigest()[:8] + '-' + source.name))
    files = {p.relative_to(payload).as_posix(): sha256(p)
             for p in payload.rglob('*') if p.is_file() and p != payload / 'package.json'}
    for name in files:
        lower = name.lower()
        if any(item in lower for item in ('live2dcubismcore', 'cubismsdkfornative', '.moc3', '.model3.json', '.vst3',
                                         'reacomp-standalone.dll', 'reafir_standalone.dll', 'reaeq-standalone.dll', 'reagate-standalone.dll')):
            raise RuntimeError('Proprietary asset detected in payload')
    (payload / 'package.json').write_text(json.dumps({'schema': 1, 'version': VERSION,
        'notes': RELEASE_NOTES, 'files': files}, indent=2), encoding='utf-8')
    archive = out / PACKAGE_NAME
    shutil.make_archive(str(archive.with_suffix('')), 'zip', payload)
    manifest = {'schema': 1, 'version': VERSION, 'platform': 'windows-x64',
                'package': PACKAGE_NAME, 'sha256': sha256(archive), 'size': archive.stat().st_size}
    (out / 'update-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (out / 'release-notes.md').write_text('# ' + APP_NAME + ' v' + VERSION + '\n\n' + RELEASE_NOTES +
        '\n\nDownload **Cosmere-Video-Recording-Suite-Setup.exe** and run it as your normal Windows user. '
        'No Python, administrator rights, or GitHub token needed for public updates. '
        'Alpha17 users who never connected release access need this installer once to enable token-free updates. '
        'Install ReaPlugs 2.36 x64 separately from https://www.reaper.fm/reaplugs/, then add the desired DLLs under Output → Recording → Add ReaPlugs / VST2. '
        'ReaEQ, ReaComp, ReaGate and ReaFIR voice processing are verified. MIDI routing, external sidechains, surround output and 32-bit plugins are outside this release. '
        'Licensed Cubism Core and models also remain separate. The standalone native host is GPL-3.0-or-later; its complete corresponding source is included in the source archive.\n', encoding='utf-8')
    # Installer pins the embedded payload digest at build time. No network or Python setup.
    generated = ROOT / 'build/setup_entry.py'
    generated.write_text('import sys\nfrom pathlib import Path\nfrom windows_launcher import main\n'
        'payload = Path(sys._MEIPASS) / "' + PACKAGE_NAME + '"\n'
        'sys.exit(main(["--install", str(payload), "--sha256", "' + manifest['sha256'] + '", *sys.argv[1:]]))\n', encoding='utf-8')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'Cosmere-Video-Recording-Suite-Setup', '--version-file', version_info, '--paths', APP, '--add-data', str(archive) + ';.', generated)
    # Source archive includes only tracked files; build products/user data cannot leak.
    run('git', 'archive', '--format=zip', '-o', out / ('Cosmere-Video-Recording-Suite-v' + VERSION + '-source.zip'), 'HEAD')


if __name__ == '__main__':
    build()
