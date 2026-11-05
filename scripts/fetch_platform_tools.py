"""Download Google's platform tools and extract only the ADB runtime/notice."""
import argparse
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import sys
from urllib.request import urlopen
import zipfile

BASE = 'https://dl.google.com/android/repository/platform-tools-latest-'
ARCHIVES = {
    'win32': ('windows', {'adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'libwinpthread-1.dll',
                          'NOTICE.txt', 'source.properties'}),
    'darwin': ('darwin', {'adb', 'NOTICE.txt', 'source.properties'}),
    'linux': ('linux', {'adb', 'NOTICE.txt', 'source.properties'}),
}
EXECUTABLES = {'windows': ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'), 'darwin': ('adb',), 'linux': ('adb',)}


def default_platform():
    key = 'win32' if os.name == 'nt' else sys.platform
    return key if key in ARCHIVES else 'linux'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='build/vendor/platform-tools')
    parser.add_argument('--platform', choices=sorted(ARCHIVES), default=default_platform(),
                        help='Target platform whose official ADB runtime is downloaded')
    args = parser.parse_args()
    archive_name, files = ARCHIVES[args.platform]
    url = BASE + archive_name + '.zip'
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response:
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in entry.filename:
                raise RuntimeError('Unsafe archive path')
            if path.parts == ('platform-tools', path.name) and path.name in files:
                target = folder / path.name
                target.write_bytes(archive.read(entry))
                if path.name in EXECUTABLES[archive_name]:
                    target.chmod(0o755)
    required = EXECUTABLES[archive_name] + ('NOTICE.txt',)
    if not all((folder / name).is_file() for name in required):
        raise RuntimeError('The ADB distribution is incomplete')
    print(f'ADB runtime ({args.platform}): {folder}; source archive SHA256: {hashlib.sha256(data).hexdigest()}')


if __name__ == '__main__':
    main()
