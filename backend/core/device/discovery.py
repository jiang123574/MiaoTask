"""Bounded Android discovery and first-use USB authorization without changing keys.

Only the child ADB process environment is changed for the Windows libusb fallback.
An existing device, even an unauthorized/offline one, always keeps its server.
"""
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


class AndroidConnectionError(ConnectionError):
    """A connection needs a cable, a phone authorization, or device selection."""


ADB_EXECUTABLE = 'adb.exe' if os.name == 'nt' else 'adb'


def resolve_adb_binary():
    """Use the same bundled/explicit executable as adbutils, then the PATH one."""
    explicit = os.environ.get('ADBUTILS_ADB_PATH')
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise AndroidConnectionError('配置的 ADB 程序不存在，请检查 ADB 路径或重新安装完整版本')
        return str(candidate.resolve())
    if getattr(sys, 'frozen', False):
        candidate = Path(sys._MEIPASS) / 'shared' / 'tools' / ADB_EXECUTABLE
        if not candidate.is_file():
            raise AndroidConnectionError('安装包缺少 ADB，请重新安装完整版本')
        return str(candidate.resolve())
    candidate = shutil.which('adb')
    if not candidate:
        raise AndroidConnectionError('未找到 ADB，请使用完整安装包或将 platform-tools 加入 PATH')
    return str(Path(candidate).resolve())


def _is_windows():
    return sys.platform.startswith('win')


def _run_adb(binary, args, env, timeout):
    try:
        return subprocess.run(
            [binary, *args], capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=max(.01, timeout), env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if _is_windows() else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise AndroidConnectionError('ADB 连接调用超时，请检查数据线和手机调试状态') from exc
    except OSError as exc:
        raise AndroidConnectionError('无法运行 ADB，请检查便携包是否完整或程序是否被占用') from exc


def _parse_devices(output):
    devices = []
    for raw in output.splitlines():
        line = raw.strip()
        if (not line or line.startswith(('List of devices attached', '*', 'adb:', 'adb server '))):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        serial, state = parts[:2]
        detail = parts[2] if len(parts) > 2 else ''
        if state == 'no' and detail.startswith('permissions'):
            state = 'no permissions'
        model = re.search(r'(?:^|\s)model:([^\s]+)', detail)
        devices.append(dict(serial=serial, state=state, model=model.group(1) if model else '',
                            description=detail))
    return devices


def list_android_devices(adb_path=None, *, timeout=8, env=None):
    """Return serial/state/model records; discovery never restarts a server."""
    binary = adb_path or resolve_adb_binary()
    child_env = dict(os.environ if env is None else env)
    result = _run_adb(binary, ['devices', '-l'], child_env, timeout)
    if result.returncode:
        raise AndroidConnectionError('ADB 未能列出设备，请检查手机连接后重试')
    return _parse_devices(result.stdout)


def android_identity(serial):
    """Read-only stable identity, including emulators without serial properties."""
    binary = resolve_adb_binary()
    for args in (['getprop', 'ro.serialno'], ['getprop', 'ro.boot.serialno'],
                 ['settings', 'get', 'secure', 'android_id']):
        result = _run_adb(binary, ['-s', serial, 'shell', *args], os.environ.copy(), 8)
        value = result.stdout.strip()
        if (result.returncode == 0 and re.fullmatch(r'[A-Za-z0-9._-]{3,128}', value)
                and value.lower() not in ('unknown','null','none')):
            return ('android-' if args[0] == 'settings' else '') + value
    raise AndroidConnectionError('无法核定设备身份，为避免重复控制同一设备，请恢复调试连接后重试')


def _network_serial(serial):
    return bool(serial and re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]|[^:\s]+):\d+', serial))


def prepare_android_connection(serial=None, on_status=None, timeout=45):
    """Return a single authorized serial, waiting for the user's phone approval.

    ``on_status(message)`` receives progress text. This function cannot approve
    the RSA prompt: it only establishes ADB handshakes while the user approves it
    on the unlocked phone. Neither ADB keys, drivers nor system settings change.
    """
    if serial is not None:
        if (not isinstance(serial, str) or not serial or len(serial) > 256
                or re.search(r'\s|[\x00-\x1f]', serial) or serial.startswith('-')):
            raise ValueError('设备序列号或无线调试地址无效')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('ADB 等待时间必须为正数')
    notify = on_status or (lambda message: None)
    binary = resolve_adb_binary()
    env = os.environ.copy()
    deadline = time.monotonic() + timeout

    def remaining():
        return max(.01, min(8, deadline - time.monotonic()))

    def run(args):
        return _run_adb(binary, args, env, remaining())

    def devices():
        return list_android_devices(binary, timeout=remaining(), env=env)

    network = _network_serial(serial)
    notify('正在确认无线设备授权；请保持手机解锁并开启无线调试' if network else
           '正在连接 ADB；首次连接请保持手机解锁，留意 USB 调试授权弹窗')
    if run(['start-server']).returncode:
        raise AndroidConnectionError('ADB 服务启动失败，请关闭其他占用 ADB 的程序后重试')
    found = devices()
    fallback_done = False
    if network and not any(d['serial'] == serial and d['state'] == 'device' for d in found) and time.monotonic() < deadline:
        notify('正在连接指定的无线调试设备')
        run(['connect', serial])
        found = devices()
    # Recheck immediately before the only explicit server restart. Another ADB
    # client/phone may have become available since the first observation.
    if not found and _is_windows() and not network and time.monotonic() < deadline:
        found = devices()
        if not found:
            notify('未检测到 USB 设备，正在尝试 Windows 兼容连接方式')
            fallback_done = True
            env['ADB_LIBUSB'] = '1'
            if run(['kill-server']).returncode or run(['start-server']).returncode:
                raise AndroidConnectionError('ADB 兼容连接启动失败，请重新连接数据线后重试')
            found = devices()
    selected = serial
    last_state = ''
    while True:
        if selected is None:
            if len(found) > 1:
                raise AndroidConnectionError('检测到多台 Android 设备，请先在控制台选择设备后连接')
            if found:
                selected = found[0]['serial']
        target = next((device for device in found if device['serial'] == selected), None)
        if target:
            state = target['state']
            if state == 'device':
                notify('设备已授权，正在建立自动化连接')
                return selected
            if state not in ('unauthorized', 'offline', 'authorizing', 'connecting'):
                raise AndroidConnectionError(f'设备当前处于 {state[:40]} 状态，请恢复正常 Android 系统后重试')
            if state != last_state:
                if state in ('unauthorized', 'authorizing'):
                    notify('无线设备尚未授权，请检查配对状态并在手机确认调试授权' if network else
                           '请解锁手机，在“允许 USB 调试”弹窗点击允许；正在等待手机授权')
                else:
                    notify('无线设备暂时离线，请检查 Wi-Fi、无线调试开关和连接端口' if network else
                           '设备暂时离线，正在等待恢复；请检查数据线并保持手机解锁')
                last_state = state
            # Each selected transport handshake lets Android display its own
            # RSA authorization dialog. Never accept it or replace ADB keys.
            run(['-s', selected, 'get-state'])
        elif found:
            raise AndroidConnectionError('指定设备未出现在 ADB 列表中，请重新选择当前连接的设备')
        elif not last_state:
            notify('等待手机连接，请开启 USB 调试并使用支持数据传输的数据线')
            last_state = 'missing'
        if time.monotonic() >= deadline:
            break
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            break
        found = devices()
    if last_state in ('unauthorized', 'authorizing'):
        if network:
            raise AndroidConnectionError('等待无线调试授权超时，请确认手机配对状态后重新连接')
        raise AndroidConnectionError('等待手机 USB 调试授权超时。请解锁手机，在系统弹窗点击允许后重新连接')
    if last_state in ('offline', 'connecting'):
        if network:
            raise AndroidConnectionError('无线设备持续离线，请核对当前连接地址并保持手机和电脑在可互访的网络')
        raise AndroidConnectionError('设备一直处于离线状态，请重新连接数据线并确认手机 USB 调试授权')
    suffix = '，也已尝试 Windows 兼容方式' if fallback_done else ''
    raise AndroidConnectionError('未检测到可用的指定设备' + suffix + '；请检查 USB 调试、数据线或无线调试地址')
