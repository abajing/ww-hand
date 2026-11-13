# -*- coding: utf-8 -*-
"""textin.py —— 往窗口里「打字」

为什么单独搞一个
----------------
worker 原来只能发单个按键（key / pkey / keytap），
要输一段中文或者一个搜索词，调用方得自己拆成 VK —— 中文根本拆不出来，
因为中文没有对应的 VK。之前只能靠 ADB 广播之类的旁路去写文字。

这里补两条路：

1. **post_text(hwnd, 文本)** —— WM_CHAR 逐字投递
   优点：不需要焦点、不抢前台、管理员进程能穿 UIPI 打进提权窗口
   缺点：只对走窗口消息的程序有效。Chromium / Electron 的
         Chrome_RenderWidgetHostHWND **不吃 WM_CHAR**（实测投进去无反应），
         这类要用 UIA 的 ValuePattern（走 uia_x 的 set）。

2. **send_text(文本)** —— SendInput 走 Unicode 注入（KEYEVENTF_UNICODE）
   优点：真实输入，任何拿到焦点的程序都吃，中文也行
   缺点：**要求目标在前台**（SendInput 是全局输入队列）
   适合：浏览器里往输入框打字 —— 先 UIA 聚焦，再 send_text。

实测结论（抖音 Chromium 窗口）
------------------------------
  act_live(role='edit', op='set', text=...)  → 成功，读回一致  ← 首选
  post_text → 无反应（Chromium 不吃 WM_CHAR）
  所以：**能走 UIA ValuePattern 就走 UIA**，post_text 留给 Win32 老程序。

用法
----
    import textin
    textin.post_text(hwnd, '你好世界')      # 消息投递，不需焦点
    textin.send_text('你好世界')            # 真实输入，需前台
"""
import ctypes
import ctypes.wintypes as wt
import time

_u = ctypes.windll.user32
_u.SetProcessDPIAware()

WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_PASTE = 0x0302
WM_SETTEXT = 0x000C

_u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_u.PostMessageW.restype = wt.BOOL
_u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_u.SendMessageW.restype = wt.LPARAM
_u.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
_u.MapVirtualKeyW.restype = wt.UINT

# SendInput 的结构体（和 tap.py 一致，但这里要 Unicode 模式）
_PUL = ctypes.POINTER(ctypes.c_ulong)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long),
                ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', _PUL)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', _PUL)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort),
                ('wParamH', ctypes.c_ushort)]


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [('mi', _MOUSEINPUT), ('ki', _KEYBDINPUT),
                    ('hi', _HARDWAREINPUT)]
    _anonymous_ = ('i',)
    _fields_ = [('type', ctypes.c_ulong), ('i', _I)]


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# 把系统时钟粒度调到 1ms。不调的话任何 sleep(0.005) 实际睡 15.6ms，
# 逐字发送会慢到没法用（tap.py 里也有这一手）。
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass


def _signed32(x):
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def post_text(hwnd, text, delay=0.012, vk_enter=False, auto_child=True):
    """用 WM_CHAR 往窗口投一段文字（含中文）。

    返回投出去的字数。

    auto_child=True 时会先在窗口里找编辑子控件（Edit / RichEdit*），
    找到就投给它 —— 直接投顶层窗口是没用的，子控件收不到。

    Chromium / Electron 不吃 WM_CHAR，投了也没反应，那种窗口用 uia_x 的 set。
    """
    if not text:
        return 0
    target = int(hwnd)
    child, child_cls = (find_edit_child(hwnd) if auto_child else (None, ''))
    if child:
        target = int(child)
    n = 0
    for ch in str(text):
        r = _u.PostMessageW(target, WM_CHAR, ord(ch), 1)
        if r:
            n += 1
        if delay:
            time.sleep(delay)
    if vk_enter:
        scan = _u.MapVirtualKeyW(0x0D, 0)
        _u.PostMessageW(target, WM_KEYDOWN, 0x0D,
                        _signed32(1 | (scan << 16)))
        time.sleep(0.04)
        _u.PostMessageW(target, WM_KEYUP, 0x0D,
                        _signed32(1 | (scan << 16) | (1 << 30) | (1 << 31)))
    return n


def set_text(hwnd, text):
    """WM_SETTEXT 直接设控件文本。

    只对标准 Win32 控件（Edit、ComboBox 的编辑框）有效，
    而且必须是那个控件自己的 HWND，不是父窗口。
    """
    r = _u.SendMessageW(int(hwnd), WM_SETTEXT, 0,
                        ctypes.c_wchar_p(str(text)))
    return {'ok': bool(r) or r == 0, 'ret': int(r)}


def send_text(text, delay=0.006, unicode_mode=True):
    """SendInput 打一串字，**要求目标已在前台**。

    unicode_mode=True 走 KEYEVENTF_UNICODE：wVk 填 0、wScan 填字符码，
    绕开键盘布局，中文/emoji 都能打。
    unicode_mode=False 走扫描码（只适合 ASCII 且要按键行为真实时）。
    """
    if not text:
        return 0
    n = 0
    for ch in str(text):
        if unicode_mode:
            code = ord(ch)
            down = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
            up = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                             0, None)
        else:
            scan = _u.MapVirtualKeyW(ord(ch), 0)
            down = _KEYBDINPUT(0, scan, 0x0008, 0, None)
            up = _KEYBDINPUT(0, scan, 0x0008 | KEYEVENTF_KEYUP, 0, None)
        arr = (_INPUT * 2)()
        arr[0].type = 1
        arr[0].ki = down
        arr[1].type = 1
        arr[1].ki = up
        if _u.SendInput(2, arr, ctypes.sizeof(_INPUT)):
            n += 1
        if delay:
            time.sleep(delay)
    return n


def paste(hwnd=None):
    """发 Ctrl+V。配合先往剪贴板写内容，是往 Chromium 输长文本最稳的一招。"""
    if hwnd:
        _u.PostMessageW(int(hwnd), WM_PASTE, 0, 0)
        return {'ok': True, 'via': 'WM_PASTE'}
    vk_ctrl, vk_v = 0x11, 0x56
    arr = (_INPUT * 4)()
    for i, (vk, up) in enumerate(((vk_ctrl, False), (vk_v, False),
                                  (vk_v, True), (vk_ctrl, True))):
        scan = _u.MapVirtualKeyW(vk, 0)
        ki = _KEYBDINPUT(0, scan, 0x0008 | (KEYEVENTF_KEYUP if up else 0),
                         0, None)
        arr[i].type = 1
        arr[i].ki = ki
    _u.SendInput(4, arr, ctypes.sizeof(_INPUT))
    return {'ok': True, 'via': 'SendInput'}


# 真正吃 WM_CHAR 的编辑控件类名（Win32 原生）。
# Win11 的记事本已经换成 WinUI，编辑区是 RichEditD2DPT —— 消息投到**顶层窗口**
# 是没用的，必须投到这个子控件上。实测：往 Notepad 顶层投 8 个字，
# post_text 报成功 8，读回是空字符串。
EDIT_CLASSES = ('Edit', 'RichEdit20W', 'RichEdit20A', 'RichEdit50W',
                'RICHEDIT50W', 'RichEditD2DPT', 'RichEditD2DPC',
                'RICHEDIT60W', 'TextEditor')

_u.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
_u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_u.IsWindowVisible.argtypes = [wt.HWND]


def find_edit_child(hwnd):
    """在窗口里找真正接收文字的编辑控件。

    为什么必须找：WM_CHAR 是**投给某个具体窗口**的，投到顶层父窗口上
    子控件收不到。Win32 的 Edit、Win11 记事本的 RichEditD2DPT 都在子窗口里。

    返回 (child_hwnd, cls)；找不到返回 (None, '')。
    """
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(h, lp):
        b = ctypes.create_unicode_buffer(256)
        _u.GetClassNameW(h, b, 256)
        if b.value in EDIT_CLASSES and _u.IsWindowVisible(h):
            found.append((h, b.value))
        return True

    _u.EnumChildWindows(int(hwnd), _cb, 0)
    if found:
        return found[0]
    return (None, '')


def can_accept_wm_char(hwnd):
    """这个窗口吃不吃 WM_CHAR —— 靠**有没有可投递的编辑子控件**判断。

    以前这里只看类名白名单，结果对着 Win11 记事本说「WM_CHAR 可投递: True」，
    实际上投进去是空的（投到了顶层窗口）。改成看子控件，才是可验证的判据。
    """
    b = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(int(hwnd), b, 256)
    cls = b.value
    child, child_cls = find_edit_child(hwnd)
    # Chromium/Electron 的渲染层不吃 WM_CHAR，即使找到了子控件也别信
    chromium = ('Chrome' in cls or 'Electron' in cls or 'WidgetWin' in cls)
    accepts = bool(child) and not chromium
    return {'hwnd': int(hwnd), 'cls': cls,
            'edit_child': child, 'edit_child_cls': child_cls,
            'accepts': accepts,
            'suggestion': 'WM_CHAR' if accepts else 'uia_set_or_send_text'}
