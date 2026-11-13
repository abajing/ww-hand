# -*- coding: utf-8 -*-
"""键鼠事件底座：真实 SendInput，不带驱动，不做注入
用法:
  python tap.py move X Y
  python tap.py click X Y [button]
  python tap.py dclick X Y
  python tap.py drag X1 Y1 X2 Y2 [ms]
  python tap.py key VKCODE [hold_ms]
  python tap.py hold VKCODE ms
坐标一律是【屏幕绝对坐标】。
"""
import sys, time, ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# 把系统时钟粒度从默认 15.6ms 调到 1ms。
# 不调的话，任何 time.sleep(0.005) 实际都睡 15.6ms 起步，
# 按键"按住 10 毫秒"根本做不到准，发键开销里一大半是这个。
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
    _TIMER_1MS = True
except Exception:
    _TIMER_1MS = False

PUL = ctypes.POINTER(ctypes.c_ulong)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]

class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", ctypes.c_ulong), ("i", _I)]

# 急停闸：所有发键最终都走 _send()，在这里拦一处就覆盖全部脚本。
# 每 50ms 才真查一次（查一次是文件系统调用，几十微秒），
# 缓存窗口内直接放行，不影响发键速度。
_stop_mod = None
_stop_last = 0.0
_STOP_CACHE = 0.05


def _check_stop():
    global _stop_mod, _stop_last
    now = time.perf_counter()
    if now - _stop_last < _STOP_CACHE:
        return
    _stop_last = now
    if _stop_mod is None:
        try:
            import stop as _s
            _stop_mod = _s
        except Exception:
            _stop_mod = False
    if _stop_mod and _stop_mod.should_stop():
        raise _stop_mod.Stopped('紧急停止：%s' % _stop_mod.reason())


def _send(*inputs):
    _check_stop()
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))

def move(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)

def _mouse(flags, data=0):
    mi = MOUSEINPUT(0, 0, data, flags, 0, None)
    inp = INPUT(type=0)
    inp.mi = mi
    _send(inp)

def click(x=None, y=None, button='left', delay=0.05):
    if x is not None:
        move(x, y)
        time.sleep(0.03)
    down = 0x0002 if button == 'left' else (0x0008 if button == 'right' else 0x0020)
    up = 0x0004 if button == 'left' else (0x0010 if button == 'right' else 0x0040)
    _mouse(down); time.sleep(delay); _mouse(up)

def dclick(x, y):
    move(x, y); time.sleep(0.03)
    click(); time.sleep(0.05); click()

def drag(x1, y1, x2, y2, ms=400, steps=24):
    move(x1, y1); time.sleep(0.05)
    _mouse(0x0002)
    for i in range(1, steps + 1):
        t = i / steps
        move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        time.sleep(ms / 1000.0 / steps)
    _mouse(0x0004)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001


def key_down_scan(vk):
    scan = user32.MapVirtualKeyW(int(vk), 0)
    ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, None)
    inp = INPUT(type=1); inp.ki = ki
    _send(inp)


def key_up_scan(vk):
    scan = user32.MapVirtualKeyW(int(vk), 0)
    ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
    inp = INPUT(type=1); inp.ki = ki
    _send(inp)


def mouse_rel(dx, dy=0):
    """相对移动鼠标（转镜头用）"""
    mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF_MOVE, 0, None)
    inp = INPUT(type=0)
    inp.mi = mi
    _send(inp)


def key(vk, hold_ms=60):
    """虚拟键码方式"""
    ki = KEYBDINPUT(int(vk), 0, 0, 0, None); inp = INPUT(type=1); inp.ki = ki
    _send(inp)
    time.sleep(hold_ms / 1000.0)
    ki2 = KEYBDINPUT(int(vk), 0, KEYEVENTF_KEYUP, 0, None); inp2 = INPUT(type=1); inp2.ki = ki2
    _send(inp2)


def key_scan(vk, hold_ms=60):
    """扫描码方式 —— UE/Unity 游戏多半只认这个"""
    scan = user32.MapVirtualKeyW(int(vk), 0)
    if scan == 0:
        return key(vk, hold_ms)
    ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, None)
    inp = INPUT(type=1); inp.ki = ki
    _send(inp)
    time.sleep(hold_ms / 1000.0)
    ki2 = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
    inp2 = INPUT(type=1); inp2.ki = ki2
    _send(inp2)


def hold_scan(vk, ms):
    """按住不放（带扫描码），到点再松"""
    scan = user32.MapVirtualKeyW(int(vk), 0)
    ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, None)
    inp = INPUT(type=1); inp.ki = ki
    _send(inp)
    time.sleep(ms / 1000.0)
    ki2 = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
    inp2 = INPUT(type=1); inp2.ki = ki2
    _send(inp2)


# ---- 高频路径：预先算好扫描码，down/up 各一次 SendInput，中间不留 sleep ----

_scan_cache = {}


def _scan(vk):
    s = _scan_cache.get(vk)
    if s is None:
        s = user32.MapVirtualKeyW(int(vk), 0)
        _scan_cache[vk] = s
    return s


def tap_scan(vk):
    """扫描码敲一下，down+up 一次性打包发出（单次 SendInput，中间零等待）。

    UE/Unity 认扫描码；打包发送省掉 down 与 up 之间的 sleep。
    """
    scan = _scan(vk)
    if not scan:
        return tap_vk(vk)
    ki_d = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, None)
    ki_u = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
    i1 = INPUT(type=1); i1.ki = ki_d
    i2 = INPUT(type=1); i2.ki = ki_u
    _send(i1, i2)


def tap_vk(vk):
    """虚拟键码版，同样打包发送"""
    ki_d = KEYBDINPUT(int(vk), 0, 0, 0, None)
    ki_u = KEYBDINPUT(int(vk), 0, KEYEVENTF_KEYUP, 0, None)
    i1 = INPUT(type=1); i1.ki = ki_d
    i2 = INPUT(type=1); i2.ki = ki_u
    _send(i1, i2)


def tap_key(vk, scan=True):
    return tap_scan(vk) if scan else tap_vk(vk)


def click_fast(x, y, button='left'):
    """高频点击：SetCursorPos + down/up 打包，不留 sleep"""
    user32.SetCursorPos(int(x), int(y))
    down = 0x0002 if button == 'left' else (0x0008 if button == 'right' else 0x0020)
    up = 0x0004 if button == 'left' else (0x0010 if button == 'right' else 0x0040)
    d = INPUT(type=0); d.mi = MOUSEINPUT(0, 0, 0, down, 0, None)
    u = INPUT(type=0); u.mi = MOUSEINPUT(0, 0, 0, up, 0, None)
    _send(d, u)

VK = {'esc':0x1B,'enter':0x0D,'space':0x20,'tab':0x09,'shift':0x10,'ctrl':0x11,
      'alt':0x12,'f':0x46,'e':0x45,'q':0x51,'r':0x52,'t':0x54,'w':0x57,'a':0x41,
      's':0x53,'d':0x44,'z':0x5A,'x':0x58,'c':0x43,'v':0x56,'1':0x31,'2':0x32,
      '3':0x33,'4':0x34,'5':0x35,'6':0x36,'7':0x37,'8':0x38,'9':0x39,'0':0x30}

def vk_of(s):
    s = s.lower()
    return VK.get(s, int(s) if s.isdigit() else 0)

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    op = sys.argv[1]
    a = sys.argv[2:]
    if op == 'move':
        move(int(a[0]), int(a[1]))
    elif op == 'click':
        click(int(a[0]), int(a[1]), a[2] if len(a) > 2 else 'left')
    elif op == 'dclick':
        dclick(int(a[0]), int(a[1]))
    elif op == 'drag':
        drag(int(a[0]), int(a[1]), int(a[2]), int(a[3]), int(a[4]) if len(a) > 4 else 400)
    elif op == 'key':
        key(vk_of(a[0]), int(a[1]) if len(a) > 1 else 60)
    elif op == 'hold':
        key(vk_of(a[0]), int(a[1]))
    else:
        print('unknown op', op)

if __name__ == '__main__':
    main()
