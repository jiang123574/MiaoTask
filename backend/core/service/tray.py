"""Small native Windows tray; no extra GUI runtime or polling loop.

Other systems keep the service in the terminal, so ``start()`` reports False
and the panel opens the browser instead of a tray icon.
"""
import ctypes
from ctypes import wintypes as w
import os
import threading
import uuid
from backend.core.service.branding import windows_icon_path


class WindowsTray:
    def __init__(self, open_console, exit_service):
        self.open_console = open_console
        self.exit_service = exit_service
        self.ready = threading.Event()
        self.thread = None
        self.hwnd = None
        self.error = None
        self._exiting = False
        self._post_message = None
        self.icon_loaded = False

    def start(self):
        if os.name != 'nt':
            return False
        self.thread = threading.Thread(target=self._loop, name='miao-windows-tray', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Windows 托盘启动超时')
        if self.error:
            raise RuntimeError('Windows 托盘创建失败') from self.error
        return True

    def activate(self, command):
        if command == 1:
            threading.Thread(target=self.open_console, daemon=True).start()
        elif command == 2 and not self._exiting:
            self._exiting = True
            threading.Thread(target=self.exit_service, name='miao-tray-exit', daemon=True).start()

    def stop(self):
        if self.hwnd and self._post_message:
            self._post_message(self.hwnd, 0x0010, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(3)

    def _loop(self):
        user32, shell32, kernel32 = (ctypes.WinDLL(name, use_last_error=True)
                                    for name in ('user32', 'shell32', 'kernel32'))
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', w.UINT), ('proc', proc_type), ('class_extra', ctypes.c_int),
                        ('window_extra', ctypes.c_int), ('instance', w.HINSTANCE), ('icon', w.HICON),
                        ('cursor', w.HANDLE), ('background', w.HBRUSH), ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

        class NotifyIcon(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('window', w.HWND), ('id', w.UINT), ('flags', w.UINT),
                        ('callback', w.UINT), ('icon', w.HICON), ('tip', w.WCHAR * 128),
                        ('state', w.DWORD), ('state_mask', w.DWORD), ('info', w.WCHAR * 256),
                        ('version', w.UINT), ('title', w.WCHAR * 64), ('info_flags', w.DWORD),
                        ('guid', ctypes.c_byte * 16), ('balloon', w.HICON)]

        user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        user32.CreateWindowExW.restype = w.HWND
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        user32.LoadImageW.restype = w.HANDLE
        user32.DestroyIcon.argtypes = [w.HICON]
        user32.CreatePopupMenu.restype = w.HMENU
        user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, w.HWND, ctypes.c_void_p]
        user32.TrackPopupMenu.restype = w.UINT
        user32.DestroyMenu.argtypes = [w.HMENU]
        user32.DestroyWindow.argtypes = [w.HWND]
        user32.SetForegroundWindow.argtypes = [w.HWND]
        user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self._post_message = user32.PostMessageW
        user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.restype = w.HMODULE
        shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NotifyIcon)]
        shell32.Shell_NotifyIconW.restype = w.BOOL
        name = 'MiaoTaskTray-' + uuid.uuid4().hex
        instance = kernel32.GetModuleHandleW(None)
        taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
        icon = None
        owned_icon = None
        registered = False

        @proc_type
        def window_proc(hwnd, message, wp, lp):
            try:
                if message == taskbar_created and icon is not None:
                    shell32.Shell_NotifyIconW(0, ctypes.byref(icon))
                    return 0
                if message == 0x8001:
                    event = lp & 0xffff
                    if event == 0x0203:  # left double click
                        self.activate(1)
                    elif event in (0x0205, 0x007b):  # right up / context menu
                        menu = user32.CreatePopupMenu()
                        try:
                            user32.AppendMenuW(menu, 0, 1, '打开控制台')
                            user32.AppendMenuW(menu, 0x0800, 0, None)
                            user32.AppendMenuW(menu, 0, 2, '停止任务并退出助手')
                            point = w.POINT()
                            user32.GetCursorPos(ctypes.byref(point))
                            user32.SetForegroundWindow(hwnd)
                            command = user32.TrackPopupMenu(menu, 0x0100 | 0x0002,
                                                            point.x, point.y, 0, hwnd, None)
                            user32.PostMessageW(hwnd, 0, 0, 0)
                            self.activate(command)
                        finally:
                            user32.DestroyMenu(menu)
                    return 0
                if message == 0x0010:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:
                    self.hwnd = None
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                # Python exceptions cannot propagate through a native callback.
                import logging
                logging.getLogger(__name__).exception('托盘菜单操作失败')
            return user32.DefWindowProcW(hwnd, message, wp, lp)

        try:
            size = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON, respect the current DPI
            owned_icon = user32.LoadImageW(None, str(windows_icon_path()), 1, size, size, 0x0010)
            if not owned_icon:
                raise ctypes.WinError(ctypes.get_last_error())
            self.icon_loaded = True
            cls = WindowClass(proc=window_proc, instance=instance, icon=owned_icon, name=name)
            if not user32.RegisterClassW(ctypes.byref(cls)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self.hwnd = user32.CreateWindowExW(0, name, 'MiaoTask', 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            icon = NotifyIcon(size=ctypes.sizeof(NotifyIcon), window=self.hwnd, id=1,
                              flags=1 | 2 | 4, callback=0x8001,
                              icon=owned_icon, tip='MiaoTask · 双击打开控制台，右键退出助手')
            if not shell32.Shell_NotifyIconW(0, ctypes.byref(icon)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            message = w.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            if icon is not None:
                shell32.Shell_NotifyIconW(2, ctypes.byref(icon))
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if registered:
                user32.UnregisterClassW(name, instance)
            if owned_icon:
                user32.DestroyIcon(owned_icon)
