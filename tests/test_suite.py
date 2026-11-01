import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))
from audio_effects import normalize_chain
from recorder import CanvasRecorder
from update_core import API, GitHub
from update_ui import Updates


class SuiteTests(unittest.TestCase):
    def test_duplicate_plugin_instances_keep_independent_saved_state(self):
        source = dict(type='vst2', path='voice.dll', instance_id='first', state='opaque', enabled=False)
        chain = normalize_chain([source, source])[:2]
        self.assertNotEqual(chain[0]['instance_id'], chain[1]['instance_id'])
        self.assertEqual(chain[0]['state'], 'opaque')
        self.assertFalse(chain[0]['enabled'])
        self.assertEqual(normalize_chain(chain)[:2], chain)

    def test_discard_never_drains_plugin_lookahead(self):
        recorder = CanvasRecorder('unused.mp4', 64, 64, 30, microphone=False)
        recorder.cancelled = recorder.stopping = True
        recorder.plugin_latency_samples = 4096
        with patch('recorder.process_chain') as dsp:
            recorder._process_audio()
        dsp.assert_not_called()

    def test_stop_reports_audio_drain_failure(self):
        recorder = CanvasRecorder('unused.mp4', 64, 64, 30, microphone=False)
        recorder.stopping = True
        recorder.plugin_latency_samples = 4096
        with patch('recorder.process_chain', side_effect=OSError('disk full')):
            recorder._process_audio()
        self.assertIn('disk full', recorder.error)

    def test_expired_optional_credential_retries_public(self):
        seen = []
        def opened(request, **kwargs):
            seen.append(request.get_header('Authorization'))
            if len(seen) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, 'expired', {}, None)
            return io.BytesIO(b'[]')
        client = GitHub('expired', opener=Mock(open=opened))
        self.assertEqual(client.json(API + '/releases'), [])
        self.assertEqual(seen, ['Bearer expired', None])

    def test_public_controller_checks_without_credentials(self):
        import time
        ui = Mock(busy=False, closing=False)
        with patch('update_ui.read_token', return_value=''), patch('update_ui.find_update', return_value=None) as find:
            Updates.check(ui)
            deadline = time.monotonic() + 2
            while not ui.completed.emit.called and time.monotonic() < deadline:
                time.sleep(.01)
            find.assert_called_once()
            self.assertEqual(find.call_args.args[0].token, '')
