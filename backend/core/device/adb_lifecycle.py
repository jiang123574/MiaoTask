"""Clean up only newly started ADB processes belonging to this installation."""
import json
import logging
import os
import signal
import subprocess


class OwnedAdb:
    def __init__(self, binary):
        # os.path.realpath keeps this independent of pathlib's os.name-based
        # Path class selection, so POSIX lifecycle tests also run on Windows.
        self.binary = os.path.realpath(str(binary))
        self.before = self._processes()

    def _powershell(self, script):
        environment = dict(os.environ, MIAO_OWN_ADB=self.binary)
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                              env=environment, capture_output=True, text=True, timeout=8,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)

    def _posix_processes(self):
        """Return (pid, start-time) pairs whose executable is exactly this ADB."""
        try:
            result = subprocess.run(['ps', '-axo', 'pid=,lstart=,command='],
                                    capture_output=True, text=True, timeout=8, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        rows = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 7 or not fields[0].isdigit():
                continue
            pid, stamp, command = int(fields[0]), ' '.join(fields[1:6]), ' '.join(fields[6:])
            executable = command.split(' ', 1)[0]
            try:
                matches = os.path.realpath(executable) == self.binary
            except (OSError, ValueError):
                matches = False
            if matches:
                rows.add((pid, stamp))
        return rows

    def _processes(self):
        if os.name != 'nt':
            return self._posix_processes()
        try:
            result = self._powershell("@(Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Where-Object { $_.ExecutablePath -eq $env:MIAO_OWN_ADB } | ForEach-Object { @{id=$_.ProcessId;stamp=$_.CreationDate.ToUniversalTime().Ticks.ToString()} }) | ConvertTo-Json -Compress")
            rows = json.loads(result.stdout or '[]')
            if isinstance(rows, dict):
                rows = [rows]
            return {(int(row['id']), row['stamp']) for row in rows}
        except Exception:
            return None  # Unknown ownership must never lead to killing another tool.

    def _terminate_posix(self, pid, stamp):
        current = self._posix_processes()
        if current is None or (pid, stamp) not in current:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            logging.getLogger('backend').warning('无法关闭本次启动的 ADB，请检查设备调试进程')

    def close(self):
        current = self._processes()
        if self.before is None or current is None:
            return
        for pid, stamp in current - self.before:
            if os.name != 'nt':
                self._terminate_posix(pid, stamp)
                continue
            if not isinstance(stamp, str) or not stamp.isdigit():
                continue
            try:
                self._powershell(f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; if ($p -and $p.ExecutablePath -eq $env:MIAO_OWN_ADB -and $p.CreationDate.ToUniversalTime().Ticks.ToString() -eq '{stamp}') {{ Stop-Process -Id {pid} -ErrorAction Stop }}")
            except Exception:
                logging.getLogger('backend').warning('无法关闭本次启动的 ADB，请检查设备调试进程')
