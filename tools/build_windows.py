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
from version import VERSION, RELEASE_NOTES
from update_core import sha256, PACKAGE_NAME


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def build():
    if os.name != 'nt':
        raise RuntimeError('Windows builds must run on Windows')
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    # Build from enumerated sources/dependencies, never copy an existing install.
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'CosmereCamLauncher', '--paths', APP, APP / 'windows_launcher.py')
    run('cmake', '-S', APP / 'native', '-B', ROOT / 'build/native', '-A', 'x64')
    run('cmake', '--build', ROOT / 'build/native', '--config', 'Release')
    native = ROOT / 'build/native/Release/dt_engine.dll'
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', 'CosmereCam', '--paths', APP,
        '--collect-all', 'vosk', '--collect-all', 'sounddevice',
        '--collect-all', 'imageio_ffmpeg', '--collect-all', 'spellchecker',
        '--collect-all', 'pedalboard', '--collect-all', 'cv2',
        '--hidden-import', 'keyring.backends.Windows', '--hidden-import', 'pygrabber.dshow_graph',
        '--add-binary', str(native) + ';native', APP / 'app_entry.py')
    payload = out / 'CosmereCam'
    shutil.copyfile(out / 'CosmereCamLauncher.exe', payload / 'CosmereCamLauncher.exe')
    license_dir = payload / 'licenses'
    license_dir.mkdir(exist_ok=True)
    # Dependency notices/metadata accompany bundled open-source components.
    import importlib.metadata
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        if not name:
            continue
        destination = license_dir / name
        destination.mkdir(exist_ok=True)
        (destination / 'METADATA.txt').write_text(str(distribution.metadata), encoding='utf-8')
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ('license', 'copying', 'notice')):
                source = distribution.locate_file(file)
                if source.is_file() and source.stat().st_size < 2_000_000:
                    shutil.copyfile(source, destination / (hashlib.sha256(str(file).encode()).hexdigest()[:8] + '-' + source.name))
    files = {p.relative_to(payload).as_posix(): sha256(p)
             for p in payload.rglob('*') if p.is_file() and p.name != 'package.json'}
    for name in files:
        lower = name.lower()
        if any(item in lower for item in ('live2dcubismcore', 'cubismsdkfornative', '.moc3', '.model3.json', '.vst3')):
            raise RuntimeError('Proprietary asset detected in payload')
    (payload / 'package.json').write_text(json.dumps({'schema': 1, 'version': VERSION,
        'notes': RELEASE_NOTES, 'files': files}, indent=2), encoding='utf-8')
    archive = out / PACKAGE_NAME
    shutil.make_archive(str(archive.with_suffix('')), 'zip', payload)
    manifest = {'schema': 1, 'version': VERSION, 'platform': 'windows-x64',
                'package': PACKAGE_NAME, 'sha256': sha256(archive), 'size': archive.stat().st_size}
    (out / 'update-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (out / 'release-notes.md').write_text('# Cosmere-Cam v' + VERSION + '\n\n' + RELEASE_NOTES +
        '\n\nDownload **Cosmere-Cam-Setup.exe** and run it as your normal Windows user. '
        'No Python or administrator rights needed. Private auto-updates require one-time '
        'release access in the Updates tab. Licensed Cubism Core, models and plugins are installed separately.\n', encoding='utf-8')
    # Installer pins the embedded payload digest at build time. No network or Python setup.
    generated = ROOT / 'build/setup_entry.py'
    generated.write_text('import sys\nfrom pathlib import Path\nfrom windows_launcher import main\n'
        'payload = Path(sys._MEIPASS) / "' + PACKAGE_NAME + '"\n'
        'sys.exit(main(["--install", str(payload), "--sha256", "' + manifest['sha256'] + '", *sys.argv[1:]]))\n', encoding='utf-8')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'Cosmere-Cam-Setup', '--paths', APP, '--add-data', str(archive) + ';.', generated)
    # Source archive includes only tracked files; build products/user data cannot leak.
    run('git', 'archive', '--format=zip', '-o', out / ('Cosmere-Cam-v' + VERSION + '-source.zip'), 'HEAD')


if __name__ == '__main__':
    build()

