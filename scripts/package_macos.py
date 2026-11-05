"""Build a tested, self-contained macOS package using an explicit allowlist."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
if __package__:
    from .package_windows import (PORTABLE_DOCS, PROJECT_LICENSES, licenses, source_commit,
                                  worktree_snapshot)
else:
    from package_windows import (PORTABLE_DOCS, PROJECT_LICENSES, licenses, source_commit,
                                 worktree_snapshot)

LAUNCHER = '''#!/bin/bash
# Double-click launcher kept next to the bundled executable.
cd "$(dirname "$0")" || exit 1
exec ./MiaoTask "$@"
'''


def architecture():
    machine = platform.machine().lower()
    return 'arm64' if machine in ('arm64', 'aarch64') else 'x64'


def copy_documentation(destination, *, include_activities=False, preview=False):
    folder = destination / 'docs'
    folder.mkdir(parents=True, exist_ok=True)
    for name in PORTABLE_DOCS:
        shutil.copy2(ROOT / 'docs' / name, folder / name)
    for name in PROJECT_LICENSES:
        shutil.copy2(ROOT / name, destination / name)
    activity_help = ('本包包含源码提供的活动，可在网页中选择使用。\n\n' if include_activities else
                     '默认仅包含框架与运行环境。首次打开网页，在「活动中心」下载需要的应用与活动。\n\n')
    notice = ('此包为本地预览构建，未按正式发布流程审阅。\n\n' if preview else '')
    (destination / 'README.md').write_text(
        '# MiaoTask\n\n' + notice +
        '双击 `MiaoTask.command` 启动，或在该目录终端运行 `./MiaoTask`；'
        '关闭终端会同时结束后台任务，按 `Ctrl+C` 停止服务。\n\n'
        + activity_help +
        '活动源码位于本目录旁的 `activities/`，可在活动中心查看实际位置或打开文件夹；'
        '日记录与设备信息保存在 `~/Library/Application Support/MiaoTask`。\n\n'
        '首次使用需要允许终端访问本地网络，并在手机上确认 USB 调试授权。\n\n'
        '[使用说明](docs/USAGE.md) · [风险与隐私](docs/SAFETY.md) · '
        '[发布记录](docs/RELEASES.md) · [许可与声明](docs/LICENSING.md)\n',
        encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb-dir', help='Folder containing the macOS adb runtime and NOTICE.txt')
    parser.add_argument('--skip-tests', action='store_true', help='Skip unit tests already run on this commit')
    parser.add_argument('--include-activities', action='store_true',
                        help='Bundle source activities for offline compatibility checks')
    parser.add_argument('--preview', action='store_true',
                        help='Local candidate only: allow uncommitted source and mark the build as preview')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('Build this package on macOS')
    if platform.machine().lower() not in ('arm64', 'aarch64', 'x86_64'):
        raise SystemExit('Unsupported macOS architecture')
    adb_dir = Path(args.adb_dir).resolve() if args.adb_dir else ROOT / 'build/vendor/platform-tools'
    if not all((adb_dir / name).is_file() for name in ('adb', 'NOTICE.txt')):
        raise SystemExit('ADB missing: run python scripts/fetch_platform_tools.py or pass --adb-dir')
    if not args.skip_tests:
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'], cwd=ROOT, check=True)
    version = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'MiaoTask-v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise SystemExit('Invalid release version')
    revision = '' if args.preview else source_commit(allow_dirty=False)
    snapshot = worktree_snapshot(local_source=args.preview)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    dist = (ROOT / 'dist' / f'macos-{stamp}').resolve()
    work = (ROOT / 'build' / f'pyinstaller-{stamp}').resolve()
    if not dist.is_relative_to(ROOT / 'dist') or not work.is_relative_to(ROOT / 'build'):
        raise SystemExit('Build path escapes the workspace')
    env = os.environ.copy()
    env['MIAO_ADB_DIR'] = str(adb_dir)
    env['MIAO_INCLUDE_ACTIVITIES'] = '1' if args.include_activities else '0'
    # Keep PyInstaller's conversion cache inside the ignored build tree so the
    # build works in read-only home directories and never touches app support.
    env.setdefault('PYINSTALLER_CONFIG_DIR', str(ROOT / 'build' / 'pyinstaller-cache'))
    Path(env['PYINSTALLER_CONFIG_DIR']).mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(dist),
                    '--workpath', str(work), str(ROOT / 'build.spec')], env=env, cwd=ROOT, check=True)
    package = dist / 'MiaoTask'
    executable = package / 'MiaoTask'
    if not executable.is_file():
        raise SystemExit(f'PyInstaller did not produce {executable}')
    launcher = package / 'MiaoTask.command'
    launcher.write_text(LAUNCHER, encoding='utf-8')
    launcher.chmod(0o755)
    copy_documentation(package, include_activities=args.include_activities, preview=args.preview)
    licenses(package / 'licenses', adb_dir)
    from smoke_packaged import smoke
    smoke_result = smoke(executable, package / 'self-test.json')
    policy = json.loads((package / '_internal/bundle-policy.json').read_text(encoding='utf-8'))
    manifest = dict(version=version, platform=f'macos-{architecture()}', python=platform.python_version(),
                    source_commit=revision, source_repository='https://github.com/h0ll0w-AkuZr0guY/MiaoTask',
                    preview=args.preview, source_dirty=args.preview,
                    source_tree_sha256=snapshot, bundle_mode=policy['bundle_mode'],
                    framework_api=policy['framework_api'],
                    embedded_activities=policy['embedded_activity_modules'],
                    verified_activity_imports=smoke_result['actual_activity_imports'],
                    built_utc=datetime.now(timezone.utc).isoformat(),
                    packages={d.metadata['Name']: d.version for d in metadata.distributions()},
                    adb_bundled=True, offline_models=True, smoke_test_passed=True,
                    distribution='one-folder', launcher='MiaoTask.command')
    (package / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if snapshot != worktree_snapshot(local_source=args.preview):
        raise RuntimeError('Source changed during the build; rebuild the final candidate')
    suffix = ('-with-activities' if args.include_activities else '') + ('-preview' if args.preview else '')
    archive = ROOT / 'dist' / f'{version}-macos-{architecture()}{suffix}.zip'
    root = package.resolve()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(package.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            member = Path('MiaoTask') / path.relative_to(package)
            if path.is_symlink():
                # PyInstaller keeps relative dylib aliases inside _internal.
                target = os.readlink(path)
                if not path.resolve().is_relative_to(root) or Path(target).is_absolute():
                    raise RuntimeError(f'Unsafe package symlink: {path.name}')
                record = zipfile.ZipInfo(str(member))
                record.create_system = 3
                record.external_attr = (0o120777 & 0xFFFF) << 16
                bundle.writestr(record, target)
                continue
            if not path.resolve().is_relative_to(root):
                raise RuntimeError(f'Unsafe package member: {path.name}')
            bundle.write(path, member)
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps(dict(archive=str(archive), executable=str(executable),
                          bytes=archive.stat().st_size, sha256=digest), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
