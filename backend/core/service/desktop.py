"""Native desktop integration with one platform dispatch point.

Only already-resolved, trusted local paths and fixed messages reach these
helpers; browser-supplied input is never forwarded to a shell or dialog.
"""
import logging
import os
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger(__name__)


def _applescript_string(value):
    """Quote arbitrary text for an AppleScript literal without a shell."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def open_path(path):
    """Open a folder or file in the system file manager and return its path."""
    target = str(Path(path))
    if os.name == 'nt' and hasattr(os, 'startfile'):
        os.startfile(target)
        return target
    command = ['open', target] if sys.platform == 'darwin' else ['xdg-open', target]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法打开文件夹，请按界面显示的路径手动访问') from exc
    if result.returncode:
        raise ValueError('无法打开文件夹，请按界面显示的路径手动访问')
    return target


def show_error_dialog(title, message):
    """Best-effort startup failure dialog; logging remains the fallback."""
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 16)
        elif sys.platform == 'darwin':
            script = f'display alert {_applescript_string(title)} as critical message {_applescript_string(message)}'
            subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=30)
        else:
            logger.error('%s: %s', title, message)
    except Exception:
        logger.warning('无法显示系统对话框，请查看运行日志', exc_info=True)


def show_alert(title, message):
    """Blocking acknowledgement dialog used for manual-intervention reminders."""
    if os.name == 'nt':
        import ctypes
        import ctypes.wintypes
        dialog = ctypes.WinDLL('user32', use_last_error=True).MessageBoxW
        dialog.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPCWSTR,
                           ctypes.wintypes.LPCWSTR, ctypes.wintypes.UINT]
        dialog.restype = ctypes.c_int
        # OK + warning icon + foreground + topmost.
        if dialog(None, str(message), str(title), 0x30 | 0x10000 | 0x40000) == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return
    if sys.platform == 'darwin':
        script = (f'display dialog {_applescript_string(message)} with title {_applescript_string(title)} '
                  'buttons {"确定"} default button 1 with icon caution')
        subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=3600)
        return
    raise OSError('当前系统不支持原生提醒对话框')
