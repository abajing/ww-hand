# -*- coding: utf-8 -*-
"""窗口截图底座：找窗口 -> 全屏抓取 -> 裁窗口区 -> 落盘
用法: python shot.py [标题关键字] [输出名]
"""
import sys, os, time, ctypes
from ctypes import wintypes
from PIL import ImageGrab

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_shots')
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# 必须显式声明 argtypes：x64 下 HWND 是 64 位，不声明时 ctypes 按默认 c_int 传递，
# 遇到高位句柄就抛 OverflowError: int too long to convert（曾让 enum_windows 整个崩掉）
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetWindowRect.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

def enum_windows(min_w=300, min_h=200):
    """枚举可见且有标题的窗口。

    以前尺寸门槛 300x200 是硬编码的，为的是跳过鸣潮那个 160x24 的托盘幽灵窗口。
    但「手」现在要通用，操作一个 280 宽的小对话框时它就被过滤没了 ——
    所以门槛做成参数，默认值保持原行为，通用场景传小一点。
    """
    res = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w > min_w and h > min_h:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            res.append({'hwnd': hwnd, 'title': buf.value, 'cls': cls.value,
                        'rect': (r.left, r.top, r.right, r.bottom), 'w': w, 'h': h})
        return True
    user32.EnumWindows(CB(cb), 0)
    return res

def find(keyword=None):
    wins = enum_windows()
    if keyword:
        hit = [w for w in wins if keyword.lower() in w['title'].lower()]
        if hit:
            return hit
    if keyword and 'Wuthering' in keyword:
        pass
    return wins

def virtual_screen():
    """整个虚拟桌面（多显示器拼起来那块）"""
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return {'hwnd': 0, 'title': '<screen>', 'cls': '<screen>',
            'rect': (x, y, x + w, y + h), 'w': w, 'h': h}

def grab(rect):
    """按屏幕绝对坐标截一块。

    多显示器坑：ImageGrab.grab(all_screens=True) 返回的图，
    左上角对应的是虚拟桌面原点 (SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN)，
    不一定是 (0,0)。直接拿绝对坐标 crop 会在副屏在左/上方时整体偏移。
    这里按虚拟原点换算。
    """
    l, t, r, b = rect
    vs = virtual_screen()
    ox, oy = vs['rect'][0], vs['rect'][1]
    img = ImageGrab.grab(all_screens=True)
    return img.crop((l - ox, t - oy, r - ox, b - oy))

def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else None
    name = sys.argv[2] if len(sys.argv) > 2 else 'shot'
    wins = find(kw)
    if not wins:
        print('NO_WINDOW')
        for w in enum_windows():
            print('  ', w['title'], w['cls'], w['w'], 'x', w['h'])
        return
    tgt = wins[0]
    img = grab(tgt['rect'])
    p = os.path.join(OUT, name + '.png')
    img.save(p)
    print('SAVED', p, img.size, 'rect=', tgt['rect'], 'title=', tgt['title'])
    return

if __name__ == '__main__':
    main()
