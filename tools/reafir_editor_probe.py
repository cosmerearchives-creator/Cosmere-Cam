"""Interactive development probe: synthetic noise only, no audio devices.

Use the plugin's native UI to learn a profile. Commands on stdin: save, restore,
quit. State files remain in ignored build/ and are never distributed.
"""
import sys
import time
import json
import threading
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
from vst2_host import Host


def main():
    plugin = next((ROOT / 'build/reaplugs-test/plugins').rglob('reafir_standalone.dll'))
    host = Host(plugin)
    host.editor()
    lock = threading.RLock()
    stop = threading.Event()
    saved = ''
    output = ROOT / 'build/reafir-profile-verification.json'
    rng = np.random.default_rng(123)
    def audio():
        while not stop.is_set():
            data = (rng.normal(0, 1800, 512)).clip(-20000, 20000).astype('<i2').tobytes()
            with lock:
                host.audio(data)
            time.sleep(512/48000)
    worker = threading.Thread(target=audio, daemon=True)
    worker.start()
    print('Synthetic-noise ReaFIR editor ready. Commands: save, restore, quit', flush=True)
    try:
        for command in sys.stdin:
            command = command.strip()
            with lock:
                if command == 'save':
                    saved = host.capture()
                    (ROOT / 'build/reafir-learned-state.txt').write_text(saved)
                    print('Saved full state:', len(saved), 'base64 characters', flush=True)
                elif command == 'restore' and saved:
                    host.close()
                    host = Host(plugin, saved)
                    actual = host.capture()
                    host.editor()
                    result = {'state_roundtrip': saved == actual, 'state_characters': len(saved),
                              'plugin': host.info['name'], 'synthetic_noise_only': True}
                    output.write_text(json.dumps(result, indent=2))
                    print(json.dumps(result), flush=True)
                elif command == 'quit':
                    break
    finally:
        stop.set()
        worker.join(timeout=2)
        host.close()


if __name__ == '__main__':
    main()
