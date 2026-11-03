"""Tests must not depend on activity packages downloaded on this machine."""
import os
from pathlib import Path
import unittest

import tests.support  # 隔离本机下载的活动包

import backend.app
from backend.core.registry import Registry, default_registry

CHECKOUT = Path(__file__).resolve().parents[1]
DOWNLOADED = CHECKOUT / 'activities'


class DownloadedActivityIsolationTests(unittest.TestCase):
    def test_activity_root_points_somewhere_else_than_the_checkout(self):
        self.assertTrue(os.environ.get('MIAO_ACTIVITIES_DIR'))
        self.assertFalse(Path(os.environ['MIAO_ACTIVITIES_DIR']).resolve().is_relative_to(DOWNLOADED))

    def test_registry_ignores_downloaded_packages(self):
        for registry in (Registry(), default_registry()):
            self.assertFalse(registry.plugin_root.resolve().is_relative_to(DOWNLOADED))
            self.assertFalse(registry.plugin_root.exists())

    def test_backend_app_paths_stay_inside_the_source_tree(self):
        for path in backend.app.__path__:
            self.assertTrue(Path(path).resolve().is_relative_to(CHECKOUT / 'backend/app'))


if __name__ == '__main__':
    unittest.main()
