# -*- coding: utf-8 -*-
"""用 PostMessage 往游戏窗口投键鼠消息（ok-ww 用的就是这条路）
UE 游戏的 lParam 必须带扫描码，否则不认。
"""
import os, sys, time, ctypes
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
try:
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
except Exception:
    pass
MK_RBUTTON = 0x0002


def find_game():
    for w in shot.enum_windows():
        if 'Unreal' in w['cls']:
            return w
    return None


def vk_to_scan(vk):
    return user32.MapVirtualKeyW(int(vk), 0)


def _signed32(x):
    """PostMessageW 的 lParam 是 32 位有符号，超过 0x7FFFFFFF 必须折回来，
    否则 ctypes 报 'int too long to convert'。"""
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def make_lparam(vk, up=False):
    scan = vk_to_scan(vk)
    lp = 1 | (scan << 16)
    if up:
        lp |= (1 << 30) | (1 << 31) | (1 << 29)
    return _signed32(lp)


def post_key(hwnd, vk, hold_ms=80):
    dn = make_lparam(vk, False)
    up = make_lparam(vk, True)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, dn)
    time.sleep(hold_ms / 1000.0)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, up)


def post_key_hold(hwnd, vk, ms):
    dn = make_lparam(vk, False)
    up = make_lparam(vk, True)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, dn)
    # 按住期间需要重发（UE 用自动重复判断持续按住）
    step = 30
    t = 0
    while t < ms:
        time.sleep(step / 1000.0)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, dn | (1 << 30))
        t += step
    user32.PostMessageW(hwnd, WM_KEYUP, vk, up)


def post_click(hwnd, x, y, delay=0.08):
    """x,y 是客户区坐标"""
    lp = (int(y) << 16) | (int(x) & 0xFFFF)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.03)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(delay)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def screen_to_client(hwnd, sx, sy):
    pt = wintypes.POINT(int(sx), int(sy))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    g = find_game()
    if not g:
        print('找不到游戏窗口')
        return
    hwnd = g['hwnd']
    print('hwnd=%s title=%r rect=%s' % (hwnd, g['title'], g['rect']))

    if cmd == 'key':
        vk = int(sys.argv[2], 0)
        ms = int(sys.argv[3]) if len(sys.argv) > 3 else 500
        post_key_hold(hwnd, vk, ms)
        print('posted VK 0x%02X hold %dms (scan=%d)' % (vk, ms, vk_to_scan(vk)))
    elif cmd == 'click':
        # 传窗口内坐标，自动换算客户区
        wx, wy = int(sys.argv[2]), int(sys.argv[3])
        l, t, r, b = g['rect']
        cx, cy = screen_to_client(hwnd, l + wx, t + wy)
        post_click(hwnd, cx, cy)
        print('clicked client(%d,%d)' % (cx, cy))


if __name__ == '__main__':
    main()
