"""JOY游记任务列表解析。

活动结构（2026-11 攻略核对，坐标与文案待真机核对）：
* 活动主页下方「更多任务 / 赚晶石」板块和任务面板混着大量非浏览类
  任务（下单、开通 PLUS、订阅频道、填问卷…），这些一律不碰。
* 浏览类任务形如「浏览商品15秒」「逛会场领晶石」「看视频得奖励」，
  行尾按钮是「去完成 / 去浏览 / 去逛逛 / 去看看」。

与支付宝月月赚相同，这里在解析层**硬编码**浏览类过滤，而不是放进
config：安全属性不能因为改配置而失效——只能按「标题」命中浏览类才
生成任务，否则今天的手滑会让脚本去开一个 PLUS 会员。
"""
import re

from backend.core.models import Task, normalize

CLAIM_ACTIONS = frozenset({'claim'})
EXTERNAL_APPS = ()

# 浏览类任务的标题特征。JOY游记的轻任务都以「看/逛/浏览」开头或包含。
BROWSE_TITLE_RE = re.compile(r'浏览|逛|看一看|看看|逛逛|视频')

# 列表中永远不是"任务按钮"的文案，避免把它们当成任务标题
BUTTON_TEXT = {'去完成', '去看看', '去浏览', '去逛逛', '领取', '立即领取',
               '做任务', '更多任务', '已完成', '已领取'}

# 浏览类任务的按钮文案；「去完成」在京东任务面板中同时被商业任务使用，
# 因此按钮命中后仍必须通过标题的 BROWSE_TITLE_RE 复核。
BROWSE_BUTTONS = {'去完成', '去看看', '去浏览', '去逛逛'}

# 页面身份锁：JOY游记的活动标题。搜索结果页、其他互动游戏页不会
# 同时出现身份标题和成排任务按钮。
ACTIVITY_TITLES = ('JOY游记', 'JOY 游记', 'joy游记')


def _norm(text):
    return normalize(text or '')


def _has_activity_title(nodes):
    """上半屏出现「JOY游记」标题——这是任务面板的身份锁。

    京东其他互动游戏（东东农场、全民夺宝…）的任务面板同样满是
    「去完成」按钮和「浏览xx」标题，标题栏写的是它们自己的名字。
    """
    return any(_norm(n.text).replace(' ', '') in
               tuple(t.replace(' ', '') for t in ACTIVITY_TITLES)
               for n in nodes)


def is_task_list(nodes):
    """当前屏幕是否为 JOY游记任务面板（或活动主页的任务板块）。"""
    texts = [_norm(n.text) for n in nodes]
    buttons = ('去完成', '去看看', '去浏览', '去逛逛', '去领取', '领取', '已完成')
    actions = [t for t in texts if t in buttons]
    if len(actions) < 2:
        # 搜索结果页只有一个入口按钮，不构成任务面板。
        return False
    return _has_activity_title(nodes)


def _title_near(nodes, button, width, height):
    """在按钮同行或紧邻上方找任务标题（与月月赚同规则）。"""
    bx0, by0, bx1, by1 = button.bounds
    bcy = (by0 + by1) / 2
    best = None
    best_key = None
    for n in nodes:
        if n is button:
            continue
        t = _norm(n.text)
        if not t or t in BUTTON_TEXT:
            continue
        nx0, ny0, nx1, ny1 = n.bounds
        if ny0 > by1:                      # 完全在按钮下方，属于另一行
            continue
        gap = max(0, by0 - ny1)            # 与按钮顶部的纵向空隙
        if gap > height * 0.12:            # 离得太远，不是同一行
            continue
        hdist = max(0, bx0 - nx1)          # 标题在按钮左侧的横向间隔
        if hdist > width * 0.35:           # 横向太远
            continue
        key = (round(abs((ny0 + ny1) / 2 - bcy)), gap, hdist)
        if best_key is None or key < best_key:
            best_key, best = key, t
    return best


def parse_tasks(nodes, width, height):
    """只解析「浏览类」任务，其余一概跳过。"""
    tasks = []
    seen = set()
    for n in nodes:
        if _norm(n.text) not in BROWSE_BUTTONS:
            continue
        title = _title_near(nodes, n, width, height)
        if not title or not BROWSE_TITLE_RE.search(title):
            continue  # 商业/开通类任务，安全起见直接跳过
        if title in seen:
            continue
        seen.add(title)
        tasks.append(Task(
            key=title + '#browse',
            title=title,
            detail=_norm(n.text),
            action='browse',
            bounds=n.bounds,
            kind='browse',
            current=0,
            total=1,
        ))
    return tasks
