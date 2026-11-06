"""Actual Windows installer/launcher/Qt/FFmpeg tests; no devices or licensed models.

The upgrade baseline is a protocol fixture, not a historical published binary (the
repository had no releases). User-data paths are seeded as an existing source install.
--published additionally downloads the real GitHub release through the shipping client.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
from update_core import (GitHub, find_update, stage_update, read_json, atomic_json,
                         sha256, deploy, version_path, PACKAGE_NAME, recover)
from version import VERSION


def run(command, env):
    subprocess.run([str(x) for x in command], check=True, env=env, timeout=180)


def smoke(root, env):
    report = root / 'smoke.json'
    report.unlink(missing_ok=True)
    env = dict(env, COSMERE_SMOKE_REPORT=str(report))
    run([root / 'CosmereCamLauncher.exe', '--root', root, '--smoke-test'], env)
    data = read_json(report)
    assert data['version'] == VERSION and data['qt'] and data['ffmpeg'] and data['discard'] and data['save'], data
    assert not (root / 'transaction.json').exists()
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--published', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/windows-verification.json')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Requires real Windows')
    results = {}
    with tempfile.TemporaryDirectory(prefix='cosmere-e2e-') as folder:
        base = Path(folder)
        root = base / 'managed'
        home = base / 'legacy-user'
        home.mkdir()
        environment = dict(os.environ, USERPROFILE=str(home), HOME=str(home),
            LOCALAPPDATA=str(base / 'local'), APPDATA=str(base / 'roaming'), COSMERE_TEST_MODE='1')
        # No hardware. Existing settings/model paths and licensed runtime remain byte-identical.
        user_files = [home / '.dynamic_teleprompter_v01.json',
                      home / '.dynamic_teleprompter_models/library.txt',
                      base / 'local/DynamicTeleprompter/Runtime/Live2D/runtime-marker.txt',
                      home / 'private.teleprompter.json']
        for path in user_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if path.suffix == '.json' else 'user owned data', encoding='utf-8')
        before = {str(path): sha256(path) for path in user_files}
        if args.published:
            client = GitHub()  # Published updates must work without credentials.
            import time
            deadline = time.monotonic() + 90
            update = find_update(client, '0.0.0')
            while (not update or update['version'] != VERSION) and time.monotonic() < deadline:
                time.sleep(5)
                update = find_update(client, '0.0.0')
            assert update and update['version'] == VERSION, update
            stage_update(client, update, root)
            # Real published helper, not the checkout build, installs the real downloaded payload.
            helper = Path(read_json(root / 'pending.json')['package']).parent / 'CosmereCamLauncher.exe'
            run([helper, '--root', root, '--apply-only'], environment)
            results['published_detection_and_download'] = VERSION
            results['anonymous_public_updates'] = True
        else:
            run([ROOT / 'dist/Cosmere-Video-Recording-Suite-Setup.exe', '--root', root, '--no-launch'], environment)
            results['fresh_installer'] = True
        results['first_launch'] = smoke(root, environment)
        if not args.published:
            # Actual shipping alpha17 launcher must keep protocol-v1 compatibility.
            from update_core import API
            client = GitHub()
            previous_release = client.json(API + '/releases/tags/v0.5.0-alpha17')
            previous_client = GitHub()
            previous_client.json = lambda url: [previous_release]
            previous_update = find_update(previous_client, '0.0.0')
            older_root = base / 'real-alpha17-upgrade'
            stage_update(previous_client, previous_update, older_root)
            previous_pending = read_json(older_root / 'pending.json')
            previous_active = deploy(older_root, previous_pending)
            shutil.copyfile(version_path(older_root, previous_active) / 'CosmereCamLauncher.exe',
                            older_root / 'CosmereCamLauncher.exe')
            (older_root / 'transaction.json').unlink()
            current_package = ROOT / 'dist' / PACKAGE_NAME
            atomic_json(older_root / 'pending.json', {'package': str(current_package),
                'version': VERSION, 'sha256': sha256(current_package), 'notes': 'Real alpha17 upgrade'})
            results['real_alpha17_launcher_upgrade'] = smoke(older_root, environment)
        original = read_json(root / 'active.json')
        # A compatible previous-version fixture points at the genuine running bundle.
        baseline = dict(original, version='0.5.0-alpha16.2', notes='Previous fixture')
        atomic_json(root / 'active.json', baseline)
        package_path = (Path(read_json(root / 'pending.json')['package']) if (root / 'pending.json').exists()
                        else ROOT / 'dist' / PACKAGE_NAME)
        if args.published:
            package_path = next((root / 'downloads').glob('*/' + PACKAGE_NAME))
        pending = {'package': str(package_path), 'version': VERSION,
                   'sha256': sha256(package_path), 'notes': 'Verified release notes'}
        atomic_json(root / 'pending.json', pending)
        results['upgrade'] = smoke(root, environment)
        assert read_json(root / 'active.json')['notes'] == 'Verified release notes'
        results['version_and_notes'] = True
        # Simulate power loss during the first startup of a candidate.
        atomic_json(root / 'transaction.json', {'previous': baseline, 'candidate': original, 'booting': True})
        results['restart_recovery'] = smoke(root, environment)
        assert read_json(root / 'active.json') == baseline
        # Authenticated but broken executable fixture: launcher must immediately roll back.
        broken = base / 'broken.zip'
        source = version_path(root, original)
        meta = read_json(source / 'package.json')
        meta['version'] = '0.5.0-alpha17.1'
        broken_bytes = b'MZ broken executable fixture'
        import hashlib
        meta['files']['CosmereCam.exe'] = hashlib.sha256(broken_bytes).hexdigest()
        with zipfile.ZipFile(broken, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in source.rglob('*'):
                if path.is_file() and path != source / 'package.json':
                    name = path.relative_to(source).as_posix()
                    archive.writestr(name, broken_bytes if name == 'CosmereCam.exe' else path.read_bytes())
            archive.writestr('package.json', json.dumps(meta))
        atomic_json(root / 'pending.json', {'package': str(broken), 'version': meta['version'],
                                          'sha256': sha256(broken), 'notes': 'broken fixture'})
        results['failed_boot_rollback'] = smoke(root, environment)
        assert read_json(root / 'active.json') == baseline
        corrupted = base / 'corrupt.zip'
        corrupted.write_bytes(b'incomplete download')
        atomic_json(root / 'pending.json', dict(pending, package=str(corrupted)))
        run([root / 'CosmereCamLauncher.exe', '--root', root, '--apply-only'], environment)
        assert read_json(root / 'active.json') == baseline
        results['corruption_rejected'] = True
        assert before == {str(path): sha256(path) for path in user_files}
        results['legacy_user_data_preserved'] = True
        # Frozen app starts with all internet access unavailable; test mode suppresses
        # checker and source unit tests exercise network errors explicitly.
        results['offline_frozen_start'] = smoke(root, dict(environment, HTTPS_PROXY='http://127.0.0.1:9'))
        if args.published:
            # Run the real Qt controller's automatic startup timer, background
            # download and critical-operation gate against the published release.
            controller_root = base / 'controller-install'
            report = controller_root / 'restarted-app.json'
            probe_env = dict(environment, COSMERE_SMOKE_REPORT=str(report))
            run([sys.executable, ROOT / 'tools/controller_probe.py', controller_root], probe_env)
            import time
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not report.exists():
                time.sleep(.2)
            assert read_json(report, {}).get('version') == VERSION, read_json(report)
            # Ensure the relaunched process and launcher have exited before Temp cleanup.
            while (controller_root / 'transaction.json').exists() and time.monotonic() < deadline:
                time.sleep(.2)
            time.sleep(2)
            results['automatic_controller'] = read_json(controller_root / 'controller-verification.json')
            assert results['automatic_controller']['download_progress']
            # Exercise Windows Credential Manager without using/persisting a real token.
            from update_ui import credential_store
            vault = credential_store()
            vault.set_password('Cosmere-Cam CI fixture', 'test', 'non-secret-fixture')
            try:
                assert vault.get_password('Cosmere-Cam CI fixture', 'test') == 'non-secret-fixture'
                results['windows_credential_store'] = True
            finally:
                vault.delete_password('Cosmere-Cam CI fixture', 'test')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
