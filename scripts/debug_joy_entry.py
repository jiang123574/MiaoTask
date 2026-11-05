"""真机调试：京东首页 → 点我领奖 → JOY游记。

用法：
    python scripts/debug_joy_entry.py            # 读当前页
    python scripts/debug_joy_entry.py 点我领奖    # 找到并点击
"""
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


def ensure_foreground():
    info = device.current_app()
    if (info.get('package') or '').startswith('com.jingdong.app.mall'):
        return info
    device.start_app('com.jingdong.app.mall')
    time.sleep(6)
    return device.current_app()


def read_screen(tag, force_ocr=True):
    t0 = time.time()
    screen = reader.read(force_ocr=force_ocr)
    print(f'\n===== {tag} ({time.time()-t0:.1f}s pkg={screen.package} src={screen.source}) =====')
    for n in screen.nodes:
        print(f'  {n.bounds}  {n.text!r}')
    if reader.last_image is not None:
        path = OUT / f'{tag}.png'
        reader.last_image.save(path)
        print(f'  截图 {path}')
    return screen


def find(screen, keyword):
    hits = [n for n in screen.nodes if keyword in n.text.replace(' ', '')]
    if hits:
        return hits[0]
    return None


def main():
    info = ensure_foreground()
    print('前台:', info)
    screen = read_screen('jd-home')
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
        node = find(screen, keyword)
        print('\n命中:', node)
        if node:
            device.click(*node.center)
            print('已点击，等待 5 秒...')
            time.sleep(5)
            read_screen('after-click')


if __name__ == '__main__':
    main()
