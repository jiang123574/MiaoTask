"""Read configured MuMu endpoints through its installed vendor CLI, no port scan."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess


def find_mumu_cli():
    explicit = os.environ.get('MIAO_MUMU_CLI')
    candidates = [explicit, shutil.which('mumu-cli.exe'), shutil.which('MuMuManager.exe')]
    if os.name == 'nt' and not any(candidates):
        # Read running executable locations, not process command lines or account data.
        result = subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter \"Name='MuMuNxMain.exe'\" | Select-Object -ExpandProperty ExecutablePath"],
            capture_output=True, encoding='utf-8', errors='replace', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        for line in result.stdout.splitlines():
            parent = Path(line.strip()).parent
            candidates.extend([parent/'mumu-cli.exe', parent/'MuMuManager.exe'])
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def discover_emulators():
    try:
        binary = find_mumu_cli()
    except (OSError,subprocess.SubprocessError):
        return dict(devices=[], errors=['无法定位 MuMu 安装目录；可手动填写其 ADB 地址，或配置 MIAO_MUMU_CLI'])
    if not binary:
        # MuMu's vendor CLI is Windows-only; other emulators already appear in
        # the normal ADB device list, so only manual addresses remain here.
        message = ('当前系统不自动发现 MuMu 实例；模拟器可先启动，再用 ADB 地址手动连接'
                   if os.name != 'nt' else '未找到运行中的 MuMu；可先启动模拟器，或手动填写其 ADB 地址')
        return dict(devices=[], errors=[message])
    try:
        result = subprocess.run([binary,'info','--vmindex','all'], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=8,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        rows = json.loads(result.stdout)
        if result.returncode or not isinstance(rows,dict):
            raise ValueError('invalid vendor response')
        devices = []
        for index, row in rows.items():
            if not isinstance(row,dict) or not row.get('is_android_started'):
                continue
            host = str(row.get('adb_host_ip',''))
            port = int(row.get('adb_port',0))
            if not ipaddress.ip_address(host).is_loopback or not 1 <= port <= 65535:
                continue
            devices.append(dict(serial=f'{host}:{port}', type='uiautomator2', status='device',
                                model=str(row.get('name',f'MuMu {index}'))[:100], transport='emulator'))
        return dict(devices=devices, errors=[])
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):
        return dict(devices=[], errors=['无法读取 MuMu 实例信息；可手动填写模拟器诊断页的 ADB 地址'])
