# -*- coding: utf-8 -*-
"""stop.py —— 紧急停止开关

三条路，任意一条触发就停，谁先来都行：

  1. 系统热键 F12        —— RegisterHotKey 注册，系统直接投递 WM_HOTKEY
  2. 系统热键 Ctrl+Shift+F12 —— 备用，怕 F12 和别的东西撞
  3. 哨兵文件 D:\\WW_Auto\\_state\\STOP —— 双击「紧急停止.bat」创建，
     或者按不到键盘时用

为什么用 RegisterHotKey 而不是轮询 GetAsyncKeyState：
实测这个环境里 GetAsyncKeyState 读不到注入的按键（连 keybd_event 按住 150ms
都读不到），轮询方案没法自测，也就没法保证它真能用。
RegisterHotKey 走的是系统级注册，按下时系统主动投递 WM_HOTKEY，
不依赖键盘状态表的读取权限，可靠得多。

代价：注册了 F12 之后，游戏收不到 F12。鸣潮的 F12 不是操作键，可以接受。

用法：
    from stop import should_stop, arm, clear, reason

    arm()                      # 开跑前清一次
    while True:
        if should_stop():
            break
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

# 根目录：环境变量优先，否则按本文件位置推导（_ops 的上一级）
ROOT = os.environ.get('WW_HOME') or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, '_state')
STOP_FILE = os.path.join(STATE_DIR, 'STOP')

VK_F12 = 0x7B
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_uint]
user32.PeekMessageW.restype = wt.BOOL

_lock = threading.Lock()
_triggered = False
_reason = ''
_thread = None
_registered = []
_hotkey_ids = {9101: 'F12', 9102: 'Ctrl+Shift+F12'}


class Stopped(Exception):
    """被紧急停止叫停"""


def file_exists():
    return os.path.exists(STOP_FILE)


def _drop_sentinel(why):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STOP_FILE, 'w', encoding='utf-8') as f:
            f.write('%s at %s' % (why, time.strftime('%Y-%m-%d %H:%M:%S')))
    except Exception:
        pass


def _fire(why):
    global _triggered, _reason
    with _lock:
        if not _triggered:
            _triggered = True
            _reason = why
            _drop_sentinel(why)


def _pump():
    """后台线程：注册热键 + 捡 WM_HOTKEY。

    注册和 PeekMessage 必须在同一个线程 —— 热键消息只投递到注册它的
    那个线程的消息队列，跨线程注册等于永远收不到。
    """
    global _registered
    _registered = []
    for hid, name in list(_hotkey_ids.items()):
        vk, mods = (VK_F12, MOD_NOREPEAT) if hid == 9101 else \
                   (VK_F12, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT)
        if user32.RegisterHotKey(None, hid, mods, vk):
            _registered.append(name)

    msg = wt.MSG()
    while True:
        got = user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, 1)
        if got:
            name = _hotkey_ids.get(int(msg.wParam), 'hotkey')
            _fire('%s 热键' % name)
        time.sleep(0.02)


def _ensure_thread():
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_pump, daemon=True, name='stop-hotkey')
    _thread.start()


def install(wait=1.0):
    """起热键线程并等它注册完。返回注册成功的名字列表。"""
    _ensure_thread()
    t0 = time.time()
    while time.time() - t0 < wait and not _registered:
        time.sleep(0.02)
    return list(_registered)


def hotkey_down():
    """兼容旧接口：现在热键是异步投递的，这里直接看标志。"""
    return _triggered


def should_stop():
    """每轮调它。一旦 True 就一直 True，直到 clear()。"""
    if _triggered:
        return True
    if file_exists():
        _fire('STOP 文件')
        return True
    return False


def reason():
    return _reason


def stop():
    """程序主动叫停自己。"""
    _fire('程序主动')


def clear():
    """恢复：清标志、删哨兵文件。"""
    global _triggered, _reason
    with _lock:
        _triggered = False
        _reason = ''
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except Exception:
        pass


def arm():
    """开跑前清一次遗留状态。"""
    clear()


def guard(fn):
    """装饰器：深调用栈里没空查 should_stop 的，靠它抛异常弹出来。"""
    def wrapper(*a, **kw):
        if should_stop():
            raise Stopped('紧急停止：%s' % (reason() or '未知'))
        return fn(*a, **kw)
    wrapper.__name__ = getattr(fn, '__name__', 'wrapper')
    wrapper.__doc__ = getattr(fn, '__doc__', None)
    return wrapper


if __name__ == '__main__':
    reg = install()
    print('已注册热键:', reg if reg else '（无，可能被占用）')
    print('哨兵文件:', STOP_FILE)
    print('监控 60 秒，按 F12 试试，或者手动创建那个文件')
    arm()
    t0 = time.time()
    while time.time() - t0 < 60:
        if should_stop():
            print('已停止，原因：%s' % reason())
            break
        time.sleep(0.05)
    else:
        print('60 秒内没触发')
