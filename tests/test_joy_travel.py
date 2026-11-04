"""JOY游记解析契约的离线验证：正反例合成页面 + 注册与配置校验。"""
import tests.support  # 隔离本机下载的活动包

import unittest

from backend.core.config.loader import load_config
from backend.core.config.schema import validate_config
from backend.core.models import TextNode
from backend.core.registry import Registry, default_registry
from backend.app.jd.activities.joy_travel.tasks import parse_tasks, is_task_list


def node(text, box):
    return TextNode(text, tuple(box))


# 合成任务面板：身份标题 + 两行浏览任务 + 一行商业任务（必须被排除）
# 几何按常见 1344x3048 布局：标题占行内左侧大半，按钮贴右缘。
PANEL = [
    node('JOY游记', (900, 120, 1140, 190)),
    node('做任务赚晶石', (60, 220, 400, 280)),
    node('浏览商品15秒', (60, 400, 820, 460)),
    node('去完成', (1000, 400, 1200, 460)),
    node('逛会场领晶石', (60, 520, 820, 580)),
    node('去浏览', (1000, 520, 1200, 580)),
    node('开通PLUS会员领礼包', (60, 640, 860, 700)),
    node('去完成', (1000, 640, 1200, 700)),
]

WIDTH, HEIGHT = 1344, 3048


class ParseTests(unittest.TestCase):
    def test_browse_rows_become_tasks_and_commercial_rows_are_excluded(self):
        tasks = parse_tasks([n for n in PANEL], WIDTH, HEIGHT)
        titles = [t.title for t in tasks]
        self.assertEqual(titles, ['浏览商品15秒', '逛会场领晶石'])
        self.assertTrue(all(t.kind == 'browse' for t in tasks))
        self.assertTrue(all(t.action == 'browse' for t in tasks))

    def test_panel_without_identity_title_is_not_a_task_list(self):
        # 东东农场等京东其他互动游戏的任务面板同样满是「去完成」，
        # 没有身份标题时 is_task_list 必须拒绝（框架只在 is_task_list
        # 通过后才会调度 parse_tasks 的结果执行）。
        strangers = [n for n in PANEL if n.text != 'JOY游记']
        self.assertFalse(is_task_list(strangers))
        self.assertTrue(is_task_list(PANEL))

    def test_search_result_page_is_not_a_task_list(self):
        # 搜索结果页只有一个入口按钮，不构成任务面板。
        page = [node('搜索', (60, 60, 1600, 130)),
                node('JOY游记', (60, 300, 500, 360)),
                node('去看看', (1500, 300, 1700, 360))]
        self.assertFalse(is_task_list(page))

    def test_task_list_requires_identity_and_rows(self):
        self.assertTrue(is_task_list(PANEL))
        self.assertFalse(is_task_list([n for n in PANEL if '去' not in n.text]))


class RegistrationTests(unittest.TestCase):
    def test_jd_app_and_activity_are_discovered(self):
        registry = default_registry()
        registration = registry.get('jd', 'joy-travel')
        self.assertEqual(registration.app.id, 'jd')
        self.assertEqual(registration.activity.id, 'joy-travel')

    def test_default_config_passes_schema(self):
        config = validate_config(load_config(app='jd', activity='joy-travel'))
        self.assertEqual(config['task_search_keyword'], 'JOY游记')
        self.assertEqual(config['enabled_task_kinds'], ['browse'])

    def test_profile_matches_jd_package(self):
        registration = Registry().discover().get('jd', 'joy-travel')
        self.assertTrue(registration.app.profile.matches('com.jingdong.app.mall'))
        self.assertFalse(registration.app.profile.matches('com.taobao.taobao'))


if __name__ == '__main__':
    unittest.main()
