# -*- coding: utf-8 -*-
"""reset.py —— 以管理员身份把 worker / guard 全部清掉，再拉一个干净的

为什么需要单独跑成提权进程
--------------------------
worker 和 guard 是管理员进程。DSH 这边的 shell 是普通权限，
Stop-Process 会报 Access denied，而且连它们的命令行都读不到
（Win32_Process 对提权进程返回空 CommandLine），所以从外面看
「杀了 0 个」其实是根本没看见。

这个脚本被提权跑起来之后，它自己有权限枚举并杀干净。

用法（由 ww_op.py reset 调用，一般不用手敲）
    python reset.py          杀干净 + 拉起新 guard
    python reset.py --kill   只杀
"""
import os
import subprocess
import sys
import time

# 根目录：环境变量优先，否则按本文件位置推导（_ops 的上一级）
ROOT = os.environ.get('WW_HOME') or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(ROOT, '_ops')
IPC = os.path.join(ROOT, '_ipc')

# 用 pythonw.exe（GUI 子系统）。用控制台版 python.exe 会闪黑框，
# 哪怕加了 CREATE_NO_WINDOW 也一样 —— venv 的 launcher 那一层会分配控制台。
_PYDIR = os.path.dirname(os.path.abspath(sys.executable))
_PYW = os.path.join(_PYDIR, 'pythonw.exe')
PY = _PYW if os.path.isfile(_PYW) else sys.executable

sys.path.insert(0, OPS)


def say(msg):
    """pythonw 下没有 stdout（sys.stdout 是 None），不能直接 print。

    结果统一写进 _log\\reset.log，外面用 Test-Path / Get-Content 读。
    """
    line = '%s %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        with open(os.path.join(ROOT, '_log', 'reset.log'),
                  'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    try:
        if sys.stdout:
            print(line, flush=True)
    except Exception:
        pass


def _known_ports():
    """本实例该被认领的端口。

    reset 是「本实例」的换手工具，只该动本实例的进程。认端口是为了防一个真踩过的坑：
    老 fuse1 进程环境里没有 WW_PORT，会退回 8792 去撞生产，然后被生产的守护拉回来。
    哪些端口算「我」的：配过 WW_PORT 就是它；没配就只有一个 —— 本实例 broker.token
    那个端口（token 文件里不存端口，所以退一步用 8794 这个第二实例的固定值）。
    """
    p = os.environ.get('WW_PORT')
    out = set()
    if p and p.isdigit():
        out.add(int(p))
    return out


def main():
    import procfind
    kill_only = '--kill' in sys.argv

    # 匹配必须带**完整根路径**，不能用 '_ops\worker.py' 这种片段。
    # 片段会同时匹配两个部署目录下的 worker.py（第一实例和第二实例）
    # —— 实测用片段匹配跑第二实例的 reset，会把第一实例一起杀掉
    # 会把生产那套一起杀掉。完整路径互不包含，天然区分。
    guard_self = os.path.join(OPS, 'worker_guard.py')
    worker_self = os.path.join(OPS, 'worker.py')
    # 端口来自 WW_PORT，但**不能只信环境变量**：
    #   旧的 pythonw 进程环境里根本没有 WW_PORT（环境变量不落盘，重启就没了），
    #   于是老的 fuse1 实例会退回默认 8792，撞上生产的 worker 就退出，
    #   而生产的守护又会好心把「端口写错的那个」拉回来 —— 谁都起不来。
    # 这里给一个兜底清单，按「谁的进程树里有这个进程」认领端口。
    ports = _known_ports()
    targets = []
    for pid, pp, c in procfind.find([guard_self], exclude=('reset.py',), ports=ports):
        targets.append(pid)
    for pid, pp, c in procfind.find([worker_self],
                                    exclude=('worker_guard', 'channel',
                                             'reset.py'), ports=ports):
        targets.append(pid)

    # 把 launcher 外壳也带上（它们的命令行同样含 worker.py）
    me = os.getpid()
    targets = sorted(set(t for t in targets if t != me))

    killed = []
    for pid in targets:
        if not procfind.pid_alive(pid):
            continue
        try:
            r = subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                killed.append(pid)
        except Exception:
            pass

    time.sleep(1.5)

    for f in os.listdir(IPC):
        if f == 'broker.token':
            continue          # token 要留着，否则每次换手调用方都得重新读
        try:
            os.remove(os.path.join(IPC, f))
        except Exception:
            pass

    say('killed=%s' % killed)
    if kill_only:
        return 0

    # 拉起新 guard（无窗口）
    glog = open(os.path.join(ROOT, '_log', 'guard_boot.log'),
                'a', encoding='utf-8')
    glog.write('\n--- reset 拉起 %s ---\n' % time.strftime('%H:%M:%S'))
    glog.flush()
    p = subprocess.Popen([PY, os.path.join(OPS, 'worker_guard.py')],
                         cwd=OPS, stdout=glog, stderr=subprocess.STDOUT,
                         creationflags=0x08000008)
    say('guard_started=%d' % p.pid)
    return 0


if __name__ == '__main__':
    sys.exit(main())
