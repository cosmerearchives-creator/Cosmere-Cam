"""Small stable per-user launcher/installer. Packaged separately from Qt application."""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from update_core import (install_root, install_lock, read_json, atomic_json,
                         unpack_package, deploy, recover, version_path, sha256)


def wait_for_pid(pid):
    if not pid:
        return
    if os.name != 'nt':
        raise RuntimeError('Process wait requires Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(handle)


def shortcut(root):
    """Use a temporary script, never interpolate a user path into shell code."""
    if os.name != 'nt':
        return
    script = root / 'create-shortcut.vbs'
    script.write_text('Set sh = CreateObject("WScript.Shell")\n'
                      'Set link = sh.CreateShortcut(WScript.Arguments(0))\n'
                      'link.TargetPath = WScript.Arguments(1)\nlink.Save\n', encoding='utf-8')
    target = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Cosmere-Cam.lnk'
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cscript.exe', '//nologo', str(script), str(target),
                    str(root / 'CosmereCamLauncher.exe')], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    script.unlink(missing_ok=True)


def launch(root, smoke=False, timeout=60):
    """A new version must acknowledge a constructed Qt window before commit."""
    root = Path(root)
    active = read_json(root / 'active.json')
    if not active:
        raise RuntimeError('No working installation. Run the Cosmere-Cam installer again.')
    transaction = read_json(root / 'transaction.json')
    if transaction:
        transaction['booting'] = True
        atomic_json(root / 'transaction.json', transaction)
    token = uuid.uuid4().hex
    ready = root / ('boot-' + token + '.json')
    environment = os.environ.copy()
    environment['COSMERE_INSTALL_ROOT'] = str(root.resolve())
    environment['COSMERE_BOOT_TOKEN'] = token
    command = [str(version_path(root, active) / 'CosmereCam.exe')]
    if smoke:
        command.append('--smoke-test')
    try:
        process = subprocess.Popen(command, cwd=version_path(root, active), env=environment)
    except OSError:
        if transaction:
            recover(root)
            if read_json(root / 'active.json'):
                return launch(root, smoke, timeout)
        raise
    deadline = time.monotonic() + timeout
    acknowledged = False
    while time.monotonic() < deadline:
        if read_json(ready, {}).get('token') == token:
            acknowledged = True
            break
        if process.poll() is not None:
            break
        time.sleep(.1)
    ready.unlink(missing_ok=True)
    if acknowledged:
        (root / 'transaction.json').unlink(missing_ok=True)
        return process.wait()
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    if transaction:
        recover(root)
        previous = read_json(root / 'active.json')
        if previous:
            return launch(root, smoke, timeout)
    raise RuntimeError('Application did not complete startup; see recovery.json or rerun installer.')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=install_root())
    parser.add_argument('--wait-pid', type=int)
    parser.add_argument('--apply-only', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--install', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--no-launch', action='store_true')
    args = parser.parse_args(argv)
    wait_for_pid(args.wait_pid)
    # On normal close the parent launcher may need a moment to release its lock.
    with install_lock(args.root, wait=bool(args.wait_pid)):
        recover(args.root)
        if args.install:
            if not args.sha256:
                raise RuntimeError('Installer package digest required')
            check = args.root / ('bootstrap-' + uuid.uuid4().hex)
            meta = unpack_package(args.install, check, args.sha256)
            try:
                stable = args.root / 'CosmereCamLauncher.exe'
                if not stable.exists():
                    shutil.copyfile(check / stable.name, stable)
                pending = {'version': meta['version'], 'sha256': args.sha256,
                           'package': str(args.install.resolve()), 'notes': meta.get('notes', '')}
            finally:
                shutil.rmtree(check)
            deploy(args.root, pending)
            shortcut(args.root)
        else:
            pending = read_json(args.root / 'pending.json')
            if pending:
                try:
                    deploy(args.root, pending)
                    stable = args.root / 'CosmereCamLauncher.exe'
                    if not stable.exists():
                        active = read_json(args.root / 'active.json')
                        shutil.copyfile(version_path(args.root, active) / stable.name, stable)
                        shortcut(args.root)
                except Exception as exc:
                    # Retain current version and quarantine bad pending metadata.
                    (args.root / 'pending.json').unlink(missing_ok=True)
                    atomic_json(args.root / 'recovery.json', {'message': 'Update was not installed: ' + str(exc)})
                    if not read_json(args.root / 'active.json'):
                        raise
        if args.apply_only or args.no_launch:
            return 0
        return launch(args.root, args.smoke_test)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        if os.name == 'nt' and not '--smoke-test' in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Cosmere-Cam startup', 0x10)
        sys.exit(1)

