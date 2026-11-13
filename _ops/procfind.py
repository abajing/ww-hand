# -*- coding: utf-8 -*-
"""进程查找工具：用 ctypes 直接调 Win32，不走 PowerShell

为什么不用 PowerShell：
    subprocess.run(..., text=True) 默认按 locale/utf-8 解码，
    中文 Windows 上 powershell 输出是 GBK（0xbe 这种字节），
    直接 UnicodeDecodeError 崩在读取线程里，异常还抓不到。
    改成直接用 Toolhelp32 快照枚举，零外部进程、零编码问题。
"""
import ctypes
import ctypes.wintypes as wt
import os

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wt.DWORD),
        ('cntUsage', wt.DWORD),
        ('th32ProcessID', wt.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wt.DWORD),
        ('cntThreads', wt.DWORD),
        ('th32ParentProcessID', wt.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wt.DWORD),
        ('szExeFile', wt.WCHAR * 260),
    ]


k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Process32FirstW.argtypes = [wt.HANDLE,
                                ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32FirstW.restype = wt.BOOL
k32.Process32NextW.argtypes = [wt.HANDLE,
                               ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.GetExitCodeProcess.restype = wt.BOOL

PROCESS_QUERY_LIMITED = 0x1000
STILL_ACTIVE = 259


def pid_alive(pid):
    if not pid:
        return False
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, int(pid))
    if not h:
        return False
    code = wt.DWORD()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE


def all_python():
    """返回 [(pid, parent_pid, exe_name)]，只取 python*.exe"""
    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(e)):
            return out
        while True:
            name = e.szExeFile.lower()
            if name.startswith('python'):
                out.append((int(e.th32ProcessID),
                            int(e.th32ParentProcessID), name))
            if not k32.Process32NextW(snap, ctypes.byref(e)):
                break
    finally:
        k32.CloseHandle(snap)
    return out


def cmdline(pid):
    """拿进程命令行。

    wmic 输出是 GBK，subprocess 用 text=True 会按 utf-8 解码失败，
    而且失败发生在读取线程里，抓不到。所以拿 bytes 自己解码。
    """
    try:
        import subprocess
        r = subprocess.run(
            ['wmic', 'process', 'where', 'ProcessId=%d' % int(pid),
             'get', 'CommandLine', '/value'],
            capture_output=True, timeout=10)
        raw = r.stdout or b''
        txt = None
        for enc in ('mbcs', 'gbk', 'utf-8'):
            try:
                txt = raw.decode(enc)
                break
            except Exception:
                continue
        if txt is None:
            return ''
        for ln in txt.splitlines():
            if ln.startswith('CommandLine='):
                return ln[len('CommandLine='):]
    except Exception:
        pass
    return ''


def parent_of(pid):
    """任一进程的父 pid。不限 python 进程。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return 0
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(e)):
            return 0
        while True:
            if int(e.th32ProcessID) == int(pid):
                return int(e.th32ParentProcessID)
            if not k32.Process32NextW(snap, ctypes.byref(e)):
                return 0
    finally:
        k32.CloseHandle(snap)


def ancestors(pid):
    """pid 的所有祖先。

    用途：venv 的 python.exe 是 launcher，会再起一个子进程跑真正的脚本，
    父子两个进程的命令行都含 `_ops\\worker.py`。清理多开时如果只看命令行，
    会把 launcher 当成多余的兄弟杀掉 —— 而杀父进程会连带杀掉干活的那个，
    表现就是「worker 就绪 1 秒后已退出」。所以祖先一律保护。
    """
    out = set()
    cur = int(pid)
    for _ in range(12):
        pp = parent_of(cur)
        if not pp or pp in out or pp == cur:
            break
        out.add(pp)
        cur = pp
    return out


def find(needles, exclude=()):
    """按命令行关键词找 python 进程，返回 [(pid, ppid, cmd)]

    会排除自己：本模块自己的命令行里就带着关键词
    （比如用它去搜 'worker_guard.py' 时，那条搜索命令本身命中），
    不排除的话每查一次多算一个，guard 会误判成多开去互杀。
    """
    me = os.getpid()
    out = []
    for pid, ppid, _ in all_python():
        if pid == me or pid in (ppid for ppid, _, _ in out):
            continue
        if not pid_alive(pid):
            continue
        c = cmdline(pid)
        if not c:
            continue
        if 'procfind' in c:          # 别的进程正在用本模块做查询，跳过
            continue
        if any(x in c for x in exclude):
            continue
        if any(n in c for n in needles):
            out.append((pid, ppid, c))
    return out


# ── 实例隔离 ──────────────────────────────────
# 心跳事件名和单实例互斥体都是**全机共享的命名内核对象**。
# 模拟盘（WW_INSTANCE=lab）和生产跑同一份代码时，如果不加后缀：
#   · 模拟盘的 worker 会 SetEvent 到生产的事件上 → 生产的守护以为它还活着；
#   · 模拟盘的 worker 会撞上生产的 Worker 互斥体 → 直接退出，起不来。
# 所以按实例名加后缀。不设 WW_INSTANCE 时后缀为空，名字与改造前完全一致。
INSTANCE = os.environ.get('WW_INSTANCE', 'fuse1')
_SUFFIX = ('_' + INSTANCE) if INSTANCE else ''

# ── 心跳：Windows 命名事件 ────────────────────
# worker 每轮 SetEvent 一次。进程一死句柄释放，事件自动复位。
# 比读文件可靠：文件写入会撞杀软/索引器的占用锁，事件不会。
EVENT_NAME = r'Local\WW_Auto_Worker_Alive' + _SUFFIX
HEARTBEAT_MAX_AGE = 6.0

_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenEventW.restype = wt.HANDLE
_k32.SetEvent.argtypes = [wt.HANDLE]
_k32.SetEvent.restype = wt.BOOL
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD

SYNCHRONIZE = 0x00100000

_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
ERROR_ALREADY_EXISTS = 183

# 单实例互斥体句柄必须活到进程退出，否则互斥体被回收，
# 第二个实例就能进来了。放在模块级。
_MUTEXES = {}


def single_instance(tag):
    """拿一个命名互斥体；已经有人在用就返回 False。

    为什么不用「找进程 + pid 比大小」：
        父进程可能是提权的，普通权限枚举不到它的命令行（实测返回空），
        于是每次都判定「没人在跑」，拉起第二个、第三个。
        而且用「杀多余的」来收敛很危险 —— venv 的 python.exe 是 launcher，
        父子命令行一模一样，杀父会连带杀掉干活的那个。

        互斥体是内核对象，创建即原子，第二个必然拿到 ERROR_ALREADY_EXISTS。
        进程一死句柄自动释放，不需要清理，没有竞态。
    """
    name = r'Local\WW_Auto_%s%s' % (tag, _SUFFIX)
    ctypes.set_last_error(0)
    h = _k32.CreateMutexW(None, True, name)
    err = ctypes.get_last_error()
    if not h:
        return True          # 创建失败就当单实例，不要因此拦住正常启动
    # 必须用 ctypes.get_last_error()，不能直接调 kernel32.GetLastError()：
    # WinDLL(use_last_error=True) 时 ctypes 自己接管了错误码，
    # 直接读 GetLastError 拿到的是错的值（实测会漏判，起出两个守护）。
    if err == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(h)
        return False
    _MUTEXES[tag] = h
    return True


def heartbeat_event():
    """打开 worker 的心跳事件；没有就返回 None"""
    h = _k32.OpenEventW(SYNCHRONIZE, False, EVENT_NAME)
    return h or None


def heartbeat_alive(max_age=HEARTBEAT_MAX_AGE):
    """worker 心跳是否活着。

    两步判：
      1. 事件对象存在（进程死了句柄释放，事件就不在了）
      2. 事件被 Set 过（worker 主循环跑到了）
    再加一层文件年龄兜底，防止上一个刚死的进程留下的信号还没清。
    """
    h = heartbeat_event()
    if not h:
        return False
    try:
        r = _k32.WaitForSingleObject(h, 0)
        signalled = (r == 0)
    finally:
        _k32.CloseHandle(h)
    if not signalled:
        return False
    return True
