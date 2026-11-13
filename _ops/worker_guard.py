# -*- coding: utf-8 -*-
"""worker 守护：worker 死了就自动重启，心跳永远新鲜

为什么需要
----------
worker.py 里的抓帧走 dxcam（DXGI）。DXGI 的对象在解释器退出时
会 access violation，进程直接没，不留堆栈。表现是：
    心跳文件还留着旧值，但进程没了
启动器只看心跳文件，就会误判成「已在跑」，跳过重启，
然后 ww_run 连不上，报「worker 没在跑」。

这个守护盯两件事：
  1. worker 进程在不在（按 pid 核实，不看文件）
  2. 心跳新不新鲜

任一不满足就拉起新的。守护自己也是管理员常驻进程。

用法
----
    python worker_guard.py            前台跑，Ctrl+C 停
    python worker_guard.py --detach   后台跑（推荐，跟着 launcher 起）
"""
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import subprocess
import sys
import time

# 根目录可被 WW_HOME 改：模拟盘与生产跑同一份代码（不设就是原路径）。
ROOT = os.environ.get('WW_HOME', r'D:\WW_HAND_fuse_v1')
OPS = os.path.join(ROOT, '_ops')
IPC = os.path.join(ROOT, '_ipc')
LOG = os.path.join(ROOT, '_log')
ALIVE = os.path.join(IPC, 'worker.alive')
GUARD_LOG = os.path.join(LOG, 'guard.log')
# 用 pythonw 而不是 python。
# python.exe 是控制台程序，即使给了 CREATE_NO_WINDOW，venv 的 launcher
# 那一层照样会分配一个控制台窗口，于是桌面上出现两个黑框
# （用户反复说"怎么又黑窗"就是这个）。pythonw.exe 是 GUI 子系统，
# 根本不会分配控制台，从根上不会再闪。
_PYDIR = os.path.dirname(os.path.abspath(sys.executable))
_PYW = os.path.join(_PYDIR, 'pythonw.exe')
PY = _PYW if os.path.isfile(_PYW) else sys.executable

k32 = ctypes.windll.kernel32

for d in (IPC, LOG, os.path.join(ROOT, '_shots')):
    os.makedirs(d, exist_ok=True)


def log(msg):
    line = '%s %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        with open(GUARD_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    # pythonw 下 sys.stdout 是 None，直接 print 会 AttributeError。
    try:
        if sys.stdout:
            print(line, flush=True)
    except Exception:
        pass


def pid_alive(pid):
    if not pid:
        return False
    try:
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259
    except Exception:
        return False


def heartbeat_pid():
    try:
        with open(ALIVE, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return 0


def hb_age():
    try:
        return time.time() - os.path.getmtime(ALIVE)
    except Exception:
        return 9999.0


def hb_alive(max_age=6.0):
    """心跳是否活着。

    判据是 Windows 命名事件（worker 每轮 SetEvent），不是文件时间。
    文件会撞杀软占用锁，事件不会。文件年龄仅作辅助。
    """
    sys.path.insert(0, OPS)
    import procfind
    if procfind.heartbeat_alive(max_age):
        return True
    # 事件不在时退回文件判断（兼容旧版本 worker 或事件创建失败）
    h = heartbeat_pid()
    return bool(h) and pid_alive(h) and hb_age() < max_age


def find_any_worker():
    """按命令行找活着的 worker.py，返回 [(pid, ppid)]。

    用 procfind（Toolhelp + wmic，全程自己处理编码）。
    以前用 subprocess + PowerShell + text=True，中文 Windows 上
    PowerShell 输出是 GBK，解码会在读取线程里抛 UnicodeDecodeError，
    异常抓不到，直接表现为「找到 0 个 worker」。
    """
    sys.path.insert(0, OPS)
    import procfind
    # 用完整路径做 needle：片段 '_ops\worker.py' 会同时命中
    # 生产与模拟盘两套 worker，守护就会去「管理」别人的进程。
    return [(p, pp) for p, pp, _ in procfind.find(
        [os.path.join(OPS, 'worker.py')],
        exclude=('worker_guard', 'worker_channel'))]


def start_worker():
    for f in glob.glob(os.path.join(IPC, 'cmd_*.json')):
        try:
            os.remove(f)
        except Exception:
            pass
    try:
        os.remove(ALIVE)
    except Exception:
        pass
    wlog = open(os.path.join(LOG, 'worker_boot.log'), 'a', encoding='utf-8')
    wlog.write('\n--- guard 拉起 %s ---\n' % time.strftime('%H:%M:%S'))
    wlog.flush()
    p = subprocess.Popen([PY, os.path.join(OPS, 'worker.py')],
                         cwd=OPS, stdout=wlog, stderr=subprocess.STDOUT,
                         creationflags=0x08000008)
    log('拉起 worker 外壳 pid=%d' % p.pid)
    # 0x08000008 = CREATE_NO_WINDOW | DETACH_PROCESS
    # 以前只有 DETACH_PROCESS(0x8)，会继承父进程的控制台 →
    # 每次拉起都闪一个黑窗口，用户看到就是"怎么还在一直启动"。
    # 判就绪只看心跳事件：不能拿 p.pid 比，venv 的 python.exe 是
    # launcher，真正跑脚本的是它起的子进程，pid 永远对不上。
    t0 = time.time()
    while time.time() - t0 < 20:
        time.sleep(0.2)
        h = heartbeat_pid()
        if h and pid_alive(h) and hb_alive():
            log('worker 就绪 pid=%d（%.1fs）' % (h, time.time() - t0))
            return h
    h = heartbeat_pid()
    if h and pid_alive(h):
        return h
    log('worker 起不来（等了 %.0fs），看 worker_boot.log' % (time.time() - t0))
    return 0


LOCK = os.path.join(IPC, 'guard.lock')


def acquire_lock():
    """单实例锁：同时只能有一个 guard 干活。

    以前没有锁，两个 guard 各自跑 ensure()，A 判定「留 X 杀 Y」，
    B 同时判定「留 Y 杀 X」，互相把对方刚拉起的 worker 杀掉，
    日志里就是「留 7292 杀 2808」和「留 2808 杀 7292」并存。
    用 O_EXCL 建文件当锁，句柄一放就释放。
    """
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        # 看看持锁的还活着没有
        try:
            pid = int(open(LOCK).read().strip())
        except Exception:
            pid = 0
        if pid and pid_alive(pid):
            return None
        # 死锁：清掉重来
        try:
            os.remove(LOCK)
        except Exception:
            pass
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except Exception:
            return None
    except Exception:
        return None


def release_lock(fd):
    try:
        if fd is not None:
            os.close(fd)
    except Exception:
        pass
    try:
        os.remove(LOCK)
    except Exception:
        pass


_GC_EVERY = 30.0
_last_gc = 0.0


def _gc(keep):
    """低频清理多余的 worker。

    两条保护，都是踩过坑才加的：
      1. 祖先一律不杀 —— venv 的 python.exe 是 launcher，父子命令行一样，
         杀父会连带杀掉真正干活的子进程。
      2. 30 秒最多一轮 —— 枚举要调 wmic，一次 1~2 秒，不能每 3 秒做一遍。
    """
    global _last_gc
    if time.time() - _last_gc < _GC_EVERY:
        return
    _last_gc = time.time()
    try:
        sys.path.insert(0, OPS)
        import procfind
        protect = procfind.ancestors(keep) | {int(keep)}
        others = [p for p, pp in find_any_worker()
                  if p not in protect and pid_alive(p)]
    except Exception:
        return
    if not others:
        return
    for p in others:
        try:
            subprocess.run(['taskkill', '/PID', str(p), '/F'],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    log('清掉多余 worker %s（留 %d）' % (others, keep))


def ensure_locked():
    """真干活的部分，调用方必须已经拿到锁

    判活只看一条：心跳事件被 Set 过，且心跳里的 pid 还真活着。
    不再枚举进程表当主判据 —— wmic 拿命令行要 1~2 秒，而且对刚起来的
    进程经常返回空（实测），空就被当成「worker 已退出」，于是每轮都
    白拉起一个新的，日志里全是「就绪 0.2s → 已退出」的假警报。
    """
    h = heartbeat_pid()
    if h and pid_alive(h) and hb_alive():
        return h

    if h and pid_alive(h):
        log('worker pid=%d 进程在但心跳停了 %.0fs，判定卡死，重启'
            % (h, hb_age()))
        try:
            subprocess.run(['taskkill', '/PID', str(h), '/F'],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        time.sleep(0.4)
    elif h:
        log('worker pid=%d 已退出，心跳残留 %.0fs' % (h, hb_age()))
    return start_worker()


def ensure():
    """保证有且只有一个活着的 worker，返回 pid（带单实例锁）"""
    fd = acquire_lock()
    if fd is None:
        # 别的 guard 正在处理，跳过这一轮
        p = heartbeat_pid()
        return p if (p and pid_alive(p) and hb_age() < 8) else 0
    try:
        return ensure_locked()
    finally:
        release_lock(fd)


def main():
    # 单实例：第二个守护直接退出。
    # 用内核互斥体，不用「找进程 pid 比大小」——
    # 提权进程的命令行普通权限枚举不到，会永远判定「没人在跑」。
    try:
        sys.path.insert(0, OPS)
        import procfind
        if not procfind.single_instance('Guard'):
            log('已有守护在跑，本实例退出 pid=%d' % os.getpid())
            return
    except Exception as e:
        log('单实例检查跳过 %r' % (e,))

    log('=== guard 启动 pid=%d admin=%s ==='
        % (os.getpid(),
           bool(ctypes.windll.shell32.IsUserAnAdmin())))
    fails = 0
    while True:
        try:
            p = ensure()
            if p:
                fails = 0
            else:
                fails += 1
                log('第 %d 次没起来' % fails)
                time.sleep(min(30, 2 * fails))
        except Exception as e:
            log('guard 异常 %r' % (e,))
            time.sleep(2)
        time.sleep(3)


if __name__ == '__main__':
    if '--once' in sys.argv:
        log('确保一次: pid=%d' % ensure())
        sys.exit(0)
    main()
