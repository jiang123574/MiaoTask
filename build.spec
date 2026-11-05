# -*- mode: python ; coding: utf-8 -*-
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import os
import shutil
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from scripts.bundle_policy import application_data, bundle_policy, include_module, write_policy

# Windows ships a windowed build behind the tray. Other desktops keep the
# console so Ctrl+C can stop the service and logs stay visible.
windows = sys.platform == 'win32'
tool_names = {name: f'{name}.exe' if windows else name for name in ('adb', 'hdc')}
include_activities = os.environ.get('MIAO_INCLUDE_ACTIVITIES') == '1'
policy = bundle_policy(root, include_activities)
policy_path = write_policy(Path(workpath) / 'bundle-policy.json', root, include_activities)
datas = [(str(root / 'version.json'), '.'), (str(root / 'frontend'), 'frontend'), (str(root / 'backend/core/config/defaults.json'), 'backend/core/config')]
datas.append((str(policy_path), '.'))
datas += application_data(root, include_activities)
binaries = collect_dynamic_libs('onnxruntime')
for package in ('uiautomator2', 'hmdriver2', 'rapidocr'):
    datas += collect_data_files(package)


def metadata_name(name):
    try:
        distribution(name)
        return name
    except PackageNotFoundError:
        if name == 'onnxruntime':
            return 'onnxruntime-gpu'
        raise


for package in ('uiautomator2', 'hmdriver2', 'rapidocr', 'onnxruntime', 'numpy', 'Pillow'):
    datas += copy_metadata(metadata_name(package), recursive=True)

for tool in ('adb', 'hdc'):
    supplied = os.environ.get(f'MIAO_{tool.upper()}_DIR')
    executable = str(Path(supplied) / tool_names[tool]) if supplied else shutil.which(tool_names[tool])
    if executable and Path(executable).is_file():
        folder = Path(executable).parent
        binaries.append((executable, 'shared/tools'))
        if windows:
            binaries.extend((str(dll), 'shared/tools') for dll in folder.glob('*.dll'))
        datas.extend((str(folder / name), 'shared/tools') for name in ('NOTICE.txt', 'source.properties')
                     if (folder / name).is_file())
    elif tool == 'adb':
        raise RuntimeError('ADB is required for this release build')

a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=collect_submodules('backend', filter=lambda name: include_module(name, include_activities)) + collect_submodules('rapidocr'),
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['torch', 'tensorflow', 'paddle', 'matplotlib', 'pytest', 'IPython'] + policy['excluded_app_modules'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MiaoTask',
          icon=str(root / 'frontend/assets/miaotask.ico') if windows else None,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=not windows, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MiaoTask')
