"""Updater protocol v1. No Qt, no executable code from a manifest, no user-data writes.

Trust root: authenticated HTTPS to the fixed GitHub repository. Both the manifest
and package must match GitHub's server-computed asset digests. Publishing access
to that repository is code-signing authority for updates; this is not Authenticode.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = 'cosmerearchives-creator/Cosmere-Cam'
API = 'https://api.github.com/repos/' + REPOSITORY
MAX_PACKAGE = 1_500_000_000
MAX_EXPANDED = 3_000_000_000
MANIFEST_NAME = 'update-manifest.json'
PACKAGE_NAME = 'Cosmere-Cam-Windows-x64.zip'


class UpdateError(ValueError):
    pass


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-alpha(\d+)(?:\.(\d+))?)?', value)
    if not match:
        raise UpdateError('Unsupported release version')
    major, minor, patch, alpha, fix = match.groups()
    return (int(major), int(minor), int(patch), int(alpha is None), int(alpha or 0), int(fix or 0))


def install_root():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'CosmereCam'


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique files prevent a second checker from clobbering a pending write.
    fd, name = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def digest(asset):
    value = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', value):
        raise UpdateError('Release asset has no trusted GitHub SHA-256 digest')
    return value[7:]


def release_url(url):
    if not isinstance(url, str) or not url.startswith(API + '/releases/assets/'):
        raise UpdateError('Unexpected release asset address')
    if not url[len(API + '/releases/assets/'):].isdigit():
        raise UpdateError('Invalid release asset ID')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in (
                'api.github.com', 'release-assets.githubusercontent.com',
                'objects.githubusercontent.com', 'github.com'):
            raise UpdateError('Refused unexpected download redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if parsed.hostname != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self, token='', opener=None):
        self.token = token
        self.opener = opener or urllib.request.build_opener(SafeRedirect())

    def open(self, url, binary=False):
        if not url.startswith(API + '/'):
            raise UpdateError('Refused untrusted repository URL')
        headers = {'User-Agent': 'Cosmere-Cam-Updater/1',
                   'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(url, headers=headers)
        try:
            return self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            if self.token and error.code in (401, 403):
                # An expired optional credential must not block public releases.
                self.token = ''
                request.remove_header('Authorization')
                return self.opener.open(request, timeout=30)
            raise

    def json(self, url):
        with self.open(url) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise UpdateError('Oversized release metadata')
        return json.loads(data)

    def download(self, asset, target, progress=lambda done, total: None):
        expected = digest(asset)
        size = asset.get('size')
        if not isinstance(size, int) or not 0 < size <= MAX_PACKAGE:
            raise UpdateError('Invalid package size')
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix('.part')
        try:
            count = 0
            with self.open(release_url(asset['url']), binary=True) as source, partial.open('wb') as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size:
                        raise UpdateError('Download exceeded published size')
                    output.write(chunk)
                    progress(count, size)
                output.flush()
                os.fsync(output.fileno())
            if count != size or sha256(partial) != expected:
                raise UpdateError('Incomplete or corrupted update; current installation unchanged')
            os.replace(partial, target)
            return target
        finally:
            partial.unlink(missing_ok=True)


def find_update(client, current):
    """Include alpha prereleases; ignore old source-only releases and drafts."""
    releases = client.json(API + '/releases?per_page=30')
    candidates = []
    for release in releases:
        if release.get('draft'):
            continue
        try:
            key = version_key(release['tag_name'])
        except (KeyError, UpdateError):
            continue
        assets = {asset['name']: asset for asset in release.get('assets', [])}
        if key > version_key(current) and MANIFEST_NAME in assets and PACKAGE_NAME in assets:
            candidates.append((key, release, assets))
    if not candidates:
        return None
    _, release, assets = max(candidates, key=lambda item: item[0])
    # Manifest is independently authenticated by GitHub's asset digest.
    with tempfile.TemporaryDirectory() as folder:
        path = client.download(assets[MANIFEST_NAME], Path(folder) / MANIFEST_NAME)
        manifest = read_json(path)
    version = release['tag_name'].removeprefix('v')
    package = assets[PACKAGE_NAME]
    if (manifest.get('schema') != 1 or manifest.get('version') != version or
            manifest.get('package') != PACKAGE_NAME or manifest.get('sha256') != digest(package) or
            manifest.get('size') != package['size'] or manifest.get('platform') != 'windows-x64'):
        raise UpdateError('Release manifest does not match its GitHub assets')
    return {'version': version, 'notes': str(release.get('body') or ''),
            'asset': package, 'sha256': digest(package), 'release_id': release['id']}


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (not name or '\\' in name or ':' in name or name.startswith('/') or
            any(p in ('..', '.') or p.endswith((' ', '.')) or
                p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *('COM' + str(i) for i in range(1, 10)),
                    *('LPT' + str(i) for i in range(1, 10))} for p in parts)):
        raise UpdateError('Unsafe path in update package')
    return parts


def unpack_package(package, destination, expected):
    """Only extract authenticated content into a NEW, app-owned directory."""
    if sha256(package) != expected:
        raise UpdateError('Update package changed after download')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > 30000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise UpdateError('Oversized update archive')
            names = set()
            for item in infos:
                safe_name(item.filename)
                key = item.filename.rstrip('/').casefold()
                if key in names or stat.S_ISLNK(item.external_attr >> 16):
                    raise UpdateError('Duplicate or linked update entry')
                names.add(key)
                lower = item.filename.lower()
                if ('live2dcubismcore' in lower or lower.endswith(('.moc3', '.model3.json', '.vst3',
                        '.teleprompter.json', '.mp4', '.mkv', '.wav', '.env'))):
                    raise UpdateError('Private or licensed content in update archive')
            archive.extractall(destination)
        meta = read_json(destination / 'package.json')
        version_key(meta['version'])
        if meta.get('schema') != 1 or not (destination / 'CosmereCam.exe').is_file():
            raise UpdateError('Invalid Windows application package')
        inventory = meta.get('files')
        actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
        if not isinstance(inventory, dict) or actual != set(inventory) | {'package.json'}:
            raise UpdateError('Package inventory mismatch')
        for name, file_hash in inventory.items():
            safe_name(name)
            if sha256(destination / name) != file_hash:
                raise UpdateError('Package file integrity failed')
        return meta
    except BaseException:
        shutil.rmtree(destination)
        raise


def stage_update(client, update, root, progress=lambda done, total: None):
    root = Path(root)
    # Each download is isolated; a failed retry cannot destroy a prior ready update.
    cache = root / 'downloads' / (update['version'] + '-' + update['sha256'][:16])
    package = client.download(update['asset'], cache / PACKAGE_NAME, progress)
    # Validate before offering Restart, and validate again before deployment.
    check = cache / ('verify-' + str(time.time_ns()))
    meta = unpack_package(package, check, update['sha256'])
    try:
        if meta['version'] != update['version']:
            raise UpdateError('Wrong version inside update package')
        helper = check / 'CosmereCamLauncher.exe'
        if not helper.is_file():
            raise UpdateError('Missing bootstrap launcher')
        # Source installations can bootstrap without modifying their original files.
        helper_target = cache / 'CosmereCamLauncher.exe'
        shutil.copyfile(helper, helper_target)
    finally:
        shutil.rmtree(check)
    pending = {'schema': 1, 'version': update['version'], 'sha256': update['sha256'],
               'package': str(package.resolve()), 'notes': update['notes']}
    atomic_json(root / 'pending.json', pending)
    return pending


def version_path(root, pointer):
    name = pointer.get('directory', '')
    if not re.fullmatch(r'[0-9A-Za-z.-]+', name) or name in ('.', '..'):
        raise UpdateError('Invalid installed version pointer')
    return Path(root) / 'versions' / name


def deploy(root, pending):
    """Caller holds install_lock and no app is running. Pointer swap is the commit."""
    root = Path(root)
    version_key(pending['version'])
    current = read_json(root / 'active.json')
    if current and version_key(pending['version']) < version_key(current['version']):
        raise UpdateError('Refused to downgrade the installed application')
    if not re.fullmatch('[a-f0-9]{64}', pending['sha256']):
        raise UpdateError('Invalid package digest')
    name = pending['version'] + '-' + pending['sha256'][:16]
    versions = root / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    stage = versions / ('staging-' + str(time.time_ns()))
    meta = unpack_package(pending['package'], stage, pending['sha256'])
    try:
        if meta['version'] != pending['version']:
            raise UpdateError('Installed version mismatch')
        destination = versions / name
        # A crash after rename but before pointer commit leaves an orphan. Reuse
        # only after verifying every file against this newly verified package.
        if destination.exists():
            present = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
            expected_files = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
            if present != expected_files:
                raise UpdateError('Existing version inventory differs')
            for path in stage.rglob('*'):
                if path.is_file() and (not (destination / path.relative_to(stage)).is_file() or
                        sha256(path) != sha256(destination / path.relative_to(stage))):
                    raise UpdateError('Existing version is damaged')
        else:
            os.replace(stage, destination)
        pointer = {'directory': name, 'version': pending['version'], 'notes': pending.get('notes', '')}
        active = read_json(root / 'active.json')
        if active != pointer:
            atomic_json(root / 'transaction.json', {'previous': active, 'candidate': pointer})
            atomic_json(root / 'active.json', pointer)
        (root / 'pending.json').unlink(missing_ok=True)
        return pointer
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def recover(root):
    """An unacknowledged boot always restores the prior pointer, even after power loss."""
    root = Path(root)
    transaction = read_json(root / 'transaction.json')
    if not transaction:
        return False
    if not transaction.get('booting'):
        return False
    previous = transaction.get('previous')
    if previous and (version_path(root, previous) / 'CosmereCam.exe').is_file():
        atomic_json(root / 'active.json', previous)
    else:
        (root / 'active.json').unlink(missing_ok=True)
    (root / 'transaction.json').unlink(missing_ok=True)
    atomic_json(root / 'recovery.json', {'message': 'The new version did not finish startup. Previous version restored.'})
    return True


@contextlib.contextmanager
def install_lock(root, wait=False):
    """Launcher owns this OS-released lock for the entire application lifetime."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'running.lock').open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            # Nonblocking: never queue a second app to unexpectedly open later.
            deadline = time.monotonic() + (15 if wait else 0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
