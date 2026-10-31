import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))
import update_core as core
from update_ui import critical_operation, Updates


def package(folder, version='0.5.0-alpha17', extra=None):
    """Protocol fixture; Windows integration separately tests the actual frozen app."""
    path = Path(folder) / (version + '.zip')
    files = {'CosmereCam.exe': b'fake executable', 'CosmereCamLauncher.exe': b'fake launcher'}
    files.update(extra or {})
    meta = {'schema': 1, 'version': version, 'files': {
        name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr('package.json', json.dumps(meta))
    return {'version': version, 'sha256': core.sha256(path), 'package': str(path), 'notes': 'Test notes'}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'install'
        self.old = package(self.temp.name, '0.5.0-alpha16.2')
        self.new = package(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_install_and_atomic_upgrade_preserve_data(self):
        data = self.root / 'User/library.model3.json'
        data.parent.mkdir(parents=True)
        data.write_text('private settings and models')
        first = core.deploy(self.root, self.old)
        # Simulate successful startup acknowledgment of the baseline fixture.
        (self.root / 'transaction.json').unlink()
        second = core.deploy(self.root, self.new)
        self.assertNotEqual(first['directory'], second['directory'])
        self.assertTrue((core.version_path(self.root, first) / 'CosmereCam.exe').exists())
        self.assertEqual(data.read_text(), 'private settings and models')
        self.assertEqual(core.read_json(self.root / 'active.json')['notes'], 'Test notes')

    def test_restart_during_pointer_swap_recovers_previous_version(self):
        first = core.deploy(self.root, self.old)
        (self.root / 'transaction.json').unlink()
        core.deploy(self.root, self.new)
        tx = core.read_json(self.root / 'transaction.json')
        tx['booting'] = True
        core.atomic_json(self.root / 'transaction.json', tx)
        self.assertTrue(core.recover(self.root))
        self.assertEqual(core.read_json(self.root / 'active.json'), first)

    def test_unattempted_boot_survives_restart(self):
        core.deploy(self.root, self.new)
        self.assertFalse(core.recover(self.root))
        self.assertIsNotNone(core.read_json(self.root / 'active.json'))

    def test_failure_before_commit_leaves_active_version(self):
        first = core.deploy(self.root, self.old)
        (self.root / 'transaction.json').unlink()
        real_replace = os.replace
        def fail_pointer(src, dst):
            if Path(dst).name == 'active.json':
                raise PermissionError('simulated locked pointer')
            return real_replace(src, dst)
        with patch('update_core.os.replace', side_effect=fail_pointer):
            with self.assertRaises(PermissionError):
                core.deploy(self.root, self.new)
        self.assertEqual(core.read_json(self.root / 'active.json'), first)
        # Retry after interruption revalidates an orphaned version, then commits.
        core.deploy(self.root, self.new)
        self.assertEqual(core.read_json(self.root / 'active.json')['version'], self.new['version'])

    def test_corruption_and_licensed_assets_rejected(self):
        Path(self.new['package']).write_bytes(b'corrupted')
        with self.assertRaises(core.UpdateError):
            core.deploy(self.root, self.new)
        for name in ('../outside', 'C:/escape', '_internal/Live2DCubismCore.dll', 'model.moc3'):
            bad = package(self.temp.name, extra={name: b'private'})
            with self.assertRaises(core.UpdateError):
                core.deploy(self.root, bad)
        self.assertFalse((Path(self.temp.name) / 'outside').exists())

    def test_duplicate_and_symlink_entries_rejected(self):
        for name in ('CosmereCam.exe', 'link'):
            bad = package(self.temp.name)
            with zipfile.ZipFile(bad['package'], 'a') as archive:
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, 'elsewhere')
            bad['sha256'] = core.sha256(bad['package'])
            with self.assertRaises(core.UpdateError):
                core.deploy(self.root, bad)

    def test_os_lock_prevents_concurrent_install(self):
        with core.install_lock(self.root):
            with self.assertRaises(OSError):
                with core.install_lock(self.root):
                    self.fail('Second instance acquired lock')

    def test_stale_source_checkout_cannot_downgrade_managed_install(self):
        core.deploy(self.root, self.new)
        with self.assertRaises(core.UpdateError):
            core.deploy(self.root, self.old)

    def test_health_timeout_never_kills_a_running_capture(self):
        import windows_launcher
        previous = core.deploy(self.root, self.old)
        (self.root / 'transaction.json').unlink()
        core.deploy(self.root, self.new)
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch('windows_launcher.subprocess.Popen', return_value=process), \
                patch('windows_launcher.time.monotonic', side_effect=[0, 61]):
            windows_launcher.launch(self.root)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(core.read_json(self.root / 'active.json'), previous)


class DownloadTests(unittest.TestCase):
    def asset(self, data):
        return {'url': core.API + '/releases/assets/123', 'size': len(data),
                'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}

    def test_interrupted_corrupt_offline_and_successful_download(self):
        data = b'package bytes'
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'package.zip'
            for source in (io.BytesIO(b'partial'), io.BytesIO(b'bad' * 100)):
                client = core.GitHub(opener=Mock(open=Mock(return_value=source)))
                with self.assertRaises(core.UpdateError):
                    client.download(self.asset(data), target)
                self.assertFalse(target.exists())
                self.assertFalse(target.with_suffix('.part').exists())
            client = core.GitHub(opener=Mock(open=Mock(side_effect=OSError('offline'))))
            with self.assertRaises(OSError):
                client.download(self.asset(data), target)
            client = core.GitHub(opener=Mock(open=Mock(return_value=io.BytesIO(data))))
            progress = Mock()
            client.download(self.asset(data), target, progress)
            self.assertEqual(target.read_bytes(), data)
            progress.assert_called_with(len(data), len(data))

    def test_auth_not_forwarded_to_asset_host(self):
        req = urllib.request.Request(core.API + '/releases/assets/1', headers={'Authorization': 'Bearer secret'})
        redirected = core.SafeRedirect().redirect_request(req, None, 302, '', {},
            'https://release-assets.githubusercontent.com/path')
        self.assertFalse(redirected.has_header('Authorization'))
        with self.assertRaises(core.UpdateError):
            core.SafeRedirect().redirect_request(req, None, 302, '', {}, 'https://evil.example/path')

    def test_missing_releases_and_version_order(self):
        client = Mock()
        client.json.return_value = []
        self.assertIsNone(core.find_update(client, '0.5.0-alpha16.2'))
        self.assertGreater(core.version_key('0.5.0-alpha17'), core.version_key('0.5.0-alpha16.2'))
        self.assertGreater(core.version_key('0.5.0'), core.version_key('0.5.0-alpha99'))
        with self.assertRaises(core.UpdateError):
            core.version_key('../../evil')

    def test_manifest_bound_to_github_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            pending = package(folder)
            data = Path(pending['package']).read_bytes()
            asset = dict(self.asset(data), name=core.PACKAGE_NAME)
            manifest = {'schema': 1, 'version': pending['version'], 'platform': 'windows-x64',
                'package': core.PACKAGE_NAME, 'sha256': asset['digest'][7:], 'size': len(data)}
            raw = json.dumps(manifest).encode()
            manifest_asset = dict(self.asset(raw), name=core.MANIFEST_NAME, url=core.API + '/releases/assets/124')
            client = core.GitHub(opener=Mock(open=Mock(return_value=io.BytesIO(raw))))
            client.json = Mock(return_value=[{'id': 1, 'tag_name': 'v' + pending['version'],
                'body': 'Release notes', 'assets': [asset, manifest_asset]}])
            found = core.find_update(client, '0.5.0-alpha16.2')
            self.assertEqual(found['version'], pending['version'])
            # A modified manifest cannot pass the platform-computed digest.
            client.opener.open.return_value = io.BytesIO(raw.replace(b'alpha17', b'alpha18'))
            with self.assertRaises(core.UpdateError):
                core.find_update(client, '0.5.0-alpha16.2')


class BusyTests(unittest.TestCase):
    def window(self):
        w = Mock(recorder=None, audio_monitor=None, voice_worker=None,
                 test_voice_worker=None, downloader=None)
        return w

    def test_offered_during_recording_then_restart_after_recording(self):
        ui = Mock()
        ui.window = self.window()
        ui.window.recorder = Mock()
        self.assertTrue(critical_operation(ui.window))
        Updates.restart(ui)
        ui.window.close.assert_not_called()
        ui.window.recorder = None
        self.assertFalse(critical_operation(ui.window))
        Updates.restart(ui)
        ui.window.close.assert_called_once()

    def test_audio_and_background_tasks_block_restart(self):
        for field in ('audio_monitor', 'voice_worker', 'test_voice_worker', 'downloader'):
            window = self.window()
            setattr(window, field, Mock())
            self.assertTrue(critical_operation(window))

    def test_worker_failure_does_not_change_installation(self):
        ui = Mock(busy=False, closing=False)
        with patch('update_ui.read_token', return_value='test'), patch('update_ui.GitHub') as client:
            client.return_value.json.side_effect = OSError('no internet')
            Updates.check(ui)
            # Worker uses signals only; no process or installer should start.
            deadline = __import__('time').monotonic() + 2
            while not ui.completed.emit.called and __import__('time').monotonic() < deadline:
                __import__('time').sleep(.01)
        ui.downloaded.emit.assert_not_called()
        self.assertTrue(ui.completed.emit.called)

    def test_cancelled_project_close_does_not_schedule_restart(self):
        ui = Mock()
        ui.window = self.window()
        ui.window.close.return_value = False
        Updates.restart(ui)
        self.assertFalse(ui.restart_requested)

    def test_incomplete_pending_json_does_not_break_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pending.json'
            path.write_text('{incomplete')
            self.assertIsNone(core.read_json(path))

