"""真机调试：京东首页搜索「JOY游记」进入活动，OCR 识别并报告页面内容。"""
import sys, time, pathlib
sys.path.insert(0, '.')
from backend.core.device.adapter import DeviceAdapter
from backend.core.device.screen import ScreenReader

OUT = pathlib.Path('runtime/debug')
OUT.mkdir(parents=True, exist_ok=True)

device = DeviceAdapter('uiautomator2', 'a375e2cf')
reader = ScreenReader(device, ocr=True)
reader.ocr_threads = 4
device.wake_and_unlock()


def read(tag, force_ocr=True):
    screen = reader.read(force_ocr=force_ocr)
    print(f'\n===== {tag} pkg={screen.package} src={screen.source} =====')
    for n in screen.nodes:
        print(f'  {n.bounds}  {n.text!r}')
    if reader.last_image is not None:
        reader.last_image.save(OUT / f'{tag}.png')
    return screen


def go_home_search():
    """确保在京东首页，然后点开搜索页。"""
    info = device.current_app()
    if not (info.get('package') or '').startswith('com.jingdong.app.mall'):
        device.start_app('com.jingdong.app.mall')
        time.sleep(6)
    screen = read('start')
    if '搜索' not in ' '.join(n.text for n in screen.nodes):
        device.go_back()
        time.sleep(1.5)
        screen = read('after-back')
    # 首页顶部的搜索栏（OCR 显示「搜索」两字，宽度大、位于顶部）
    entry = min((n for n in screen.nodes
                 if '搜索' in n.text and n.bounds[3] < screen.height * 0.25),
                key=lambda n: (n.bounds[1], n.bounds[0]), default=None)
    if not entry:
        print('未找到搜索入口'); return False
    print('点搜索入口:', entry.bounds)
    device.click(*entry.center)
    time.sleep(2.5)
    screen = read('search-page')
    box = None
    for n in screen.nodes:
        if '搜索' in n.text or 'JOY' in n.text:
            if n.bounds[1] < screen.height * 0.2:
                box = n
                break
    if box is None:
        print('未找到输入框，直接输入试试')
    else:
        device.click(*box.center)
        time.sleep(1.2)
    device.input_text('JOY游记')
    time.sleep(3)
    screen = read('typed')
    # 点联想/结果里第一个含 JOY游记 的条目（排除输入框本身）
    hits = [n for n in screen.nodes
            if 'JOY游记' in n.text.replace(' ', '')
            and n.bounds[1] > screen.height * 0.12
            and n.bounds[2] - n.bounds[0] < screen.width * 0.8]
    if not hits:
        print('未识别到联想词，请人工看截图'); return False
    target = min(hits, key=lambda n: n.bounds[1])
    print('点联想/结果:', target.text, target.bounds)
    device.click(*target.center)
    time.sleep(3.5)
    read('result')
    return True


if __name__ == '__main__':
    go_home_search()
