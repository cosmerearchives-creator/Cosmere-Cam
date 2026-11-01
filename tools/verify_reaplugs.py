"""Test official ReaPlugs without installing/bundling their DLLs or using devices."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
from update_core import sha256
import vst2_host
from audio_effects import normalize_chain, process_chain, plugin_errors
from recorder import CanvasRecorder

VENDOR_URL = 'https://www.reaper.fm/reaplugs/reaplugs236_x64-install.exe'
VENDOR_SHA256 = '1654f1f78d746fc87a6f6d078a8f928ea61c7b49997b1f2c07dfc870f8fa6c10'
NAMES = ('reaeq-standalone.dll', 'reacomp-standalone.dll', 'reagate-standalone.dll', 'reafir_standalone.dll')


def vendor_plugins():
    folder = ROOT / 'build/reaplugs-test'
    folder.mkdir(parents=True, exist_ok=True)
    installer = folder / 'reaplugs236_x64-install.exe'
    if not installer.exists():
        urllib.request.urlretrieve(VENDOR_URL, installer)
    if sha256(installer) != VENDOR_SHA256:
        raise RuntimeError('Official test installer changed; review the vendor download before updating its pinned digest')
    sevenzip = shutil.which('7z') or 'C:/Program Files/7-Zip/7z.exe'
    subprocess.run([sevenzip, 'x', str(installer), '-o' + str(folder / 'plugins'), '-y'],
                   check=True, capture_output=True)
    return {name: next((folder / 'plugins').rglob(name)) for name in NAMES}


def check(editors=False):
    plugins = vendor_plugins()
    rng = np.random.default_rng(812)
    raw = rng.normal(0, 1800, 48000).clip(-20000, 20000).astype('<i2').tobytes()
    block = raw[:1024]
    results = {}
    for name, path in plugins.items():
        host = vst2_host.Host(path)
        try:
            before = host.parameters()
            state = host.capture()
            host.set_parameter(0, .42)
            host.restore(state)
            after = host.parameters()
            assert np.allclose([p['value'] for p in before], [p['value'] for p in after], atol=1e-5), name
            if editors:
                host.editor()
                host.request(9)
            start = time.perf_counter()
            for _ in range(100):
                data = host.audio(block)
                assert len(data) == len(block)
            elapsed = time.perf_counter()-start
            assert elapsed < 100 * 512 / 48000, (name, elapsed)
            results[name] = {'name': host.info['name'], 'state_restore': True,
                'native_editor_opened': editors, 'mean_block_ms': round(elapsed*10, 3),
                'latency_samples': host.info['latency']}
        finally:
            host.close()
    # Full learned noise curve: restored instances must agree on actual audio,
    # and perform substantially differently from a default EQ-mode instance.
    saved = (ROOT / 'tests/fixtures/reafir-synthetic-noise.b64').read_text().strip()
    hosts = [vst2_host.Host(plugins[NAMES[-1]], saved), vst2_host.Host(plugins[NAMES[-1]], saved),
             vst2_host.Host(plugins[NAMES[-1]])]
    try:
        outputs = [np.frombuffer(host.audio(raw), dtype='<i2').astype(float)[8192:] for host in hosts]
        reduced = float(np.sqrt(np.mean(outputs[0] ** 2)))
        normal = float(np.sqrt(np.mean(outputs[2] ** 2)))
        assert np.array_equal(outputs[0], outputs[1])
        assert reduced < normal * .25, (reduced, normal)
        results['learned_noise_profile'] = {'restored_audio_matches': True,
            'default_rms': round(normal, 4), 'restored_rms': round(reduced, 4), 'synthetic_noise_only': True}
    finally:
        for host in hosts:
            host.close()
    # One chain instance survives monitoring/recording hand-off; saved audio is
    # compensated for ReaFIR's declared delay and retains every captured sample.
    chain = normalize_chain([{'type': 'vst2', 'path': str(plugins[NAMES[-1]]), 'enabled': True}])
    host = vst2_host.prepare(chain[0])
    latency = vst2_host.latency(chain, refresh=True)
    recorder = CanvasRecorder(ROOT / 'build/unused.mp4', 64, 64, 30, microphone=False, audio_chain=chain)
    recorder.plugin_latency_samples = recorder._latency_remaining = latency
    output_wave = ROOT / 'build/reaplugs-test/alignment.wav'
    recorder._audio_file = wave.open(str(output_wave), 'wb')
    recorder._audio_file.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
    recorder._audio_queue.put(raw)
    recorder.stopping = True
    recorder._process_audio()
    recorder._audio_file.close()
    assert not recorder.error, recorder.error
    with wave.open(str(output_wave), 'rb') as stream:
        aligned = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2')
    original = np.frombuffer(raw, dtype='<i2')
    assert len(aligned) == len(original)
    assert np.allclose(aligned, original, atol=3), (latency, int(np.max(np.abs(aligned.astype(int)-original))))
    results['recording_alignment'] = {'delay_compensated': latency, 'sample_count_preserved': True, 'aligned_audio_matches': True}
    # Native crash/exit must bypass, not stop capture or repeatedly reload.
    host.process.kill()
    host.process.wait(timeout=3)
    vst2_host._failed[chain[0]['instance_id']] = time.monotonic() + 5
    bypassed, _ = process_chain(block, chain)
    assert bypassed == block and str(plugins[NAMES[-1]]) in plugin_errors()
    results['failed_host_bypassed'] = True
    vst2_host.close_all()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--editors', action='store_true')
    args = parser.parse_args()
    result = check(args.editors)
    out = ROOT / 'dist/reaplugs-verification.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
