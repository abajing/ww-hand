# -*- coding: utf-8 -*-
"""WW Worker —— 常驻管理员进程，接收命令并操作游戏

为什么需要它：ACE 只接受管理员权限进程发出的合成输入，
而 DSH 是普通权限，所以把「手」单独提权出来。

IPC: 文件队列
  D:\\WW_Auto\\_ipc\\cmd_<id>.json   我方写，worker 读
  D:\\WW_Auto\\_ipc\\res_<id>.json   worker 写，我方读

命令格式:
  {"id": 12, "actions": [ {"t":"focus"}, {"t":"key","vk":87,"ms":800},
                          {"t":"click","x":100,"y":200},
                          {"t":"shot","name":"a.png"},
                          {"t":"coord"}, {"t":"wait","ms":500},
                          {"t":"script","code":"..."} ]}
"""
import os, sys, time, json, glob, traceback, ctypes
from ctypes import wintypes

# 根目录 / 端口 / 实例名都可以被环境变量改。这是「同一份代码跑两个实例」的关键：
# 默认实例什么都不设（目录按本文件位置推导，端口 8792）；
# 第二实例设 WW_HOME=<另一个目录> WW_PORT=8794 WW_INSTANCE=fuse1，
# 两套并行、互不干扰，也不用维护两份代码（两份必然走偏）。
ROOT = os.environ.get('WW_HOME') or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(ROOT, '_ops')
IPC = os.path.join(ROOT, '_ipc')
LOG = os.path.join(ROOT, '_log', 'worker.log')
SHOTS = os.path.join(ROOT, '_shots')
INSTANCE = os.environ.get('WW_INSTANCE', '')

# 版本标记。换手（reset）之后靠它确认「跑的是新代码」，
# 不用猜 —— 以前只能看 pid 变没变，pid 变了不代表加载了新文件。
BUILD = 'fuse-2026-11-14c'

sys.path.insert(0, OPS)
os.makedirs(IPC, exist_ok=True)
os.makedirs(SHOTS, exist_ok=True)

# 顶层导入 procfind：单实例互斥体、心跳事件名都在它里面。
# 原来只在 main() 的 try 里导入，一旦那步失败，后面用 procfind.EVENT_NAME
# 就会 NameError —— 先模块级导一次，失败置 None，两处都按 None 判断。
try:
    import procfind
except Exception:
    procfind = None

u = ctypes.windll.user32
k = ctypes.windll.kernel32
u.SetProcessDPIAware()

# 所有动作串行化。HTTP 是多线程的，两条指令同时进来会互相打断
# （一个按住 W 期间另一个发点击，动作会错位）。
_ACT_LOCK = None      # 在 start_broker 里初始化


def log(msg):
    line = '%s %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def find_game():
    import shot
    for w in shot.enum_windows():
        if 'Unreal' in w['cls']:
            return w
    return None


def list_windows(visible_only=True, with_rect=True, min_w=60, min_h=40):
    """所有窗口。这是「手」的通用底座，不限游戏。

    尺寸门槛故意比 shot.enum_windows 的默认 300x200 小很多：
    通用场景要能拿到小对话框、工具栏窗口。
    """
    import shot
    out = []
    for w in shot.enum_windows(min_w=min_w, min_h=min_h):
        if visible_only and not w.get('visible', True):
            continue
        out.append(w)
    return out


def resolve_window(a):
    """把动作里的目标描述解析成一个窗口。

    不传任何目标就落到当前前台窗口（2026-11-14 改）。

    为什么不再「默认落到鸣潮」：那是老行为，在"手"变成通用工具之后变成了隐患 ——
    调用方漏写目标时，动作会落到一个**跟它毫无关系**的窗口上。
    实测（2026-11-14 16:5x）：一条不带 hwnd 的 `ptext` 打进了前台在跑的
    英雄联盟/QQ 那一层，而不是报错。
    现在默认取前景窗口，而且 `strict` 的调用方（ptext、uia 写入类 op）会直接报错，
    要求显式指定目标，见 _uia_actions 与 do_action 的 ptext 分支。

    但「手」本身是通用的：任何窗口都能操作，不要求游戏开着。

    认这些字段（优先级从上到下）：
        hwnd    直接给句柄
        title   标题包含（不区分大小写）
        cls     类名包含
        at      [x, y] 屏幕上一点，取那个点下面的窗口
        fg      true = 当前前台窗口
        screen  true = 整个虚拟屏幕（不是窗口，返回伪窗口）
    """
    import shot
    if a.get('screen'):
        g = shot.virtual_screen() if hasattr(shot, 'virtual_screen') else None
        if g:
            return g
        # 兜底：用 GetSystemMetrics 拼整个虚拟桌面
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return {'hwnd': 0, 'title': '<screen>', 'cls': '<screen>',
                'rect': (x, y, x + w, y + h), 'visible': True}

    hwnd = a.get('hwnd')
    if hwnd:
        hwnd = int(hwnd)
        if not u.IsWindow(hwnd):
            return None
        for w in shot.enum_windows():
            if w['hwnd'] == hwnd:
                return w
        # 枚举不到（比如枚举只收可见窗口）就自己拼一个
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        cb = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cb, 256)
        rc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rc))
        return {'hwnd': hwnd, 'title': buf.value, 'cls': cb.value,
                'rect': (rc.left, rc.top, rc.right, rc.bottom),
                'visible': bool(u.IsWindowVisible(hwnd))}

    title = a.get('title')
    if title:
        tl = title.lower()
        for w in list_windows():
            if tl in (w['title'] or '').lower():
                return w
        return None

    cls = a.get('cls')
    if cls:
        cl = cls.lower()
        for w in list_windows():
            if cl in (w['cls'] or '').lower():
                return w
        return None

    at = a.get('at')
    if at:
        h = u.WindowFromPoint(wintypes.POINT(int(at[0]), int(at[1])))
        # 取顶层窗口，不然拿到的是子控件
        GA_ROOT = 2
        h = u.GetAncestor(h, GA_ROOT) or h
        for w in shot.enum_windows():
            if w['hwnd'] == h:
                return w
        return None

    if a.get('fg'):
        h = u.GetForegroundWindow()
        for w in shot.enum_windows():
            if w['hwnd'] == h:
                return w
        return None

    # 什么都没指定 → 鸣潮（老行为）
    return find_game()


def shot_vs():
    """整个虚拟桌面的矩形（多显示器拼起来那块）"""
    import shot
    return shot.virtual_screen()


def win_hint():
    """找不到窗口时给调用方一句能照着做的提示，别只回 no window"""
    n = len(list_windows())
    return ('没指定目标窗口，也没找到鸣潮。当前有 %d 个可见窗口，'
            '可用 hwnd / title / cls / at / fg / screen 指定'
            '（GET /windows 或 ww_op.py windows 列出来）' % n)


def focus(hwnd=None, target=None):
    """把指定窗口提到前台并把键盘焦点给它。target 是动作字典（可选）。"""
    import tap
    g = resolve_window(target or ({'hwnd': hwnd} if hwnd else {}))
    if not g:
        return False
    hwnd = hwnd or g['hwnd']
    l, t, r, b = g['rect']
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        # 用一次真实点击拿焦点
        tap.click(l + (r - l) // 2, t + (b - t) // 2, delay=0.06)
        time.sleep(0.35)
    except Exception as e:
        log('focus err %s' % e)
        return False
    return u.GetForegroundWindow() == hwnd


def _tgt_hwnd(a, fallback_fg=True):
    """给动作定目标窗口。

    有目标字段就按字段解析（hwnd / title / cls / at / fg）。
    什么都没有时：

      · 读值类（fallback_fg=True）落到前台窗口，读错了没有副作用；
      · **写值类（fallback_fg=False）一律报错**，不猜。

    为什么写值类不许猜：2026-11-14 实测过一条不带目标的 `ptext`
    悄悄打进了前台那个跟调用方毫无关系的窗口。UIA 写值、按键、点击
    都是这个道理 —— 目标不明就报错，让调用方把 hwnd 说清楚。
    """
    if a.get('hwnd'):
        return int(a['hwnd']), None
    if a.get('title') or a.get('cls') or a.get('at') or a.get('fg'):
        g = resolve_window(a)
        if not g:
            return None, win_hint()
        return int(g['hwnd']), None
    if fallback_fg:
        h = u.GetForegroundWindow()
        if h:
            return int(h), None
        g = resolve_window(a)
        if not g:
            return None, win_hint()
        return int(g['hwnd']), None
    return None, ('没指定目标窗口。写操作（文本/点按）必须显式给 hwnd / title / '
                  'cls / at / fg 之一，不猜前台 —— 猜错就是往无关窗口里打字。')


def _uia_actions(a, t):
    """控件级定位 / 文本输入 / 输入能力探测的实际实现。

    单独抽出来是为了让 do_action 的动作表保持可读，
    也方便单测直接打这个函数。
    """
    import uia_x, textin

    # 写类动作（会落输入 / 会激活控件）必须显式指定目标；
    # 读类（取值、抓快照、找控件、读 URL）允许落到前台窗口。
    _WRITE_T = ('ptext', 'stext')
    _WRITE_OP = ('set', 'invoke', 'click', 'focus', 'select', 'toggle')
    strict = (t in _WRITE_T or
              (t == 'uia' and a.get('op', 'invoke') in _WRITE_OP) or
              (t == 'uia_act' and a.get('op', 'invoke') in _WRITE_OP))

    hwnd, err = _tgt_hwnd(a, fallback_fg=not strict)
    if err:
        return {'ok': False, 'err': 'no_target', 'msg': err}

    if t == 'uia':
        # 一步到位：现场定位 + 执行。最常用的一条。
        r = uia_x.act_live(
            hwnd, op=a.get('op', 'invoke'),
            name=a.get('name'), role=a.get('role'), aid=a.get('aid'),
            cls=a.get('cls_name'), index=int(a.get('index', 0)),
            regex=bool(a.get('regex')), contains=bool(a.get('contains', True)),
            text=a.get('text'), depth=int(a.get('depth', uia_x.SEARCH_DEPTH)))
        r['hwnd'] = hwnd
        return r

    if t == 'uia_snap':
        r = uia_x.snapshot(hwnd,
                           maxd=int(a.get('depth', uia_x.DEFAULT_DEPTH)),
                           only_interactive=bool(a.get('only_interactive', True)),
                           ttl=float(a.get('ttl', uia_x.DEFAULT_TTL)))
        r['hwnd'] = hwnd
        return r

    if t == 'uia_find':
        r = uia_x.find(a.get('snap') or a.get('sid') or '',
                       name=a.get('name'), role=a.get('role'),
                       aid=a.get('aid'),
                       contains=bool(a.get('contains', True)),
                       index=int(a.get('index', 0)))
        r['hwnd'] = hwnd
        return r

    if t == 'uia_act':
        r = uia_x.act(a.get('snap') or a.get('sid') or '',
                      a.get('ref'), op=a.get('op', 'invoke'),
                      text=a.get('text'), hwnd=hwnd)
        r['hwnd'] = hwnd
        return r

    if t == 'uia_url':
        r = uia_x.active_doc_url(hwnd)
        r['hwnd'] = hwnd
        return r

    if t == 'uia_drop':
        uia_x.drop(a.get('snap') or a.get('sid'))
        return {'ok': True, 'stats': uia_x.stats()}

    if t == 'ptext':
        # WM_CHAR 逐字。只对 Win32 老程序有效，Chromium 不吃。
        n = textin.post_text(hwnd, a.get('text', ''),
                             delay=float(a.get('delay', 0.012)),
                             vk_enter=bool(a.get('enter')))
        return {'ok': True, 'sent': n, 'via': 'WM_CHAR', 'hwnd': hwnd}

    if t == 'stext':
        # SendInput 真实输入，中文也行，**要求目标在前台**。
        if a.get('focus', True):
            focus(target=a)
            time.sleep(0.12)
        n = textin.send_text(a.get('text', ''),
                             delay=float(a.get('delay', 0.006)),
                             unicode_mode=bool(a.get('unicode', True)))
        return {'ok': True, 'sent': n, 'via': 'SendInput',
                'note': 'SendInput 走全局输入队列，必须在前台'}

    if t == 'probe':
        # 输入能力探测：这个窗口吃不吃哪种输入方式
        info = textin.can_accept_wm_char(hwnd)
        # UIA 探测走子进程（uia_x.probe 内部转发），本进程不碰 COM 指针
        probe = uia_x.probe(hwnd) or {}
        probe.pop('ok', None)
        for k in ('root_hwnd', 'root_note', 'uia_ok', 'uia_err'):
            probe.setdefault(k, None)
        snap = uia_x.snapshot(hwnd, maxd=8)
        probe['nodes_d8'] = snap.get('count') if snap.get('ok') else None
        uia_x.drop(snap.get('id') if snap.get('ok') else None)
        cls = info.get('cls') or ''
        if 'Chrome' in cls or 'Electron' in cls or 'WidgetWin' in cls:
            rec = 'uia'          # Chromium/Electron：UIA 语义操作最稳
        elif info['accepts']:
            rec = 'wm_char'      # 原生 Win32：消息投递即可，不用抢前台
        else:
            rec = 'sendinput'    # 其它：只能真实输入，要求前台
        probe['edit_child'] = info.get('edit_child')
        probe['edit_child_cls'] = info.get('edit_child_cls')
        return {'ok': True, 'hwnd': hwnd, 'cls': cls,
                'wm_char_ok': info['accepts'], 'uia': probe,
                'recommend': rec}

    return {'ok': False, 'err': 'unhandled %r' % t}


def do_action(a):
    import shot, ocr, tap
    t = a.get('t')

    # ── 控件级定位（融合自 pywinauto / agent-desktop 的 snapshot-ref 模型）──
    # 和像素坐标的区别：这里拿的是元素名字和语义模式，
    # 界面改版挪位置也不影响；坐标法一动就点空（闲鱼选首图试了四轮）。
    if t in ('uia', 'uia_snap', 'uia_find', 'uia_act', 'uia_url', 'uia_drop',
             'ptext', 'stext', 'probe'):
        return _uia_actions(a, t)

    if t in ('wins', 'windows'):
        # 列窗口。手是通用的，这个动作和游戏无关。
        return {'ok': True, 'windows': list_windows(),
                'count': len(list_windows())}
    if t == 'focus':
        return {'ok': focus(target=a)}
    if t == 'key':
        tap.hold_scan(int(a['vk']), int(a.get('ms', 100)))
        return {'ok': True}
    if t == 'keytap':
        tap.key_scan(int(a['vk']), int(a.get('ms', 60)))
        return {'ok': True}
    if t == 'click':
        cx, cy = int(a['x']), int(a['y'])
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        l, tp = g['rect'][0], g['rect'][1]
        tap.click(l + cx, tp + cy, delay=float(a.get('delay', 0.08)))
        return {'ok': True}
    if t == 'sclick':
        tap.click(int(a['x']), int(a['y']), delay=float(a.get('delay', 0.08)))
        return {'ok': True}
    if t == 'drag':
        tap.drag(int(a['x1']), int(a['y1']), int(a['x2']), int(a['y2']),
                 duration=float(a.get('dur', 0.4)))
        return {'ok': True}
    if t == 'move':
        tap.move(int(a['x']), int(a['y']))
        return {'ok': True}
    if t == 'hold_keys':
        # 多键同时按住，例如 Shift+W 冲刺
        vks = [int(x) for x in a['vks']]
        ms = int(a.get('ms', 1000))
        for v in vks:
            tap.key_down_scan(v)
            time.sleep(0.02)
        time.sleep(ms / 1000.0)
        for v in reversed(vks):
            tap.key_up_scan(v)
            time.sleep(0.02)
        return {'ok': True}
    if t == 'mrel':
        # 相对移动鼠标 = 转镜头
        tap.mouse_rel(int(a['dx']), int(a.get('dy', 0)))
        return {'ok': True}
    if t == 'mrel_steps':
        # 分多步平滑移动，避免游戏把大步长当丢帧
        dx = int(a['dx']); dy = int(a.get('dy', 0)); n = max(1, int(a.get('n', 12)))
        for i in range(n):
            tap.mouse_rel(dx / n, dy / n)
            time.sleep(float(a.get('gap', 0.012)))
        return {'ok': True}
    if t == 'wait':
        time.sleep(int(a.get('ms', 300)) / 1000.0)
        return {'ok': True}
    if t == 'shot':
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        img = shot.grab(g['rect'])
        name = a.get('name', 'w_%d' % int(time.time() * 1000))
        p = os.path.join(SHOTS, name if name.endswith('.png') else name + '.png')
        img.save(p)
        return {'ok': True, 'path': p, 'size': img.size}
    if t == 'coord':
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        img = shot.grab(g['rect'])
        return {'ok': True, 'coord': ocr.read_coord(img)}
    if t == 'raw':
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        import numpy as np
        img = shot.grab(g['rect'])
        return {'ok': True, 'size': img.size}
    if t == 'pkey':
        # 后台投递单键（PostMessage，不抢前台、不需要窗口激活）
        import postinput
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        postinput.post_key(g['hwnd'], int(a['vk']), int(a.get('ms', 80)))
        return {'ok': True}

    if t == 'pkeyhold':
        # 后台按住（期间重发 keydown，UE 靠自动重复判断持续按住）
        import postinput
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        postinput.post_key_hold(g['hwnd'], int(a['vk']), int(a.get('ms', 300)))
        return {'ok': True}

    if t == 'pclick':
        # 后台点击，x/y 传窗口内坐标（与截图一致），自动换算客户区
        import postinput
        g = resolve_window(a)
        if not g:
            return {'ok': False, 'err': win_hint()}
        l, tp = g['rect'][0], g['rect'][1]
        cx, cy = postinput.screen_to_client(g['hwnd'], l + int(a['x']), tp + int(a['y']))
        postinput.post_click(g['hwnd'], cx, cy, float(a.get('delay', 0.08)))
        return {'ok': True, 'client': [cx, cy]}

    if t == 'script':
        ns = {'u': u, 'k': k, 'time': time, 'sys': sys}
        import shot, ocr, tap, postinput
        ns.update({'shot': shot, 'ocr': ocr, 'tap': tap, 'postinput': postinput,
                   'find_game': find_game, 'focus': focus, 'os': os, 'json': json})
        try:
            exec(a['code'], ns)
            return {'ok': True, 'out': repr(ns.get('RESULT'))}
        except Exception as e:
            return {'ok': False, 'err': traceback.format_exc()}
    return {'ok': False, 'err': 'unknown action %r' % t}


def run_actions(actions, stop_on_fail_default=True):
    """执行一串动作，返回结果字典。文件 IPC 和 HTTP 都走这里。

    以前这段逻辑内联在 handle() 里，只有文件队列能调。
    抽出后 HTTP 直接同步返回，不用再等文件轮询——
    文件 IPC 一轮往返 130ms 左右，HTTP 是它的十分之一。
    """
    res = {'results': []}
    try:
        for a in actions:
            r = do_action(a)
            res['results'].append({'t': a.get('t'), 'r': r})
            if not r.get('ok') and a.get('stop_on_fail', stop_on_fail_default):
                res['stopped_at'] = a.get('t')
                break
        res['ok'] = True
    except Exception:
        res['ok'] = False
        res['error'] = traceback.format_exc()
    return res


def handle(path):
    cid = os.path.basename(path)[4:-5]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cmd = json.load(f)
    except Exception:
        m = path + '.bad'
        try:
            os.replace(path, m)
        except Exception:
            pass
        return
    # 立刻删除命令文件，防止 worker 重启后重放（曾导致 os._exit 自杀循环）
    try:
        os.remove(path)
    except Exception:
        pass
    log('recv %s' % cid)
    try:
        with _ACT_LOCK:
            res = run_actions(cmd.get('actions', []))
    except Exception:
        res = {'ok': False, 'error': traceback.format_exc()}
    res['id'] = cmd.get('id', cid)
    tmp = os.path.join(IPC, 'res_%s.json.tmp' % cid)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(IPC, 'res_%s.json' % cid))
    try:
        os.remove(path)
    except Exception:
        pass
    log('done %s ok=%s' % (cid, res.get('ok')))
    return res


PORT = int(os.environ.get('WW_PORT', '8792'))
TOKEN_FILE = os.path.join(IPC, 'broker.token')
_BROKER_TOKEN = ''


def _token():
    global _BROKER_TOKEN
    if not _BROKER_TOKEN:
        try:
            with open(TOKEN_FILE, 'r') as f:
                _BROKER_TOKEN = f.read().strip()
        except Exception:
            import secrets
            _BROKER_TOKEN = secrets.token_hex(16)
            try:
                with open(TOKEN_FILE, 'w') as f:
                    f.write(_BROKER_TOKEN)
            except Exception:
                pass
    return _BROKER_TOKEN


def start_broker():
    """把「手」暴露成 127.0.0.1 上的 HTTP 服务。

    为什么不用文件队列当主通道：
        文件 IPC 一轮要 130ms（写、轮询、读、删），
        而且要自己算 id、防重放、清垃圾。
        HTTP 同步返回，一次往返 <10ms，调用方一行 requests 就完事。
        文件队列保留做兜底（老脚本还在用 ww_run / worker_channel）。

    只绑 127.0.0.1，且要求 token。虽然是本机，但本机也有别的程序，
    不加 token 等于任何进程都能操作游戏。
    """
    global _ACT_LOCK
    if _ACT_LOCK is None:
        import threading
        _ACT_LOCK = threading.Lock()

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class BH(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            # token 走 header 或 query 都行，方便 curl 和浏览器
            q = self.path.split('?', 1)[1] if '?' in self.path else ''
            for part in q.split('&'):
                if part.startswith('token='):
                    if part[6:] == _token():
                        return True
            return self.headers.get('X-Broker-Token', '') == _token()

        def _path(self):
            return self.path.split('?')[0]

        def do_GET(self):
            try:
                self._get()
            except Exception:
                self._json({'ok': False, 'err': traceback.format_exc()}, 500)

        def do_POST(self):
            try:
                self._post()
            except Exception:
                self._json({'ok': False, 'err': traceback.format_exc()}, 500)

        def _get(self):
            p = self._path()
            if p == '/health':
                # 不验 token，给守护和监控探活
                return self._json({'ok': True, 'pid': os.getpid(),
                                   'build': BUILD, 'instance': INSTANCE,
                                   'admin': bool(
                                       ctypes.windll.shell32.IsUserAnAdmin())})
            if not self._authed():
                return self._json({'ok': False, 'err': 'bad token'}, 403)
            if p == '/status':
                g = find_game()
                fh = u.GetForegroundWindow()
                fg = None
                for w in list_windows():
                    if w['hwnd'] == fh:
                        fg = {'hwnd': w['hwnd'], 'title': w.get('title'),
                              'cls': w.get('cls'), 'rect': w['rect']}
                        break
                return self._json({
                    'ok': True,
                    'pid': os.getpid(),
                    'build': BUILD, 'instance': INSTANCE,
                    'admin': bool(ctypes.windll.shell32.IsUserAnAdmin()),
                    'game': ({'hwnd': g['hwnd'], 'title': g.get('title'),
                              'rect': g['rect'], 'cls': g['cls']}
                             if g else None),
                    'fg': fg,
                    'windows': len(list_windows()),
                    'screen': list(shot_vs()['rect']),
                })
            if p == '/windows':
                # 通用能力：列所有可见窗口，手不限于游戏
                try:
                    kw = ''
                    q = self.path.split('?', 1)[1] if '?' in self.path else ''
                    for part in q.split('&'):
                        if part.startswith('kw='):
                            import urllib.parse as _up
                            kw = _up.unquote(part[3:])
                    wins = list_windows()
                    if kw:
                        kl = kw.lower()
                        wins = [w for w in wins
                                if kl in (w.get('title') or '').lower()
                                or kl in (w.get('cls') or '').lower()]
                    return self._json({'ok': True, 'count': len(wins),
                                       'windows': wins})
                except Exception:
                    return self._json({'ok': False,
                                       'err': traceback.format_exc()}, 500)
            if p == '/uia':
                # 控件级查询，curl 就能用：
                #   /uia?hwnd=123&role=button&name=搜索
                #   /uia?hwnd=123&role=edit&op=set&text=你好
                #   /uia?hwnd=123&role=button&name=搜索&op=invoke
                # 不传 hwnd 就用当前前台窗口。
                q = self.path.split('?', 1)[1] if '?' in self.path else ''
                qs = {}
                import urllib.parse as _up
                for part in q.split('&'):
                    if '=' in part:
                        kk, vv = part.split('=', 1)
                        qs[kk] = _up.unquote(vv)
                act = {'t': 'uia'}
                for kk in ('hwnd', 'index', 'depth'):
                    if qs.get(kk):
                        act[kk] = int(qs[kk])
                for kk in ('role', 'name', 'aid', 'cls_name'):
                    if qs.get(kk):
                        act[kk] = qs[kk]
                for kk in ('op', 'text'):
                    if qs.get(kk):
                        act[kk] = qs[kk]
                # 只查不点：给了 name/role 但没给 op，就退化成「查信息」
                if not qs.get('op'):
                    act['op'] = 'get'
                try:
                    with _ACT_LOCK:
                        return self._json(_uia_actions(act, 'uia'))
                except Exception:
                    return self._json({'ok': False,
                                       'err': traceback.format_exc()}, 500)
            if p == '/shot':
                # 支持 ?hwnd= / ?title= / ?screen=1 / ?fg=1，不传就是鸣潮
                q = self.path.split('?', 1)[1] if '?' in self.path else ''
                qs = {}
                for part in q.split('&'):
                    if '=' in part:
                        kk, vv = part.split('=', 1)
                        import urllib.parse as _up
                        qs[kk] = _up.unquote(vv)
                target = {}
                for kk in ('hwnd', 'title', 'cls'):
                    if qs.get(kk):
                        target[kk] = int(qs[kk]) if kk == 'hwnd' else qs[kk]
                if qs.get('screen') in ('1', 'true'):
                    target['screen'] = True
                if qs.get('fg') in ('1', 'true'):
                    target['fg'] = True
                g = resolve_window(target)
                if not g:
                    return self._json({'ok': False, 'err': win_hint()}, 404)
                import shot
                img = shot.grab(g['rect'])
                nm = 'op_%d' % int(time.time() * 1000)
                out = os.path.join(SHOTS, nm + '.png')
                img.save(out)
                return self._json({'ok': True, 'path': out,
                                   'size': list(img.size),
                                   'target': {'hwnd': g['hwnd'],
                                              'title': g.get('title'),
                                              'cls': g.get('cls'),
                                              'rect': g['rect']}})
            return self._json({'ok': False, 'err': 'not found'}, 404)

        def _post(self):
            p = self._path()
            if not self._authed():
                return self._json({'ok': False, 'err': 'bad token'}, 403)
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b'{}'
            try:
                body = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                return self._json({'ok': False, 'err': 'bad json'}, 400)

            if p == '/do':
                acts = body.get('actions') or []
                if not isinstance(acts, list) or not acts:
                    return self._json({'ok': False, 'err': 'no actions'}, 400)
                with _ACT_LOCK:
                    res = run_actions(acts)
                return self._json(res)
            if p == '/script':
                code = body.get('code') or ''
                if not code:
                    return self._json({'ok': False, 'err': 'no code'}, 400)
                with _ACT_LOCK:
                    res = run_actions([{'t': 'script', 'code': code}])
                return self._json(res)
            if p == '/shutdown':
                # 优雅退出。守护会在 3 秒内拉起一个新的。
                # 用来从外部把「手」换掉，而不需要从普通权限的 shell
                # 去 taskkill 一个提权进程（那会 Access denied）。
                self._json({'ok': True, 'bye': os.getpid()})

                def _bye():
                    time.sleep(0.4)
                    log('收到 /shutdown，退出 pid=%s' % os.getpid())
                    os._exit(0)
                import threading as _t
                _t.Thread(target=_bye, daemon=True).start()
                return

            return self._json({'ok': False, 'err': 'not found'}, 404)

    try:
        # 端口必须独占。
        # HTTPServer 默认 allow_reuse_address = 1，在 Windows 上映射成
        # SO_REUSEADDR —— 但 Windows 的 SO_REUSEADDR 语义是「允许两个进程绑同一端口」，
        # 不是 Unix 那种「允许快速重用 TIME_WAIT」。后果：第二实例不报错，
        # 而是悄悄分走第一实例的请求（实测表现成随机 403，因为两份 token 不同）。
        # 独占要显式用 SO_EXCLUSIVEADDRUSE。
        class _ExclusiveServer(ThreadingHTTPServer):
            allow_reuse_address = False

            def server_bind(self):
                if os.name == 'nt':
                    import socket as _s
                    self.socket.setsockopt(
                        _s.SOL_SOCKET, _s.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        srv = _ExclusiveServer(('127.0.0.1', PORT), BH)
    except OSError as e:
        log('broker 起不来 port=%d: %r（端口已被占用，同一端口只允许一个实例）'
            % (PORT, e))
        return False
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log('broker 就绪 http://127.0.0.1:%d  token=%s...' % (PORT, _token()[:6]))
    return True


def main():
    # 单实例：第二个 worker 直接退出。
    # 以前靠守护「杀掉多余的」收敛，但 venv 的 python.exe 是 launcher，
    # 父子命令行一样，杀父会连带杀掉干活的子进程 —— 表现就是
    # 「就绪 1 秒后已退出」。改成互斥体，第二个进来自己走。
    try:
        # 互斥体名字带实例后缀（procfind 里按 WW_INSTANCE 处理），
        # 否则模拟盘会被生产那份挡住，起不来。
        if procfind and not procfind.single_instance('Worker'):
            log('已有 worker 在跑，本实例退出 pid=%s' % os.getpid())
            return
    except Exception as e:
        log('单实例检查跳过 %r' % (e,))

    log('=== worker 启动 pid=%s admin=%s ===' % (os.getpid(), bool(ctypes.windll.shell32.IsUserAnAdmin())))
    hb = os.path.join(IPC, 'worker.alive')
    # 启动时清一次垃圾：上次崩溃留下的 res 没人收，
    # 堆多了会拖慢 glob。cmd 也要清，防止重放旧命令。
    for pat in ('res_*.json', 'cmd_*.json', 'cmd_*.json.tmp', '*.bad'):
        for p in glob.glob(os.path.join(IPC, pat)):
            try:
                os.remove(p)
            except Exception:
                pass

    # 心跳改用 Windows 命名事件：「写入」就是 SetEvent，没有文件句柄，
    # 不会撞「另一个程序正在使用此文件」。
    #
    # 换掉旧做法的原因（真机实测）：os.replace 覆盖 worker.alive 时，
    # 杀软 / 索引器扫过的瞬间会抛 PermissionError，一秒能失败好几次。
    # 文件心跳在这种环境下不可靠，而且读的一方（launcher、通道自检）
    # 还要跟 worker 抢句柄。
    #
    # 事件名固定，读的一方 OpenEvent 就能看；进程一死句柄释放，
    # WaitForSingleObject 立刻超时 —— 死没死天然可判。
    k32 = ctypes.windll.kernel32
    k32.CreateEventW.restype = ctypes.c_void_p
    # 事件名统一从 procfind 取（它已经按 WW_INSTANCE 加了后缀）
    evt_name = (procfind.EVENT_NAME if procfind
                else r'Local\WW_Auto_Worker_Alive')
    evt = k32.CreateEventW(None, True, False,
                           ctypes.c_wchar_p(evt_name))
    if not evt:
        log('事件创建失败 err=%d，退回文件心跳'
            % ctypes.get_last_error())

    # 先把锁建好，再起 broker
    import threading as _th
    globals()['_ACT_LOCK'] = _th.Lock()
    # 端口绑不上就必须退出。以前这里丢掉返回值，进程会继续活着发心跳，
    # 守护以为一切正常 —— 于是得到一个「活着但没在听」的 worker，
    # 既不响应请求，又占着单实例互斥体挡住真正能起来的那个。
    if not start_broker():
        log('broker 未能监听端口，本进程退出（否则守护会误判为健康）')
        os._exit(1)

    beats = 0
    last_clean = time.time()
    pid_s = str(os.getpid())
    hb_fail = 0
    while True:
        if evt:
            k32.SetEvent(ctypes.c_void_p(evt))
        # 文件心跳保留一份，给外部脚本和人工排查看。
        # 写失败不再重试、不再刷日志——事件才是判活的依据。
        try:
            with open(hb, 'w') as f:
                f.write(pid_s)
        except Exception:
            hb_fail += 1
        beats += 1
        files = sorted(glob.glob(os.path.join(IPC, 'cmd_*.json')))
        for p in files:
            try:
                handle(p)
            except Exception:
                log('handle err %s' % traceback.format_exc())
        # 每 60 秒扫一次孤儿 res（发命令那方没来得及收的）
        if time.time() - last_clean > 60:
            last_clean = time.time()
            try:
                for p in glob.glob(os.path.join(IPC, 'res_*.json')):
                    if time.time() - os.path.getmtime(p) > 120:
                        os.remove(p)
            except Exception:
                pass
        time.sleep(0.015)


if __name__ == '__main__':
    main()
