"""Isolated 64-bit VST2 effects, native editors and opaque state for voice audio.

The independently executable GPL host uses the public FST ABI. This module speaks
only its bounded binary protocol; no plugin code or third-party SDK enters Python.
"""
import base64
import hashlib
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
import numpy as np

MAX_PAYLOAD = 16 * 1024 * 1024
_hosts = {}
_lock = threading.RLock()
_failed = {}
_loading = {}
_retired = set()


def host_executable():
    here = Path(__file__).resolve().parent
    candidates = [here / 'native/cosmere_vst2_host.exe',
                  Path(sys.executable).parent / 'native/cosmere_vst2_host.exe',
                  here.parent / 'build/native/Release/cosmere_vst2_host.exe']
    return next((str(path) for path in candidates if path.is_file()), None)


class Host:
    def __init__(self, path, saved=''):
        executable = host_executable()
        if not executable:
            raise RuntimeError('VST2 host is missing. Install the current Windows release.')
        if not Path(path).is_file() or Path(path).suffix.lower() != '.dll':
            raise ValueError('Choose an installed 64-bit VST2 plugin DLL')
        self.lock = threading.RLock()
        self.replies = queue.Queue(maxsize=4)
        self.process = subprocess.Popen([executable, str(Path(path).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        import windows_jobs
        try:
            self.job = windows_jobs.contain(self.process)
        except Exception:
            self.process.kill()
            self.process.wait(timeout=2)
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.info = json.loads(self.request(0, timeout=10))
            if saved:
                self.restore(saved)
        except BaseException:
            self.close()
            raise

    def _exact(self, count):
        data = bytearray()
        while len(data) < count:
            chunk = self.process.stdout.read(count-len(data))
            if not chunk:
                raise RuntimeError('Plugin host exited unexpectedly')
            data.extend(chunk)
        return bytes(data)

    def _read(self):
        try:
            while True:
                status, size = struct.unpack('<II', self._exact(8))
                if size > MAX_PAYLOAD:
                    raise RuntimeError('Plugin response exceeded the safety limit')
                self.replies.put((status, self._exact(size)))
        except Exception as error:
            try:
                self.replies.put_nowait(error)
            except queue.Full:
                pass

    def request(self, operation, data=b'', timeout=2):
        if len(data) > MAX_PAYLOAD:
            raise ValueError('Plugin request is too large')
        with self.lock:
            if self.process.poll() is not None:
                raise RuntimeError('Plugin host is no longer running')
            try:
                self.process.stdin.write(struct.pack('<II', operation, len(data)) + data)
                self.process.stdin.flush()
                result = self.replies.get(timeout=timeout)
                if isinstance(result, Exception):
                    raise result
                status, payload = result
                if status:
                    raise RuntimeError(payload.decode('utf-8', 'replace'))
                return payload
            except (queue.Empty, BrokenPipeError, OSError) as error:
                # A stale reply cannot be mistaken for the next request.
                self.process.kill()
                raise RuntimeError('Plugin timed out or stopped responding; effect bypassed') from error

    def audio(self, data):
        samples = np.frombuffer(data, dtype='<i2').astype('<f4') / 32768.0
        chunks = []
        for start in range(0, len(samples), 2048):
            block = samples[start:start+2048].astype('<f4').tobytes()
            output = self.request(1, block, timeout=.35)
            if len(output) != len(block):
                raise RuntimeError('Plugin changed the audio block size')
            chunks.append(np.frombuffer(output, dtype='<f4').copy())
        result = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
        return np.clip(np.rint(np.nan_to_num(result) * 32768), -32768, 32767).astype('<i2').tobytes()

    def capture(self):
        return base64.b64encode(self.request(5)).decode('ascii')

    def restore(self, saved):
        data = base64.b64decode(saved, validate=True)
        self.request(6, data)
        self.info = json.loads(self.request(0))

    def editor(self):
        self.request(3)

    def parameters(self):
        return json.loads(self.request(7))

    def set_parameter(self, index, value):
        self.request(4, struct.pack('<If', index, value))

    def close(self):
        with self.lock:
            try:
                if self.process.poll() is None:
                    self.request(8, timeout=.5)
                    self.process.wait(timeout=1)
            except Exception:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=2)
            finally:
                for pipe in (self.process.stdin, self.process.stdout):
                    if pipe:
                        try:
                            pipe.close()
                        except OSError:
                            pass
                import windows_jobs
                windows_jobs.release(self.job)
                self.job = None


def instance_key(entry):
    return entry['instance_id']


def prepare(entry):
    key = instance_key(entry)
    with _lock:
        instance_lock = _loading.setdefault(key, threading.RLock())
    # Loading another DLL must not block a running effect's audio requests.
    with instance_lock:
        with _lock:
            if key in _retired:
                raise RuntimeError('Effect was removed from the active chain')
            host = _hosts.get(key)
            if host and host.process.poll() is None:
                return host
            if host:
                _hosts.pop(key, None)
            if time.monotonic() < _failed.get(key, 0):
                raise RuntimeError('Plugin temporarily bypassed after failure')
        if host:
            host.close()
        try:
            host = Host(entry['path'], entry.get('state', ''))
            with _lock:
                _hosts[key] = host
            return host
        except Exception:
            with _lock:
                _failed[key] = time.monotonic() + 5
            raise


def process(data, entry):
    try:
        return prepare(entry).audio(data)
    except Exception:
        with _lock:
            host = _hosts.pop(instance_key(entry), None)
            _failed[instance_key(entry)] = time.monotonic() + 5
        if host:
            host.close()
        raise


def capture_active(entries):
    snapshots = {}
    with _lock:
        active = [(entry, _hosts.get(instance_key(entry))) for entry in entries if entry.get('type') == 'vst2']
    for entry, host in active:
        if host and host.process.poll() is None:
            snapshots[instance_key(entry)] = host.capture()
    return snapshots


def latency(entries, refresh=False):
    samples = 0
    for entry in entries:
        if entry.get('type') == 'vst2' and entry.get('enabled', True):
            host = prepare(entry)
            if refresh:
                host.info = json.loads(host.request(0))
            samples += max(0, int(host.info.get('latency', 0)))
    if samples > 480000:
        raise RuntimeError('Plugin lookahead exceeds ten seconds; reduce it before recording')
    return samples


def hide_editors():
    with _lock:
        hosts = list(_hosts.values())
    for host in hosts:
        if host.process.poll() is None:
            host.request(9)


def close_all():
    with _lock:
        hosts = list(_hosts.values())
        _hosts.clear()
        _failed.clear()
        _retired.clear()
    for host in hosts:
        host.close()


def has_hosts():
    with _lock:
        return bool(_hosts)


def reconcile(entries):
    keys = {instance_key(entry) for entry in entries}
    with _lock:
        removed = set(_hosts) - keys
        _retired.update(removed)
        _retired.difference_update(keys)
        hosts = [_hosts.pop(key) for key in removed]
    for host in hosts:
        host.close()
