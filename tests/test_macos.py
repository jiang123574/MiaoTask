"""macOS platform adaptation contracts; no real device, tray or dialog is used."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from backend.core import runtime
from backend.core.device import adb_lifecycle
from backend.core.device.adb_lifecycle import OwnedAdb
from backend.core.service import desktop, notify
from backend.core.storage import preferences


class DataRootTests(unittest.TestCase):
    def environ_without_local(self, **updates):
        clean = {key: value for key, value in os.environ.items() if key != 'LOCALAPPDATA'}
        clean.update(updates)
        return patch.dict(os.environ, clean, clear=True)

    def test_macos_uses_application_support_and_ignores_windows_localappdata(self):
        with self.environ_without_local(), patch.object(runtime.sys, 'platform', 'darwin'):
            self.assertEqual(runtime.user_data_root(),
                             Path.home() / 'Library' / 'Application Support' / 'MiaoTask')

    def test_localappdata_stays_authoritative_for_portable_windows_layouts(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {'LOCALAPPDATA': folder}), \
                patch.object(runtime.sys, 'platform', 'darwin'):
            self.assertEqual(runtime.user_data_root(), Path(folder) / 'MiaoTask')

    def test_frozen_records_and_preferences_share_the_macos_root(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {'LOCALAPPDATA': folder}), \
                patch.object(runtime.sys, 'frozen', True, create=True):
            self.assertEqual(runtime.data_dir(), Path(folder) / 'MiaoTask')
            self.assertEqual(preferences.preferences_path(),
                             Path(folder) / 'MiaoTask' / 'preferences.json')


class DesktopIntegrationTests(unittest.TestCase):
    def test_macos_folder_open_uses_open_without_a_shell(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(desktop.os, 'name', 'posix'), \
                patch.object(desktop.sys, 'platform', 'darwin'), \
                patch.object(desktop.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(desktop.open_path(folder), folder)
        run.assert_called_once_with(['open', folder], capture_output=True, text=True, timeout=15)

    def test_unsupported_folder_open_reports_a_visible_error(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(desktop.os, 'name', 'posix'), \
                patch.object(desktop.sys, 'platform', 'linux'), \
                patch.object(desktop.subprocess, 'run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(ValueError, '无法打开文件夹'):
                desktop.open_path(folder)

    def test_macos_alert_quotes_the_fixed_message_for_applescript(self):
        with patch.object(desktop.os, 'name', 'posix'), \
                patch.object(desktop.sys, 'platform', 'darwin'), \
                patch.object(desktop.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            desktop.show_alert('标题"x', '第一行\n第二行')
        command, = run.call_args.args
        self.assertEqual(command[:2], ['osascript', '-e'])
        self.assertIn('display dialog', command[2])
        self.assertIn(r'标题\"x', command[2])
        self.assertIn('buttons {"确定"}', command[2])

    def test_macos_security_reminder_uses_the_darwin_dialog_once(self):
        entered, release, lock = threading.Event(), threading.Event(), threading.Lock()

        def dialog():
            entered.set()
            release.wait(2)

        with patch.object(notify.sys, 'platform', 'darwin'), \
                patch.object(notify, '_popup_lock', lock), \
                patch.object(notify, '_show_macos_dialog', side_effect=dialog):
            self.assertTrue(notify.notify_security_attention())
            self.assertTrue(entered.wait(1))
            self.assertFalse(notify.notify_security_attention())
            release.set()
            self.assertTrue(lock.acquire(timeout=2))
            lock.release()
            self.assertIs(notify._platform_dialog(), notify._show_macos_dialog)

    def test_unsupported_platform_does_not_attempt_a_dialog(self):
        with patch.object(notify.sys, 'platform', 'linux'), \
                patch.object(notify, '_show_security_dialog') as windows_dialog:
            self.assertFalse(notify.notify_security_attention())
        windows_dialog.assert_not_called()


class PosixAdbCleanupTests(unittest.TestCase):
    def test_posix_cleanup_only_terminates_new_exact_binary_processes(self):
        with patch.object(adb_lifecycle.os, 'name', 'posix'), \
                patch.object(OwnedAdb, '_processes', side_effect=[{(1, 'a')}, {(1, 'a'), (2, 'b')}]), \
                patch.object(OwnedAdb, '_terminate_posix') as terminate:
            OwnedAdb('/tmp/tools/adb').close()
        terminate.assert_called_once_with(2, 'b')

    def test_posix_process_table_ignores_other_adb_installations(self):
        with tempfile.TemporaryDirectory() as folder:
            tools = Path(folder) / 'tools'
            tools.mkdir()
            binary = tools / 'adb'
            binary.write_bytes(b'test')
            table = (f'  4242 Thu Nov  6 10:00:00 2026 {binary} -L tcp:5037 fork-server server\n'
                     '  4343 Thu Nov  6 10:00:01 2026 /usr/bin/adb devices\n'
                     '  4444 Thu Nov  6 10:00:02 2026 /bin/sh -c ps\n')
            with patch.object(adb_lifecycle.os, 'name', 'posix'), \
                    patch.object(adb_lifecycle.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], 0, table, '')):
                rows = OwnedAdb(str(binary)).before
        self.assertEqual(rows, {(4242, 'Thu Nov 6 10:00:00 2026')})

    def test_unknown_posix_ownership_never_kills(self):
        with patch.object(adb_lifecycle.os, 'name', 'posix'), \
                patch.object(OwnedAdb, '_processes', side_effect=[None, {(2, 'b')}]), \
                patch.object(OwnedAdb, '_terminate_posix') as terminate:
            OwnedAdb('/tmp/tools/adb').close()
        terminate.assert_not_called()


class ToolingTests(unittest.TestCase):
    def test_fetch_platform_tools_selects_the_darwin_archive(self):
        from scripts import fetch_platform_tools
        with patch.object(fetch_platform_tools.os, 'name', 'posix'), \
                patch.object(fetch_platform_tools.sys, 'platform', 'darwin'):
            self.assertEqual(fetch_platform_tools.default_platform(), 'darwin')
        self.assertIn('platform-tools-latest-darwin.zip',
                      fetch_platform_tools.BASE + fetch_platform_tools.ARCHIVES['darwin'][0] + '.zip')
        self.assertIn('adb', fetch_platform_tools.ARCHIVES['darwin'][1])
        self.assertNotIn('adb.exe', fetch_platform_tools.ARCHIVES['darwin'][1])
