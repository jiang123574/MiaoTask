"""JOY游记任务列表解析（2026-11-04 真机核对）。

面板真实结构（1440x3200 逻辑 1080x2400）：

    做任务赚晶石
    浏览指定会场(0/1)        [逛一逛]   浏览10s+15
    逛AI办公提效(0/1)        [逛一逛]   浏览10s+15
    逛一逛精选活动(0/8)      [去完成]   浏览10s可得+25
    组队天天赢黄金(0/1)      [逛一逛]   浏览10s+15
    领最高80元微信现金(0/1)  [逛一逛]   完成访问+15
    每日领京豆/现金(0/1)     [逛一逛]   浏览10s+15
    点击查收AI虚拟宠物(0/1)  [去完成]   与DD聊一次天+50   ← 非浏览，必须排除

每行是「标题(含进度) + 右侧按钮 + 下方奖励副标题」三段式。按钮的
真实文案是「逛一逛」，不是通用词表里的「去逛逛」，因此判定不能只看
按钮；**最可靠的浏览信号是副标题里的奖励描述**（浏览10s / 完成访问），
商业任务（下单、开通、聊天…）的副标题是「下单得…」「与DD聊一次天」。

安全属性仍然硬编码在解析层：宁可漏任务，也不能因为改配置去开通会员。
"""
import re

from backend.core.models import Task, normalize

CLAIM_ACTIONS = frozenset({'claim'})
EXTERNAL_APPS = ()

# 任务行右侧按钮（真机核对）。「去签到」是独立的 5 天签到条，不在此列。
ROW_BUTTONS = frozenset({'逛一逛', '去逛逛', '去看看', '去浏览', '去完成'})

# 不是任务标题的文案
BUTTON_TEXT = ROW_BUTTONS | {'领取', '立即领取', '做任务', '更多任务',
                             '已完成', '已领取', '去签到', '签到'}

# 浏览类的真实信号：奖励副标题里的时长/访问描述
SUBTITLE_RE = re.compile(r'浏览\s*\d+\s*(?:秒|[sS])|浏览可得|浏览得|完成访问')

# 标题兜底：老版本或半屏面板没有副标题时，按标题关键词判断
BROWSE_TITLE_RE = re.compile(r'浏览|逛|看一看|看看|视频')

# 安全排除：商业/授权类任务，标题或副标题命中即跳过，不受配置影响
UNSAFE_RE = re.compile(
    r'开通|会员|下单|购买|支付|充值|订阅|预约|关注|绑定|授权|贷款|理财|'
    r'信用卡|报名|加购|领券|聊天|与\S{0,4}聊|虚拟宠物|宠物|首购|分期')

# 页面身份锁：活动标题（OCR 常把返回箭头粘在前面，如「<JOY游记」）
ACTIVITY_TITLES = ('JOY游记', 'JOY 游记')

# 面板标题，作为身份锁的补充证据
PANEL_TITLES = ('做任务赚晶石', '赚晶石')

PROGRESS_RE = re.compile(r'[（(](\d+)\s*/\s*(\d+)[）)]')


def _norm(text):
    return normalize(text or '')


def _compact(text):
    return _norm(text).replace(' ', '')


def _has_activity_title(nodes):
    """上半屏出现「JOY游记」标题——这是任务面板的身份锁。

    京东其他互动游戏（东东农场、全民夺宝…）的任务面板同样满是
    「去完成」按钮和「浏览xx」标题，标题栏写的是它们自己的名字。
    """
    titles = tuple(_compact(t) for t in ACTIVITY_TITLES)
    for n in nodes:
        text = _compact(n.text)
        if not text:
            continue
        if any(t in text for t in titles):
            return True
    return False


def is_task_list(nodes):
    """当前屏幕是否为 JOY游记任务面板。

    真机面板同时具备：身份标题「JOY游记」+ 成排任务按钮（逛一逛/去完成）。
    活动主页只有底部导航（去赚奖励/领红包/去寻宝），不构成任务面板。
    """
    texts = [_norm(n.text) for n in nodes]
    actions = [t for t in texts if t in ROW_BUTTONS]
    if len(actions) < 2:
        return False
    if any(any(_compact(t) in _compact(text) for t in PANEL_TITLES) for text in texts):
        return True
    return _has_activity_title(nodes)


def _row_nodes(nodes, button, width, height):
    """取出按钮所在行的左侧节点（标题在上、副标题在下）。"""
    bx0, by0, bx1, by1 = button.bounds
    bcy = (by0 + by1) / 2
    left = []
    for n in nodes:
        if n is button:
            continue
        text = _norm(n.text)
        if not text or text in BUTTON_TEXT:
            continue
        if n.bounds[2] > bx0 - width * 0.02:      # 必须在按钮左侧
            continue
        cy = (n.bounds[1] + n.bounds[3]) / 2
        # 行内标题/按钮/副标题的纵向偏差远小于行距（真机行距约 0.09H），
        # 收紧到 0.045H，避免把上一行的副标题或面板标题拉进来。
        if abs(cy - bcy) > height * 0.045:
            continue
        left.append(n)
    if not left:
        return None, None
    # 标题是与按钮纵向中心最近的左列节点（真机：标题-按钮 27px，
    # 副标题-按钮 33px，都比上一行近一个行距）。
    title = min(left, key=lambda n: abs((n.bounds[1] + n.bounds[3]) / 2 - bcy))
    # 副标题紧贴标题下方（真机 12px）；再往下就是下一行的标题了。
    below = [n for n in left
             if n.bounds[1] >= title.bounds[3]
             and n.bounds[1] - title.bounds[3] <= height * 0.015]
    subtitle = max(below, key=lambda n: n.bounds[1]) if below else None
    return title, subtitle


def _progress(text):
    m = PROGRESS_RE.search(text or '')
    return (int(m.group(1)), int(m.group(2))) if m else (0, 1)


def _is_browse(title, subtitle):
    """浏览类判定：副标题优先，标题兜底，安全排除最高优先。"""
    t, s = _norm(title), _norm(subtitle or '')
    if UNSAFE_RE.search(t) or UNSAFE_RE.search(s):
        return False
    if SUBTITLE_RE.search(s):
        return True
    return bool(BROWSE_TITLE_RE.search(t))


def parse_tasks(nodes, width, height):
    """只解析「浏览类」任务，其余一概跳过。"""
    tasks = []
    seen = set()
    for n in nodes:
        if _norm(n.text) not in ROW_BUTTONS:
            continue
        title_node, subtitle_node = _row_nodes(nodes, n, width, height)
        if not title_node:
            continue
        raw_title = _norm(title_node.text)
        subtitle = _norm(subtitle_node.text) if subtitle_node else ''
        if not _is_browse(raw_title, subtitle):
            continue                              # 商业/聊天类，安全跳过
        current, total = _progress(raw_title)
        title = PROGRESS_RE.sub('', raw_title).strip() or raw_title
        if title in seen:
            continue
        seen.add(title)
        tasks.append(Task(
            key=title + '#browse',
            title=title,
            detail=subtitle or _norm(n.text),
            action='browse',
            bounds=n.bounds,
            kind='browse',
            current=current,
            total=total,
        ))
    return tasks


def parse_done_titles(nodes, width, height):
    """已完成的浏览任务标题（行仍在但按钮变成「已完成」时用）。"""
    done = set()
    for n in nodes:
        if _norm(n.text) not in ('已完成', '已领取'):
            continue
        title_node, _ = _row_nodes(nodes, n, width, height)
        if title_node:
            done.add(PROGRESS_RE.sub('', _norm(title_node.text)).strip())
    return done
