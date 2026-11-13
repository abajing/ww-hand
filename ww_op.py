# -*- coding: utf-8 -*-
"""ww_op.py —— 操作鸣潮的入口（给 AI 和脚本直接用）

它是「手」的遥控器。手是 worker.py（管理员常驻），
这个脚本只负责把话传给手，不需要任何权限。

用法
----
    python ww_op.py status                  # 手在不在、游戏窗口在哪
    python ww_op.py shot                    # 抓一张游戏画面，打印路径
    python ww_op.py key W --ms 700          # 按住 W 700 毫秒
    python ww_op.py tap E                   # 点一下 E
    python ww_op.py tap LMB                 # 点一下左键
    python ww_op.py click 880 500           # 点窗口内坐标(与截图一致)
    python ww_op.py walk W --ms 1200        # 走一段（等同 key）
    python ww_op.py seq "tap TAB;key W 800" # 连招，用分号分隔
    python ww_op.py do '[{"t":"key","vk":87,"ms":700}]'
    python ww_op.py script "print(find_game())"

设计要点
--------
1. 手不在就自己拉起（提权但无窗口），不需要用户点任何东西。
2. 全程不弹黑窗口：起进程用 CREATE_NO_WINDOW，提权用 pythonw + runas。
3. 只连 127.0.0.1，token 从 _ipc\\broker.token 读。
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 根目录 / 端口都可以被环境变量改；不设就按本文件位置推导
# （同一个文件既能当默认实例，也能当第二实例用，不维护两份会走偏的代码）。
ROOT = os.environ.get('WW_HOME') or os.path.dirname(
    os.path.abspath(__file__))
OPS = os.path.join(ROOT, '_ops')
IPC = os.path.join(ROOT, '_ipc')
TOKEN_FILE = os.path.join(IPC, 'broker.token')
PORT = int(os.environ.get('WW_PORT', '8792'))
BASE = 'http://127.0.0.1:%d' % PORT

# 解释器：用当前这个（venv 也行），pythonw 优先以便无窗口启动
_PYDIR = os.path.dirname(os.path.abspath(sys.executable))
_PYW = os.path.join(_PYDIR, 'pythonw.exe')
PY = sys.executable
PYW = _PYW if os.path.isfile(_PYW) else sys.executable

# 键名 → VK。只放游戏真用得上的；要别的直接 --vk。
VK = {
    'W': 87, 'A': 65, 'S': 83, 'D': 68,
    'E': 69, 'Q': 81, 'R': 82, 'T': 84, 'F': 70, 'G': 71,
    'H': 72, 'C': 67, 'V': 86, 'B': 66, 'X': 88, 'Z': 90,
    'SHIFT': 16, 'CTRL': 17, 'ALT': 18, 'SPACE': 32,
    'TAB': 9, 'ESC': 27, 'ENTER': 13,
    '1': 49, '2': 50, '3': 51, '4': 52,
    'LMB': 1, 'RMB': 2, 'MMB': 4,
}


def token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ''


def _req(path, data=None, timeout=60):
    url = BASE + path
    tk = token()
    if tk:
        url += ('&' if '?' in url else '?') + 'token=' + urllib.parse.quote(tk)
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    r = urllib.request.Request(url, data=body, headers=headers,
                               method='POST' if body else 'GET')
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def alive():
    """只看 /health，不需要 token，最轻的探活"""
    try:
        with urllib.request.urlopen(BASE + '/health', timeout=2) as r:
            return json.loads(r.read().decode('utf-8')).get('ok', False)
    except Exception:
        return False


def spawn_hand():
    """手不在 → 提权拉起守护，守护再拉 worker。

    用 pythonw + runas：无窗口，且这台机器 ConsentPromptBehaviorAdmin=0，
    不会弹 UAC。整个过程用户看不到任何东西。
    """
    guard = os.path.join(OPS, 'worker_guard.py')
    if not os.path.isfile(guard):
        return False, 'guard 不存在: %s' % guard
    # 先看有没有 guard 在跑，避免重复提权
    try:
        sys.path.insert(0, OPS)
        import procfind
        # 只认本实例的守护（完整路径），否则模拟盘会误判生产守护为已有
        if procfind.find([os.path.join(OPS, 'worker_guard.py')]):
            return True, 'guard 已在跑'
    except Exception:
        pass
    ps = (
        '$ErrorActionPreference="SilentlyContinue";'
        'Start-Process -FilePath "{pyw}" '
        '-ArgumentList \'"{guard}"\' '
        '-Verb RunAs -WindowStyle Hidden'
    ).format(pyw=PYW, guard=guard)
    try:
        subprocess.run(['powershell', '-NoProfile', '-WindowStyle', 'Hidden',
                        '-Command', ps],
                       capture_output=True, timeout=30,
                       creationflags=0x08000000)
    except Exception as e:
        return False, '拉起失败 %r' % (e,)
    return True, '已请求拉起'


def ensure(timeout=45):
    """确保手可用。返回 (ok, 说明)"""
    if alive():
        return True, '手在线'
    ok, why = spawn_hand()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if alive():
            return True, '拉起成功（%.1fs）' % (time.time() - t0)
        time.sleep(0.4)
    return False, '等 %.0f 秒没起来：%s' % (timeout, why)


def vk_of(name):
    u = name.upper()
    if u in VK:
        return VK[u]
    if u.isdigit():
        return int(u) + 48
    if u.startswith('0X'):
        return int(u, 16)
    raise SystemExit('不认识的键: %s（可用: %s）'
                     % (name, ' '.join(sorted(VK))))


def mk_actions(tokens):
    """把 "tap TAB; key W 800; click 100 200" 变成 actions 列表"""
    acts = []
    for raw in tokens:
        for seg in raw.split(';'):
            seg = seg.strip()
            if not seg:
                continue
            parts = seg.split()
            t = parts[0].lower()
            if t == 'tap':
                acts.append({'t': 'pkey', 'vk': vk_of(parts[1]),
                             'ms': int(parts[2]) if len(parts) > 2 else 60})
            elif t == 'key':
                acts.append({'t': 'pkeyhold', 'vk': vk_of(parts[1]),
                             'ms': int(parts[2]) if len(parts) > 2 else 500})
            elif t == 'click':
                acts.append({'t': 'pclick', 'x': int(parts[1]),
                             'y': int(parts[2])})
            elif t == 'wait':
                acts.append({'t': 'wait', 'ms': int(parts[1])})
            elif t == 'focus':
                acts.append({'t': 'focus'})
            else:
                raise SystemExit('不认识的段: %r' % seg)
    return acts


def parse_target(args):
    """从命令行尾巴里抠出目标窗口描述，返回 (target, 剩下的参数)

    手是通用的：不指定目标就落到鸣潮，指定了就能操作任何窗口。
        --hwnd 123456      直接给句柄
        --title 记事本      标题包含
        --cls Notepad       类名包含
        --at 800 500        屏幕上一点，取点下面的窗口
        --fg                当前前台窗口
        --screen            整个屏幕
    """
    target = {}
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--hwnd':
            target['hwnd'] = int(args[i + 1]); i += 2
        elif a == '--title':
            target['title'] = args[i + 1]; i += 2
        elif a == '--cls':
            target['cls'] = args[i + 1]; i += 2
        elif a == '--at':
            target['at'] = [int(args[i + 1]), int(args[i + 2])]; i += 3
        elif a == '--fg':
            target['fg'] = True; i += 1
        elif a == '--screen':
            target['screen'] = True; i += 1
        else:
            rest.append(a); i += 1
    return target, rest


def call(actions, quiet=False):
    """发一组动作，返回 worker 的原始结果"""
    ok, why = ensure()
    if not ok:
        print(why, file=sys.stderr)
        return None
    try:
        r = _req('/do', {'actions': actions})
    except Exception as e:
        print('调用失败: %r' % (e,), file=sys.stderr)
        return None
    if not quiet:
        if r.get('ok'):
            print('ok  %d 个动作%s'
                  % (len(r.get('results', [])),
                     '' if not r.get('stopped_at')
                     else '（停在 %s）' % r['stopped_at']))
            for item in r.get('results', []):
                rr = item.get('r') or {}
                if not rr.get('ok'):
                    print('   失败 %s: %s' % (item.get('t'), rr.get('err')))
        else:
            print('失败: %s' % (r.get('error') or r)[:400], file=sys.stderr)
    return r


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1].lower()
    rest = argv[2:]

    if cmd == 'status':
        ok, why = ensure()
        if not ok:
            print(why)
            return 1
        r = _req('/status')
        g = r.get('game')
        print('手 pid=%s 管理员=%s  可见窗口=%s  屏幕=%s'
              % (r.get('pid'), r.get('admin'), r.get('windows'),
                 r.get('screen')))
        if g:
            print('鸣潮 hwnd=%s 标题=%r 位置=%s'
                  % (g['hwnd'], g['title'], g['rect']))
        else:
            print('鸣潮窗口：没找到（不影响用，手是通用的，能操作任何窗口）')
        fg = r.get('fg')
        if fg:
            print('当前前台 hwnd=%s %r [%s]'
                  % (fg['hwnd'], (fg.get('title') or '')[:40], fg.get('cls')))
        return 0

    if cmd == 'windows':
        # 手的通用能力：列所有可见窗口（不限游戏）
        ok, why = ensure()
        if not ok:
            print(why)
            return 1
        kw = rest[0] if rest else ''
        try:
            r = _req('/windows' + ('?kw=' + urllib.parse.quote(kw) if kw else ''))
        except Exception as e:
            print('失败: %r' % (e,))
            return 1
        wins = r.get('windows') or []
        if not wins:
            print('没有匹配的窗口')
            return 0
        print('%d 个窗口%s' % (len(wins), ('（关键词 %s）' % kw) if kw else ''))
        for w in wins:
            l, t, rr, b = w['rect']
            print('  hwnd=%-9s %-5dx%-5d (%d,%d)  %-28s  %s'
                  % (w['hwnd'], rr - l, b - t, l, t,
                     (w.get('cls') or '')[:28], (w.get('title') or '')[:50]))
        return 0

    if cmd == 'shot':
        ok, why = ensure()
        if not ok:
            print(why)
            return 1
        target, _ = parse_target(rest)
        url = '/shot'
        qs = []
        for kk in ('hwnd', 'title', 'cls'):
            if kk in target:
                qs.append('%s=%s' % (kk, urllib.parse.quote(str(target[kk]))))
        if target.get('fg'):
            qs.append('fg=1')
        if target.get('screen'):
            qs.append('screen=1')
        if qs:
            url += '?' + '&'.join(qs)
        try:
            r = _req(url, timeout=90)
        except Exception as e:
            print('失败: %r' % (e,))
            return 1
        if r.get('ok'):
            tg = r.get('target') or {}
            print(r['path'])
            print('  目标: hwnd=%s %r  尺寸=%s'
                  % (tg.get('hwnd'), tg.get('title'), r.get('size')))
            return 0
        print('失败: %s' % r.get('err'))
        return 1

    if cmd in ('key', 'tap', 'walk', 'click', 'focus'):
        target, rest = parse_target(rest)
        if cmd == 'focus':
            acts = [dict({'t': 'focus'}, **target)]
        elif cmd == 'click':
            acts = [dict({'t': 'pclick', 'x': int(rest[0]),
                          'y': int(rest[1])}, **target)]
        else:
            key = rest[0]
            ms = 60
            if '--ms' in rest:
                ms = int(rest[rest.index('--ms') + 1])
            elif len(rest) > 1 and rest[1].isdigit():
                ms = int(rest[1])
            acts = [dict({'t': 'pkeyhold' if cmd in ('key', 'walk') else 'pkey',
                          'vk': vk_of(key), 'ms': ms}, **target)]
        r = call(acts)
        return 0 if r and r.get('ok') else 1

    if cmd == 'seq':
        target, rest = parse_target(rest)
        acts = mk_actions(rest)
        if target:
            for a in acts:
                a.update(target)
        r = call(acts)
        return 0 if r and r.get('ok') else 1

    if cmd == 'do':
        acts = json.loads(rest[0])
        r = call(acts)
        return 0 if r and r.get('ok') else 1

    if cmd == 'script':
        ok, why = ensure()
        if not ok:
            print(why)
            return 1
        r = _req('/script', {'code': rest[0]})
        print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
        return 0 if r.get('ok') else 1

    if cmd in ('uia', 'uiaclick', 'uiatype', 'uisnap', 'uitype', 'ptype',
               'probe', 'uipath'):
        # ── 控件级定位 / 文本输入 / 能力探测 ───────────────────────
        # 坐标法是「点第 N 像素」，界面一改版就点空。这里是「点名字叫
        # 搜索的按钮」，按钮挪位置也不影响。
        ok, why = ensure()
        if not ok:
            print(why)
            return 1
        target, rest = parse_target(rest)

        def _first(args, flag, default=None):
            if flag in args:
                return args[args.index(flag) + 1]
            return default

        # 带值的开关：这些东西后面那一个 token 是它的值，不是位置参数。
        # 不做这个区分会把 `--role edit` 里的 `edit` 当成控件名字 ——
        # 实测 uiatype --role edit 就因此去找一个叫 edit 的控件。
        _VALUED = ('--name', '--role', '--aid', '--index', '--text',
                   '--op', '--depth', '--ttl', '--snap', '--ref')

        def _positionals(args):
            out = []
            i = 0
            while i < len(args):
                if args[i] in _VALUED:
                    i += 2
                    continue
                if args[i].startswith('--'):
                    i += 1
                    continue
                out.append(args[i])
                i += 1
            return out

        if cmd == 'probe':
            act = dict({'t': 'probe'}, **target)
            r = call([act])
            if r and r.get('ok'):
                res = (r.get('results') or [{}])[0].get('r') or {}
                print('窗口 cls=%s' % res.get('cls'))
                print('  WM_CHAR 可投递: %s' % res.get('wm_char_ok'))
                ec = (res.get('uia') or {}).get('edit_child') or ''
                if ec:
                    print('  编辑子控件:     %s' % ec)
                print('  UIA 可读:       %s (%s)'
                      % (res.get('uia', {}).get('uia_ok'),
                         res.get('uia', {}).get('root_note')))
                print('  8 层节点数:     %s' % res.get('uia', {}).get('nodes_d8'))
                print('  建议输入方式:   %s' % res.get('recommend'))
                return 0
            return 1

        if cmd in ('uitype', 'ptype'):
            text = ' '.join(rest)
            act = dict({'t': 'stext' if cmd == 'uitype' else 'ptext',
                        'text': text}, **target)
            r = call([act])
            if r and r.get('ok'):
                res = (r.get('results') or [{}])[0].get('r') or {}
                print('已输入 %s 字（%s）' % (res.get('sent'), res.get('via')))
                return 0
            return 1

        if cmd == 'uisnap':
            depth = int(_first(rest, '--depth', 14))
            act = dict({'t': 'uia_snap', 'depth': depth}, **target)
            r = call([act])
            if not (r and r.get('ok')):
                return 1
            res = (r.get('results') or [{}])[0].get('r') or {}
            if not res.get('ok'):
                print('快照失败: %s' % res.get('err'))
                return 1
            print('快照 %s  节点 %s  %sms  root=%s'
                  % (res.get('id'), res.get('count'),
                     res.get('elapsed_ms'), res.get('note')))
            _pos = _positionals(rest)
            kw = _pos[0].lower() if _pos else ''
            shown = 0
            for it in res.get('items') or []:
                nm = it.get('name') or ''
                aid = it.get('aid') or ''
                if kw and kw not in nm.lower() and kw not in aid.lower():
                    continue
                print('  %-4s %-18s %-34s %s'
                      % (it['ref'], it['type'], nm[:34],
                         ','.join(it.get('pats') or [])))
                shown += 1
                if shown >= 60:
                    print('  ... 还有更多，加关键词过滤')
                    break
            return 0

        if cmd == 'uipath':
            act = dict({'t': 'uia_url'}, **target)
            r = call([act])
            if r and r.get('ok'):
                res = (r.get('results') or [{}])[0].get('r') or {}
                print(res.get('url'))
                return 0
            return 1

        # uia / uiaclick / uiatype：现场定位 + 执行
        name = _first(rest, '--name')
        role = _first(rest, '--role')
        aid = _first(rest, '--aid')
        index = int(_first(rest, '--index', 0))
        text = _first(rest, '--text')
        op = _first(rest, '--op')
        if not op:
            op = {'uiaclick': 'invoke', 'uiatype': 'set'}.get(cmd, 'get')
        if name is None:
            # 第一个真正的位置参数当名字（跳过 --flag 及其值）
            _pos = _positionals(rest)
            if _pos:
                name = _pos[0]
        if not (name or role or aid):
            print('用法: ww_op.py uia --name 搜索 [--role button] [--op invoke]')
            print('      ww_op.py uiaclick 精选 --role link')
            print('      ww_op.py uiatype --role edit --text 你好')
            return 2
        act = dict({'t': 'uia', 'op': op, 'name': name, 'role': role,
                    'aid': aid, 'index': index}, **target)
        if text is not None:
            act['text'] = text
        r = call([act])
        if not (r and r.get('ok')):
            return 1
        res = (r.get('results') or [{}])[0].get('r') or {}
        if not res.get('ok'):
            print('失败 %s: %s' % (res.get('err'), res.get('msg')))
            return 1
        tg = res.get('target') or {}
        print('%s %s %r' % (res.get('mechanism') or 'ok',
                            tg.get('type'), (tg.get('name') or '')[:40]))
        if res.get('value') is not None:
            print('  值: %r' % res.get('value'))
        if res.get('set') is not None:
            print('  已写入: %r' % res.get('set'))
        if res.get('rect'):
            print('  位置: %s' % (res.get('rect'),))
        return 0
    if cmd == 'up':
        ok, why = ensure()
        print(why)
        return 0 if ok else 1

    if cmd == 'reset':
        # 把手整个换掉。必须提权跑，否则杀不动管理员进程。
        # 不能用 -Wait：reset.py 拉起的新 guard 是 DETACH 的，
        # -Wait 会把整棵进程树一起等，永远不返回（实测 90 秒超时）。
        reset = os.path.join(OPS, 'reset.py')
        ps = (
            '$ErrorActionPreference="SilentlyContinue";'
            'Start-Process -FilePath "{py}" -ArgumentList \'"{reset}"\' '
            '-Verb RunAs -WindowStyle Hidden'
        ).format(py=PY, reset=reset)
        try:
            subprocess.run(['powershell', '-NoProfile', '-WindowStyle',
                            'Hidden', '-Command', ps],
                           capture_output=True, timeout=45,
                           creationflags=0x08000000)
        except Exception:
            pass
        # 顺序很关键：先等它真的死（broker 掉线），再等新的起来。
        # 以前直接轮询 alive()，旧 worker 还活着时立刻返回 True，
        # 结果「换手成功」其实是没换。
        t0 = time.time()
        while time.time() - t0 < 25:
            if not alive():
                break
            time.sleep(0.3)
        while time.time() - t0 < 60:
            if alive():
                print('已换手（%.1fs）' % (time.time() - t0))
                return 0
            time.sleep(0.3)
        print('换手超时')
        return 1

    print('不认识的命令: %s' % cmd)
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
