#!/bin/bash
# MiaoTask source launcher for macOS and Linux: double-click or run in a terminal.
# Windows uses start.bat. Close this terminal window to stop the service.
set -e
cd "$(dirname "$0")" || exit 1
python=''
if [ -x ".venv/bin/python" ]; then
    python=".venv/bin/python"
elif command -v python3.13 >/dev/null 2>&1; then
    python="python3.13"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
    python="python3"
fi
if [ -z "$python" ]; then
    echo "MiaoTask 需要 Python 3.13。macOS 可先执行：brew install python@3.13" >&2
    echo "然后在仓库目录创建 .venv 并安装 requirements.txt，详见 docs/DEVELOPMENT.md。" >&2
    exit 1
fi
exec "$python" main.py "$@"
