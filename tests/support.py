"""Keep the test process on the bundled activities.

The console downloads reviewed activity packages into ``<root>/activities/apps``.
``Registry.discover`` prefers that root and prepends it to ``backend.app.__path__``,
so a copy left behind by an ordinary run shadows the bundled source for the rest
of the process: a stale package can hide activities (``cash-signin``, ``farm``)
or ship an older runner, and unrelated tests then fail depending on which module
happened to touch the registry first.

Importing this module points the activity root at an empty temporary directory,
unless a test already chose one through ``MIAO_ACTIVITIES_DIR``.
"""
import atexit
import os
import shutil
import tempfile


def isolate_downloaded_activities():
    """Return this process's activity root, isolated when nothing chose one."""
    explicit = os.environ.get('MIAO_ACTIVITIES_DIR')
    if explicit:
        return explicit
    root = tempfile.mkdtemp(prefix='miao-test-activities-')
    atexit.register(shutil.rmtree, root, True)
    os.environ['MIAO_ACTIVITIES_DIR'] = root
    return root


isolate_downloaded_activities()
