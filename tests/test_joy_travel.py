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

# 真机面板（2026-11-04 1440x3200 截图 OCR 实得）：标题 + 右侧按钮 + 下方
# 奖励副标题三段式；最后一行的「点击查收AI虚拟宠物」是聊天任务，必须排除。
REAL_PANEL = [
    node('JOY游记', (82, 151, 348, 209)),
    node('做任务赚晶石', (392, 656, 688, 709)),
    node('去签到', (874, 854, 988, 901)),
    node('逛AI办公提效(0/1)', (187, 1079, 564, 1131)),
    node('逛一逛', (873, 1109, 990, 1156)),
    node('浏览10s+15', (186, 1143, 421, 1188)),
    node('逛一逛精选活动(0/8)', (187, 1303, 623, 1355)),
    node('去完成', (873, 1332, 991, 1383)),
    node('浏览10s可得+25', (186, 1369, 495, 1413)),
    node('组队天天赢黄金(0/1)', (186, 1526, 614, 1582)),
    node('逛一逛', (871, 1557, 990, 1608)),
    node('浏览10s+15', (182, 1589, 423, 1640)),
    node('领最高80元微信现金(0/1)', (188, 1753, 718, 1805)),
    node('逛一逛', (870, 1779, 991, 1832)),
    node('完成访问+15', (186, 1817, 438, 1862)),
    node('每日领京豆/现金(0/1)', (187, 1975, 632, 2031)),
    node('逛一逛', (871, 2005, 991, 2056)),
    node('浏览10s+15', (184, 2039, 423, 2088)),
    node('点击查收AI虚拟宠物(0/1)', (187, 2200, 703, 2256)),
    node('去完成', (873, 2230, 991, 2281)),
    node('与DD聊一次天+50', (184, 2264, 529, 2311)),
]

REAL_W, REAL_H = 1080, 2400

WIDTH, HEIGHT = 1344, 3048


class ParseTests(unittest.TestCase):
    def test_browse_rows_become_tasks_and_commercial_rows_are_excluded(self):
        tasks = parse_tasks([n for n in PANEL], WIDTH, HEIGHT)
        titles = [t.title for t in tasks]
        self.assertEqual(titles, ['浏览商品15秒', '逛会场领晶石'])
        self.assertTrue(all(t.kind == 'browse' for t in tasks))
        self.assertTrue(all(t.action == 'browse' for t in tasks))

    def test_real_panel_parses_five_browse_rows_and_drops_chat_task(self):
        tasks = parse_tasks([n for n in REAL_PANEL], REAL_W, REAL_H)
        self.assertEqual([t.title for t in tasks],
                         ['逛AI办公提效', '逛一逛精选活动', '组队天天赢黄金',
                          '领最高80元微信现金', '每日领京豆/现金'])
        progress = {t.title: (t.current, t.total) for t in tasks}
        self.assertEqual(progress['逛一逛精选活动'], (0, 8))
        self.assertNotIn('点击查收AI虚拟宠物', progress)

    def test_real_panel_is_recognized_by_ocr_header_with_leading_back_arrow(self):
        # OCR 常把返回箭头粘在标题前：「<JOY游记」仍须通过身份锁。
        panel = [node('<JOY游记', (59, 150, 350, 210))] + \
                [n for n in REAL_PANEL if n.text != 'JOY游记']
        self.assertTrue(is_task_list(panel))

    def test_panel_without_identity_title_is_not_a_task_list(self):
        # 东东农场等京东其他互动游戏的任务面板同样满是「去完成」，
        # 没有 JOY游记 身份标题时 is_task_list 必须拒绝。
        strangers = [n for n in PANEL
                     if n.text not in ('JOY游记', '做任务赚晶石')]
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
