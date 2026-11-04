"""JOY游记入口：京东搜索直达活动页，保留任务面板分页能力。

路径（攻略核对，细节待真机核对）：
    京东APP 首页顶部搜索栏 → 输入「JOY游记」→ 点搜索结果里的活动入口
    → 活动主页（身份标题「JOY游记」）→ 下方「做任务/赚晶石」打开任务面板

京东搜索框的提示词轮换且 H5 化，因此入口识别以几何特征为主、文案为辅：
搜索栏在首页顶部一条（状态栏下方、宽度超过 0.6W）。
"""
import logging
import re

from backend.core.models import TextNode, bounds_of, normalize
from backend.core.errors import Stopped, NeedsAttention
from backend.core.device.instances import InstanceMismatch

logger = logging.getLogger('backend.execution')


def _norm(text):
    return normalize(text or '')


def _is_jd(package):
    return (package or '').lower().startswith('com.jingdong.app.mall')


class EntryPolicy:
    _norm = staticmethod(_norm)

    # ---- 页面状态识别（供 Navigator 调度） ----

    @staticmethod
    def homepage_visible(screen):
        # 京东首页底部标签：首页 / 购物车 / 消息 / 我的
        return any(_norm(n.text) == '我的' and n.center[1] > screen.height * 0.85
                   for n in screen.nodes)

    @staticmethod
    def launcher_screen(screen):
        package = (screen.package or '').lower()
        return not package.startswith('com.jingdong.app.mall') and (
            'launcher' in package or package.endswith('.home'))

    @staticmethod
    def launch_screen(screen):
        """京东启动/闪屏页：几乎没有任何正文文本。"""
        return _is_jd(screen.package) and sum(
            1 for n in screen.nodes if _norm(n.text)) < 3

    @staticmethod
    def activity_landing(screen):
        """活动主页：上半屏出现「JOY游记」标题。"""
        titles = ('joy游记', 'joy游记'.replace(' ', ''))
        for n in screen.nodes:
            text = _norm(n.text).replace(' ', '')
            if text in titles and 0 < n.bounds[3] - n.bounds[1] < screen.height * 0.15 \
                    and n.center[1] < screen.height * 0.5:
                return True
        return False

    def task_panel_visible(self, screen):
        return any('去完成' in _norm(n.text) or '领取' in _norm(n.text)
                   for n in screen.nodes)

    # ---- 入口定位 ----

    def task_panel_entry(self, screen):
        """活动主页里打开任务面板的入口（做任务/赚晶石/更多任务）。"""
        if self.is_task_list(screen.nodes):
            return None
        for label in self.config.get('task_entry_labels', ['做任务']):
            text = _norm(label)
            for n in screen.nodes:
                if _norm(n.text) == text and 0 < n.bounds[2] - n.bounds[0] < screen.width * 0.6:
                    return n
        return None

    def activity_entry(self, screen, homepage=False):
        """搜索结果页里的 JOY游记 入口卡片；首页没有固定宫格入口。"""
        return self._search_result(screen)

    # ---- 搜索直达：首页搜索栏 → 输入 → 点结果卡片 ----

    @staticmethod
    def _search_entry_node(screen):
        """首页顶部的搜索入口（最靠上的含「搜索」文案节点）。"""
        hits = [n for n in screen.nodes
                if '搜索' in _norm(n.text) and n.bounds[3] < screen.height * 0.25]
        if not hits:
            return None
        return min(hits, key=lambda n: (n.bounds[1], n.bounds[0]))

    def _search_page_ready(self, screen):
        return (_is_jd(screen.package) and not self.homepage_visible(screen)
                and self._search_box(screen) is not None)

    def _tap_home_search(self, screen):
        """点开京东搜索页：优先点识别到的搜索入口，识别不到按布局兜底。"""
        entry = self._search_entry_node(screen)
        if entry is not None:
            self.click(entry)
            return True
        if not self.homepage_visible(screen):
            return False
        self.update(message='未识别到搜索入口，按首页布局点击搜索栏')
        self.click(TextNode('搜索栏', (int(screen.width * 0.2), int(screen.height * 0.04),
                                      int(screen.width * 0.8), int(screen.height * 0.1))))
        return True

    def _search_box(self, screen):
        """搜索页顶部的输入框：一条很宽的 EditText。

        config 的 search_input_ids 为空时接受任意顶部输入框；京东版本
        迭代资源名不稳定，不做白名单强校验，仅用类型+几何特征。
        """
        pending, fields = [screen.tree], []
        allowed = self.config.get('search_input_ids') or []
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                attrs = item.get('attributes', item)
                visible = attrs.get('visible-to-user', attrs.get('visible', True))
                if visible in (False, 'false'):
                    continue
                kind = attrs.get('type', attrs.get('class', ''))
                if 'EditText' in kind or 'TextInput' in kind:
                    box = bounds_of(attrs.get('bounds', attrs.get('rect')))
                    if (0 <= box[0] < box[2] <= screen.width and
                            screen.height*.02 < box[1] < box[3] < screen.height*.18
                            and box[2]-box[0] > screen.width*.4):
                        resource = attrs.get('id', attrs.get('resource-id', ''))
                        if allowed and not any(resource.endswith('/'+name) or resource == name
                                               for name in allowed):
                            continue
                        fields.append(TextNode(str(attrs.get('text') or '搜索框'), box))
                for key in ('children', 'hierarchy'):
                    if isinstance(item.get(key), (list, dict)):
                        pending.append(item[key])
        if len(fields) == 1:
            return fields[0]
        if len(fields) > 1:
            return None
        # OCR 降级：顶部的超宽横条（排除状态栏），文案含「搜索」或等于关键词
        hits = []
        keyword = self.config.get('task_search_keyword') or 'JOY游记'
        for n in screen.nodes:
            w, h = n.bounds[2] - n.bounds[0], n.bounds[3] - n.bounds[1]
            top = n.bounds[1]
            if not (w > screen.width * 0.6 and 0 < h < screen.height * 0.12):
                continue
            if not (screen.height * 0.02 < top < screen.height * 0.15):
                continue
            if '搜索' in _norm(n.text) or _norm(n.text) == _norm(keyword):
                hits.append(n)
        return min(hits, key=lambda n: n.bounds[1]) if hits else None

    def _search_result(self, screen):
        """搜索结果/联想里的 JOY游记 入口。

        输入框本身的 text 也是关键词，必须按几何排除，否则点了只是
        重新聚焦输入框。
        """
        keyword = (self.config.get('task_search_keyword')
                   or self.config.get('entry_labels', ['JOY游记'])[0] or 'JOY游记')
        hits = [n for n in screen.nodes
                if _norm(n.text).replace(' ', '') == _norm(keyword).replace(' ', '')
                and screen.height * 0.07 < n.bounds[1] < screen.height * 0.45
                and n.bounds[2] - n.bounds[0] < screen.width * 0.6
                and 0 < n.bounds[3] - n.bounds[1] < screen.height * 0.2]
        edit = self._search_box(screen)
        if edit:
            hits = [n for n in hits if not (edit.bounds[0] <= n.center[0] <= edit.bounds[2]
                                            and edit.bounds[1] <= n.center[1] <= edit.bounds[3])]
        if not hits:
            return None
        # 标题右侧同排有动作（去逛逛/立即进入/进入）时优先点动作。
        rows = [n for n in screen.nodes
                if _norm(n.text) in ('去看看', '去逛逛', '去浏览', '立即进入', '进入', '去完成')
                and any(n.bounds[0] >= title.bounds[2]
                        and max(n.bounds[1], title.bounds[1]) < min(n.bounds[3], title.bounds[3])
                        for title in hits)]
        return min(rows or hits, key=lambda n: (n.bounds[1], n.bounds[0])) if rows or hits else None

    def search_and_open(self, screen):
        """搜索直达 JOY游记：搜索栏 → 输入关键词 → 点结果卡片。

        只在还没落到任务面板时执行一次；失败留给框架的常规导航兜底。
        """
        if getattr(self, '_search_direct_done', False):
            return False
        if self._accept_list(screen) or not _is_jd(screen.package):
            return False
        keyword = (self.config.get('task_search_keyword') or '').strip() or 'JOY游记'
        self._search_direct_done = True
        try:
            # 0) 起点：退回到能点搜索的位置（首页，或已有搜索入口的页面）
            for _ in range(min(6, self.config.get('max_back_steps', 6))):
                self.guard(screen)
                if self._search_page_ready(screen) or self.homepage_visible(screen):
                    break
                self.device.go_back()
                self.control.wait(1.5)
                screen = self.read()
                if not _is_jd(screen.package):
                    return False     # 已经退出京东了，别再一路退到桌面
            # 1) 打开搜索页
            if not self._search_page_ready(screen):
                self.update(message='打开京东搜索')
                if not self._tap_home_search(screen):
                    return False
                self.control.wait(1.5)
                screen = self.read()
            if not self._search_page_ready(screen) and self.homepage_visible(screen):
                self.update(message='未进入搜索页，按首页布局重新点击搜索栏')
                self.click(TextNode('搜索栏', (int(screen.width * 0.2), int(screen.height * 0.04),
                                              int(screen.width * 0.8), int(screen.height * 0.1))))
                self.control.wait(2.0)
                screen = self.read()
            if not self._search_page_ready(screen):
                return False
            self.guard(screen)

            # 2) 输入关键词
            box = self._search_box(screen)
            if box:
                self.click(box)
                self.control.wait(1.2)
            self.update(message='搜索：' + keyword)
            self.device.input_text(keyword)
            self.control.wait(2.5)
            screen = self.read()

            # 3) 点开联想/结果里的活动入口。
            # 结果暂未加载时再读一次，仅点击已识别的活动入口。
            for attempt in range(2):
                self.guard(screen)
                result = self._search_result(screen)
                if result is None:
                    self.control.wait(1.5)
                    screen = self.read()
                    continue
                self.update(message='打开搜索结果里的活动')
                self.click(result)
                self.control.wait(3.5)
                screen = self.read()
                if self._accept_list(screen):
                    return True
                self.control.wait(1.5)
                screen = self.read()
                if self._accept_list(screen):
                    return True
            return False
        except (Stopped, NeedsAttention, InstanceMismatch):
            raise
        except Exception as exc:
            logger.info('搜索直达失败：%s', exc)
            return False

    def _bring_jd_front(self):
        """保证京东在前台（不在就拉起并等到真正切回来）。"""
        try:
            screen = self.read()
        except (Stopped, NeedsAttention, InstanceMismatch):
            raise
        except Exception:
            screen = None
        if screen is not None and _is_jd(screen.package):
            return screen
        try:
            package = getattr(self, 'origin', None) or self.application.package_for(self.device.device_type)
            self.device.start_app(package)
        except (Stopped, NeedsAttention, InstanceMismatch):
            raise
        except Exception:
            logger.info('拉起京东失败')
            return screen
        for _ in range(8):
            self.control.wait(2.0)
            try:
                screen = self.read()
            except (Stopped, NeedsAttention, InstanceMismatch):
                raise
            except Exception:
                return None
            if screen is not None and _is_jd(screen.package):
                return screen
        return screen

    def _accept_list(self, screen):
        """在 JOY游记任务面板（或活动主页的任务板块）即算到位。

        浏览类任务做完当天不会再出现，此时应正常报告"无任务"，
        而不是一直重试进面板。
        """
        self.assert_instance(screen)
        if self.is_task_list(screen.nodes) and _is_jd(screen.package):
            self.origin = screen.package
            return True
        return False

    def open_home_search(self, screen):
        """搜索已由 search_and_open 有界重试；常规导航不重复发起同一搜索。"""
        return False

    # ---- 列表内的「更多任务」分页 ----

    def browse_swipe(self, screen):
        """H5 任务面板吃不住框架默认的随机小幅手势，用稳定长距离滑动。"""
        x = int(screen.width * 0.5)
        self.device.swipe(x, int(screen.height * 0.82),
                          x, int(screen.height * 0.38), duration=0.8)

    def alt_swipe(self, screen):
        """备用方向：换批后视口停在底部，剩下的行要朝反方向滑。"""
        x = int(screen.width * 0.5)
        self.device.swipe(x, int(screen.height * 0.38),
                          x, int(screen.height * 0.82), duration=0.8)
        return True

    def _more_tasks_button(self, screen):
        label = _norm(self.config.get('more_tasks_label', '更多任务'))
        return next((n for n in screen.nodes if _norm(n.text) == label), None)

    @staticmethod
    def _view_signature(screen):
        return tuple(sorted({_norm(n.text) for n in screen.nodes if _norm(n.text)}))

    def view_signature(self, screen):
        return self._view_signature(screen)

    def next_list_page(self, screen):
        """主循环翻页契约：滑到底后用「更多任务」换下一批。"""
        return self._more_tasks_button(screen)

    # ---- 打开任务面板 ----

    def enter_task_panel(self, screen, expected_activity=False):
        """从搜索结果/活动主页进入任务面板，并等待面板出现。"""
        deadline = self.control.now() + self.config['page_timeout']
        attempts = 0
        last_tap = self.control.now() - 10
        while True:
            self.guard(screen)
            if self._accept_list(screen):
                return screen
            if not _is_jd(screen.package):
                return screen
            if self.control.now() >= deadline:
                return screen
            target = self.task_panel_entry(screen)
            if target is not None and self.control.now() - last_tap > 3:
                self.click(target)
                last_tap = self.control.now()
                attempts += 1
            self.control.wait(1.2)
            screen = self.read()

    def ensure_task_list(self, screen):
        """确保当前页就是 JOY游记任务面板；不在就退回，退回无用则重新搜索进入。"""
        if self._accept_list(screen):
            return screen
        for _ in range(4):
            self.device.go_back()
            self.control.wait(1.2)
            try:
                screen = self.read()
            except (Stopped, NeedsAttention, InstanceMismatch):
                raise
            except Exception:
                break
            if self._accept_list(screen):
                return screen
        # 逐级返回不足以回来：退出到京东首页，重新搜索进入。
        logger.info('未能返回JOY游记任务面板，退出到首页后重新搜索进入')
        screen = self.return_to_home(screen)
        self._search_direct_done = False
        try:
            if self.search_and_open(screen):
                screen = self.read()
                if self._accept_list(screen):
                    return screen
        except (Stopped, NeedsAttention, InstanceMismatch):
            raise
        except Exception as exc:
            logger.info('重新搜索进入失败：%s', exc)
        # 搜索过程中可能把京东退掉了，交给框架前再兜一次
        self._bring_jd_front()
        return super().ensure_list()
