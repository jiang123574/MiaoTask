"""JOY游记浏览任务执行（2026-11-04 真机核对）。

真机流程：
    点任务行右侧「逛一逛」 → 京东站内会场页（同包名，独立滚动）
    → 停留浏览 10 秒左右并滑动几次 → 返回键回到活动页任务面板
    → 该任务行从面板消失（今日已完成），或进度 (0/8)→(1/8)

注意事项：
* 京东的浏览任务是「跳会场页」，不是淘宝那种列表内滑动，因此必须
  真的等够 10 秒再返回，早退拿不到晶石。
* 返回后可能落在活动主页而不是任务面板，需要 ensure_task_list 兜一层。
* 完成后整行会从面板移除，所以「行消失」是正常完成信号，不是失败。
"""
import logging

from backend.core.models import TextNode, bounds_of, normalize

logger = logging.getLogger('backend.execution')


class TaskActions:
    def _button_node(self, screen, task):
        """按标题在列表中匹配任务按钮，返回带 center 的 TextNode。"""
        for t in self.tasks(screen):
            if t.title == task.title or t.key == task.key:
                return TextNode('', t.bounds)
        return None

    def _reward_popup(self, screen):
        """奖励到账确认浮层。"""
        label = normalize(self.config.get('reward_popup_text', '恭喜'))
        return next((n for n in screen.nodes if label in normalize(n.text)), None)

    def _popup_closer(self, screen):
        """弹窗的关闭控件（下半屏的 ✕ / 关闭 / 我知道了）。

        优先找带文案的关闭按钮；找不到再按几何特征找 H5 渲染的
        圆形 ✕（空文本的近正方形小节点，水平居中、位于下半屏）。
        都找不到则由调用方按返回键兜底。
        """
        patterns = self.config.get('popup_close_labels') or ['关闭', '取消', '✕', '×']
        for n in screen.nodes:
            if n.center[1] <= screen.height * 0.5:
                continue
            w, h = n.bounds[2] - n.bounds[0], n.bounds[3] - n.bounds[1]
            if w > screen.width * 0.25 or h > screen.height * 0.12:
                continue  # 太大，不是图标按钮
            text = normalize(n.text)
            if any(normalize(p) in text or text == normalize(p) for p in patterns):
                return n
        return self._graphic_close(screen)

    def _graphic_close(self, screen):
        """H5 图形关闭按钮（圆形 ✕）：空文本 + 近正方形小节点 + 下半屏居中。"""
        if not getattr(screen, 'tree', None):
            return None
        w0, h0 = screen.width, screen.height
        cands = []

        def visit(item):
            if isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                attrs = item.get('attributes', item)
                text = (attrs.get('text') or attrs.get('description')
                        or attrs.get('content-desc') or '')
                if not normalize(text):
                    b = bounds_of(attrs.get('bounds', attrs.get('rect')))
                    w, h = b[2] - b[0], b[3] - b[1]
                    if (0.03 * w0 <= w <= 0.18 * w0
                            and 0.03 * h0 <= h <= 0.12 * h0
                            and 0.6 <= w / max(h, 1) <= 1.7):
                        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                        if cy > h0 * 0.5:
                            cands.append((abs(cx - w0 / 2),
                                          abs(cy - h0 * 0.85), b))
                for key in ('children', 'hierarchy'):
                    for child in item.get(key, ()) or ():
                        visit(child)

        visit(screen.tree)
        return TextNode('', min(cands)[2]) if cands else None

    # ---- 浏览会场页 ----

    def _browse_scroll(self, seconds):
        """在会场页停留指定时长，期间滑动两次模拟真实浏览。"""
        remaining = float(seconds)
        swipes = 0
        while remaining > 0:
            step = min(3.0, remaining)
            self.control.wait(step)
            remaining -= step
            if swipes < 2 and remaining > 1.5:
                try:
                    self.swipe_browse()
                    swipes += 1
                except Exception as exc:      # 滑动失败不影响计时
                    logger.info('会场页滑动失败：%s', exc)

    def swipe_browse(self):
        """会场页统一用长距离上滑（H5 页面吃不住小幅随机手势）。"""
        screen = self.read()
        x = int(screen.width * 0.5)
        self.device.swipe(x, int(screen.height * 0.82),
                          x, int(screen.height * 0.38), duration=0.7)

    # ---- 主执行 ----

    def execute(self, task, before):
        self._active_task = task
        self.update('opening', message='打开任务：' + task.title, remaining=0)
        screen = self.read()
        target = self._button_node(screen, task)
        if not target:
            logger.info('未在当前列表找到任务按钮：%s', task.title)
            return self.recover(), False, False
        self.click(target)
        self.control.wait(1.2)

        # 京东的浏览任务应跳到站内会场页（同包名）。仍在任务面板说明
        # 点击没生效或弹出了说明层，短暂等待后按返回键兜底。
        landed = self.read()
        if self.is_task_list(landed.nodes):
            self.control.wait(1.2)
            landed = self.read()
            if self.is_task_list(landed.nodes):
                logger.info('点击后仍在任务面板，按返回键兜底：%s', task.title)
                self.device.go_back()
                self.control.wait(1.0)

        seconds = float(self.config.get('browse_shortcut_seconds', 12))
        self.update('browsing', message=f'浏览会场 {int(seconds)} 秒：' + task.title)
        self._browse_scroll(seconds)

        # 返回活动页
        self.device.go_back()
        self.control.wait(1.6)
        screen = self.read()
        if not (self.is_task_list(screen.nodes) or self.activity_landing(screen)):
            screen = self.ensure_task_list(screen)

        # 奖励到账浮层：最直接的完成证据，关闭后继续下一个
        popup = self._reward_popup(screen)
        if popup:
            self.update('browsing', message='奖励已到账，关闭提示浮层')
            closer = self._popup_closer(screen)
            if closer:
                logger.info('关闭奖励浮层：命中关闭控件 bounds=' + str(closer.bounds))
                self.click(closer)
                self.control.wait(1.2)
                screen = self.read()
            if self._reward_popup(screen):
                self.device.go_back()
                self.control.wait(1.2)
                screen = self.read()
            return screen, True, False

        return screen, self._row_finished(task, screen), False

    def _row_finished(self, task, screen):
        """任务行是否已结算：行消失、进度前进、或行尾变「已完成」。"""
        if not (self.is_task_list(screen.nodes) or self.activity_landing(screen)):
            return False
        rows = {t.title: t for t in self.tasks(screen)}
        row = rows.get(task.title)
        if row is not None:
            if row.current > task.current:
                return True
            done = getattr(self, 'parse_done_titles', None)
            return bool(done and task.title in done(screen.nodes, screen.width, screen.height))
        # 真机核对：完成的浏览任务会整行从面板移除。
        return True
