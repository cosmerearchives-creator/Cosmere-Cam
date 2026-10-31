"""Compare actual uploaded bytes, manifest, and platform asset digests before publish."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
from update_core import GitHub, API, PACKAGE_NAME, MANIFEST_NAME, sha256, digest, read_json
from version import VERSION


def main():
    client = GitHub(os.environ['GH_TOKEN'])
    release = client.json(API + '/releases/tags/v' + VERSION)
    assets = {a['name']: a for a in release['assets']}
    with tempfile.TemporaryDirectory() as folder:
        for name in ('Cosmere-Cam-Setup.exe', PACKAGE_NAME, MANIFEST_NAME):
            asset = assets[name]
            target = client.download(asset, Path(folder) / name)
            assert sha256(target) == sha256(ROOT / 'dist' / name) == digest(asset), name
        manifest = read_json(Path(folder) / MANIFEST_NAME)
        assert manifest['sha256'] == digest(assets[PACKAGE_NAME])
        assert manifest['version'] == VERSION
        assert manifest['size'] == assets[PACKAGE_NAME]['size']
    print('Published assets match locally verified build and update manifest.')


if __name__ == '__main__':
    main()

