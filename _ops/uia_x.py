# -*- coding: utf-8 -*-
"""uia_x.py —— 控件级定位（UI Automation）

融合思路来自 pywinauto / vercel-labs/agent-desktop：
不靠「猜坐标」，靠元素的名字和语义模式。按钮挪位置也不影响。

两条路径，按场景选
------------------
1. **locate() + act_live()**  原生 UIA 搜索，每次现场查找
   优点：12~64ms，不受页面变化影响，深度不受限
   缺点：每次都要重新找
   → 单次操作、目标名字明确时用这个。**默认推荐。**

2. **snapshot() + find() + act()**  agent-desktop 那套快照模型
   优点：抓一次，连续操作多个元素
   缺点：抓取代价大（深度 12 约 145~280ms），页面一变就失效
   → 要连续点同一屏里好几个控件时用。

三个实测出来的坑（不是推测）
----------------------------
1. **Chromium 要打到子窗口**
   顶层 Chrome_WidgetWin_1 走 UIA 只有 13 个节点；
   真渲染层是子窗口 Chrome_RenderWidgetHostHWND，那里有 1700 个。
   locate_root() 自动选这个子窗口。

2. **导航后旧元素全部失效**
   语义点击跳转后，旧控件再访问抛 COMError -2147220991
   （事件无法调用任何子户）。所以快照带 id，换页面必须重抓。
   act() 拿到 stale 会明确报错，不会假装成功。

3. **EditControl 的 Name 不可靠**
   占位文字会变成 Name，一旦有值或页面重绘，Name 就变空。
   实测抖音搜索框：第一次 name='搜索你感兴趣的内容'，
   导航回来后 name='' value=''。
   → 找输入框别只按名字，用 role='Edit' + index，或按 rect 判断。

4. **遍历代价随深度指数上升**（抖音 1473x1315）
   深度  8 →   54 节点,  30ms
   深度 12 →  270 节点, 145ms
   深度 16 → 1063 节点, 518ms
   默认 12。

多线程
------
worker 是 ThreadingHTTPServer，每请求新线程。UIA 走 COM，
**每线程都要 CoInitialize**，否则 0x80010106。
所有对外入口都套 UIAutomationInitializerInThread。

用法
----
    import uia_x
    # 路径一：一次到位
    uia_x.act_live(hwnd, name='精选', op='invoke')
    uia_x.act_live(hwnd, role='Edit', index=0, op='set', text='你好')

    # 路径二：快照
    snap = uia_x.snapshot(hwnd)
    hit = uia_x.find(snap['id'], name='精选')
    uia_x.act(snap['id'], hit['item']['ref'], 'invoke')
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time
import uuid

import uiautomation as auto

# ---------------------------------------------------------------- 常量

DEFAULT_TTL = 30.0
# 14 是量出来的，不是拍的。实测抖音首页关键控件深度：
#   导航链接(精选/我的) d10 · 搜索按钮 d12 · 搜索输入框 d13 · 最深 15
# 深度 12 会正好漏掉搜索输入框（之前 find(role='edit') 返回 not_found 就是这个）。
# 代价：深度 12→270 节点 145ms，14→约 500 节点 280ms，16→1063 节点 518ms。
DEFAULT_DEPTH = 14
SEARCH_DEPTH = 30          # 原生搜索的默认深度，够覆盖大部分页面

RENDER_CLASSES = ('Chrome_RenderWidgetHostHWND',)

INTERACTIVE_TYPES = frozenset((
    'ButtonControl', 'HyperlinkControl', 'EditControl', 'CheckBoxControl',
    'RadioButtonControl', 'ComboBoxControl', 'ListItemControl',
    'MenuItemControl', 'TabItemControl', 'SplitButtonControl',
    'TreeItemControl', 'SliderControl',
))

# 类型简写 → uiautomation 的类
_TYPE_MAP = {
    'button': 'ButtonControl', 'link': 'HyperlinkControl',
    'hyperlink': 'HyperlinkControl', 'edit': 'EditControl',
    'textfield': 'EditControl', 'checkbox': 'CheckBoxControl',
    'radio': 'RadioButtonControl', 'combobox': 'ComboBoxControl',
    'list': 'ListControl', 'listitem': 'ListItemControl',
    'menu': 'MenuControl', 'menuitem': 'MenuItemControl',
    'tab': 'TabControl', 'tabitem': 'TabItemControl',
    'text': 'TextControl', 'document': 'DocumentControl',
    'pane': 'PaneControl', 'group': 'GroupControl',
    'window': 'WindowControl', 'tree': 'TreeControl',
    'treeitem': 'TreeItemControl', 'slider': 'SliderControl',
    'toolbar': 'ToolBarControl', 'image': 'ImageControl',
    'calendar': 'CalendarControl', 'table': 'TableControl',
}

_u = ctypes.windll.user32
_u.SetProcessDPIAware()

# 必须声明 argtypes：x64 下 HWND 是 64 位，不声明按 c_int 传，
# 高位句柄抛 OverflowError: int too long to convert。
_u.GetWindowTextLengthW.argtypes = [wt.HWND]
_u.GetWindowTextLengthW.restype = ctypes.c_int
_u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_u.GetWindowTextW.restype = ctypes.c_int
_u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_u.GetClassNameW.restype = ctypes.c_int
_u.IsWindowVisible.argtypes = [wt.HWND]
_u.IsWindowVisible.restype = wt.BOOL
_u.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
_u.EnumWindows.restype = wt.BOOL
_u.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
_u.EnumChildWindows.restype = wt.BOOL
_u.IsWindow.argtypes = [wt.HWND]
_u.IsWindow.restype = wt.BOOL
_u.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
_u.ScreenToClient.restype = wt.BOOL

_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

_snaps = {}
_snap_lock = threading.Lock()
_MAX_SNAPS = 16


# ---------------------------------------------------------------- 窗口工具

def _title(h):
    n = _u.GetWindowTextLengthW(h)
    if n <= 0:
        return ''
    b = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(h, b, n + 1)
    return b.value


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(h, b, 256)
    return b.value


def child_windows(hwnd):
    out = []

    def cb(h, l):
        out.append(h)
        return True

    try:
        _u.EnumChildWindows(hwnd, _CB(cb), 0)
    except Exception:
        pass
    return out


def locate_root(hwnd):
    """这个窗口在 UIA 里该从哪儿开始看。返回 (root_hwnd, note)。

    Chromium/Electron 必须打到 Chrome_RenderWidgetHostHWND 子窗口：
        顶层 13 个节点，子窗口 1700 个。
    多个渲染层时取最后一个 —— 实测抖音有三个子窗口，
        只有最后一个活动（25 节点 vs 2 节点）。
    """
    try:
        if not _u.IsWindow(int(hwnd)):
            return hwnd, 'not_a_window'
    except Exception:
        return hwnd, 'iswindow_check_failed'

    render = [h for h in child_windows(hwnd) if _cls(h) in RENDER_CLASSES]
    if render:
        return render[-1], 'render_host'
    return hwnd, 'top_level'


def _norm_type(role):
    """'edit' / 'Edit' / 'EditControl' 都能认"""
    if not role:
        return None
    r = str(role).strip()
    key = r.lower().replace('control', '')
    if key in _TYPE_MAP:
        return _TYPE_MAP[key]
    if r.endswith('Control'):
        return r
    return r + 'Control'


def _ctrl_cls(type_name):
    return getattr(auto, type_name, None) or auto.Control


# ---------------------------------------------------------------- 原生定位

def locate(hwnd, name=None, role=None, aid=None, cls=None, index=0,
           regex=False, contains=False, depth=SEARCH_DEPTH):
    """用 UIA 原生搜索现场找一个控件。不依赖快照。

    参数
    ----
    name      名字。默认精确匹配；contains=True 走 RegexName 做包含匹配
    role      类型简写或全名：'edit' / 'EditControl' / 'button'
    aid       AutomationId
    cls       窗口类名
    index     第几个命中（0 起），对应 foundIndex
    regex     name 当正则用

    返回
    ----
    {'ok':True,'ctrl':对象,'info':{type,name,aid,rect,pats}}
    或 {'ok':False,'err':...,'msg':...}
    """
    if hwnd is None:
        return {'ok': False, 'err': 'no_hwnd', 'msg': '必须给窗口句柄'}
    with auto.UIAutomationInitializerInThread():
        root_hwnd, note = locate_root(int(hwnd))
        try:
            root = auto.ControlFromHandle(root_hwnd)
        except Exception as e:
            return {'ok': False, 'err': 'root_failed', 'msg': repr(e)}
        if root is None:
            return {'ok': False, 'err': 'root_failed', 'msg': '拿不到根控件'}

        type_name = _norm_type(role)
        klass = _ctrl_cls(type_name) if type_name else auto.Control

        kw = {'searchFromControl': root, 'searchDepth': int(depth),
              'foundIndex': int(index) + 1}
        if name is not None:
            if regex or contains:
                # RegexName 做包含：把用户给的串当字面量转义，再放宽
                if regex:
                    kw['RegexName'] = name
                else:
                    import re as _re
                    kw['RegexName'] = '.*' + _re.escape(name) + '.*'
            else:
                kw['Name'] = name
        if aid:
            kw['AutomationId'] = aid
        if cls:
            kw['ClassName'] = cls

        t0 = time.time()
        try:
            ctrl = klass(**kw)
        except Exception as e:
            return {'ok': False, 'err': 'search_failed', 'msg': repr(e)}
        try:
            if not ctrl.Exists(0, 0):
                return {'ok': False, 'err': 'not_found',
                        'msg': '没找到 name=%r role=%r aid=%r'
                               % (name, role, aid),
                        'root_note': note,
                        'elapsed_ms': int((time.time() - t0) * 1000)}
        except Exception as e:
            return {'ok': False, 'err': 'exists_failed', 'msg': repr(e)}

        info = _info(ctrl)
        info['hwnd'] = int(hwnd)
        info['root_hwnd'] = root_hwnd
        info['root_note'] = note
        info['elapsed_ms'] = int((time.time() - t0) * 1000)
        return {'ok': True, 'ctrl': ctrl, 'info': info}


def _info(c):
    """把一个控件描述成可 JSON 化的字典"""
    name = aid = typ = None
    rect = None
    try:
        name = c.Name or ''
    except Exception:
        pass
    try:
        aid = c.AutomationId or ''
    except Exception:
        pass
    try:
        typ = c.ControlTypeName
    except Exception:
        pass
    try:
        r = c.BoundingRectangle
        rect = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        pass
    return {'type': typ, 'name': name, 'aid': aid, 'rect': rect,
            'pats': _patterns_of(c), 'value': _value_of(c)}


def _patterns_of(ctrl):
    """探测支持哪些语义操作。

    GetXxxPattern 不是所有类型都有：HyperlinkControl 就没有
    GetSelectionItemPattern / GetTogglePattern，直接调会 AttributeError。
    必须 getattr 探，不能按类型猜。
    """
    out = []
    for name, fn, call in (
        ('invoke', 'GetInvokePattern', None),
        ('select', 'GetSelectionItemPattern', None),
        ('toggle', 'GetTogglePattern', None),
        ('value', 'GetValuePattern', None),
        ('expand', 'GetExpandCollapsePattern', None),
        ('legacy', 'GetLegacyIAccessiblePattern', None),
        ('scroll', 'GetScrollPattern', None),
    ):
        f = getattr(ctrl, fn, None)
        if f is None:
            continue
        try:
            if f() is not None:
                out.append(name)
        except Exception:
            pass
    return out


def _value_of(ctrl):
    f = getattr(ctrl, 'GetValuePattern', None)
    if f is None:
        return None
    try:
        v = f()
        return None if v is None else (v.Value or '')
    except Exception:
        return None


def _try(c, meth):
    f = getattr(c, meth, None)
    if f is None:
        return None
    try:
        return f()
    except Exception:
        return None


def _safe(fn, d=None):
    try:
        return fn()
    except Exception:
        return d


# ---------------------------------------------------------------- 执行动作

def _do(ctrl, op, text=None, hwnd=None):
    """对已拿到的控件对象执行操作。调用方负责 COM 初始化。"""
    if op == 'get':
        d = _info(ctrl)
        d['ok'] = True
        return d

    if op == 'set':
        vp = _try(ctrl, 'GetValuePattern')
        if vp is None:
            return {'ok': False, 'err': 'no_value_pattern',
                    'msg': '这个控件不支持直接写值，用 ptext 发按键'}
        try:
            vp.SetValue(str(text if text is not None else ''))
            return {'ok': True, 'set': str(text or '')}
        except Exception as e:
            return {'ok': False, 'err': 'set_failed', 'msg': repr(e)}

    if op == 'focus':
        try:
            ctrl.SetFocus()
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'err': 'focus_failed', 'msg': repr(e)}

    if op in ('invoke', 'click'):
        if op == 'invoke':
            # 语义优先：不移动鼠标、不需要焦点
            for pat, label in (('Invoke', 'invoke'),
                               ('SelectionItem', 'select'),
                               ('Toggle', 'toggle')):
                p = _try(ctrl, 'Get%sPattern' % pat)
                if p is None:
                    continue
                try:
                    if pat == 'Invoke':
                        p.Invoke()
                    elif pat == 'SelectionItem':
                        p.Select()
                    else:
                        p.Toggle()
                    return {'ok': True, 'mechanism': label}
                except Exception:
                    continue
            lp = _try(ctrl, 'GetLegacyIAccessiblePattern')
            if lp is not None:
                try:
                    lp.DoDefaultAction()
                    return {'ok': True, 'mechanism': 'legacy'}
                except Exception:
                    pass
        # 坐标兜底：元素中心 PostMessage（不抢前台）
        rect = _safe(lambda: ctrl.BoundingRectangle)
        if rect is None:
            return {'ok': False, 'err': 'no_rect',
                    'msg': '控件没有可用矩形，算不出坐标'}
        if hwnd is None:
            return {'ok': False, 'err': 'no_hwnd'}
        cx = int((rect.left + rect.right) / 2)
        cy = int((rect.top + rect.bottom) / 2)
        pt = wt.POINT(cx, cy)
        if not _u.ScreenToClient(int(hwnd), ctypes.byref(pt)):
            return {'ok': False, 'err': 'screen_to_client_failed'}
        import postinput
        postinput.post_click(int(hwnd), pt.x, pt.y, 0.08)
        return {'ok': True, 'mechanism': 'post_click',
                'client': [pt.x, pt.y]}

    return {'ok': False, 'err': 'unknown_op', 'msg': str(op)}


def act_live(hwnd, op='invoke', name=None, role=None, aid=None, cls=None,
             index=0, regex=False, contains=False, text=None,
             depth=SEARCH_DEPTH, retry=1):
    """现场定位 + 立刻执行。**最稳的一条路。**

    比快照路径稳的原因：每次都是新鲜查找，不受页面重绘影响。
    失败重试一次（重试前不保留任何状态，等于再查一遍）。
    """
    last = None
    for attempt in range(max(1, retry + 1)):
        hit = locate(hwnd, name=name, role=role, aid=aid, cls=cls,
                     index=index, regex=regex, contains=contains, depth=depth)
        if not hit.get('ok'):
            last = hit
            if hit.get('err') == 'not_found':
                # 找不到就别重复查了，重试也不会变出来
                break
            time.sleep(0.12)
            continue
        with auto.UIAutomationInitializerInThread():
            r = _do(hit['ctrl'], op, text=text, hwnd=hwnd)
        r['target'] = hit['info']
        r['attempt'] = attempt + 1
        if r.get('ok'):
            return r
        last = r
        time.sleep(0.12)
    if last is None:
        last = {'ok': False, 'err': 'unknown'}
    last.setdefault('query', {'name': name, 'role': role, 'aid': aid,
                              'index': index})
    return last


# ---------------------------------------------------------------- 快照

def snapshot(hwnd, maxd=DEFAULT_DEPTH, only_interactive=True,
             ttl=DEFAULT_TTL):
    """抓整个控件树。

    返回 {'ok','id','hwnd','root_hwnd','note','count','elapsed_ms','items'}
    items 每项 {ref,type,name,aid,rect,pats,d,vis}
    """
    with auto.UIAutomationInitializerInThread():
        t0 = time.time()
        root_hwnd, note = locate_root(int(hwnd))
        try:
            root = auto.ControlFromHandle(root_hwnd)
        except Exception as e:
            return {'ok': False, 'err': 'root_failed', 'msg': repr(e),
                    'hwnd': hwnd, 'root_hwnd': root_hwnd}
        if root is None:
            return {'ok': False, 'err': 'root_failed', 'msg': '拿不到根控件',
                    'hwnd': hwnd}

        sid = 's' + uuid.uuid4().hex[:12]
        items, store = [], {}

        def walk(c, d):
            if d > maxd:
                return
            try:
                typ = c.ControlTypeName
            except Exception:
                return          # 失效节点整枝跳过
            name = _safe(lambda: c.Name, '') or ''
            aid = _safe(lambda: c.AutomationId, '') or ''
            pats = _patterns_of(c)
            rect = _safe(lambda: [int(c.BoundingRectangle.left),
                                  int(c.BoundingRectangle.top),
                                  int(c.BoundingRectangle.right),
                                  int(c.BoundingRectangle.bottom)])
            actionable = bool(pats) or typ in INTERACTIVE_TYPES
            keep = True
            if only_interactive:
                keep = bool(name) or bool(aid) or actionable
            if keep:
                ref = 'e%d' % (len(items) + 1)
                vis = bool(rect and rect[2] > rect[0] and rect[3] > rect[1])
                items.append({'ref': ref, 'type': typ, 'name': name[:200],
                              'aid': aid[:120], 'rect': rect, 'pats': pats,
                              'd': d, 'vis': vis})
                store[ref] = c
            try:
                for ch in c.GetChildren():
                    walk(ch, d + 1)
            except Exception:
                pass

        walk(root, 0)

        with _snap_lock:
            now = time.time()
            for k in [k for k, v in _snaps.items() if now - v['ts'] > v['ttl']]:
                _snaps.pop(k, None)
            if len(_snaps) >= _MAX_SNAPS:
                for k in sorted(_snaps, key=lambda x: _snaps[x]['ts'])[:4]:
                    _snaps.pop(k, None)
            _snaps[sid] = {'ts': now, 'ttl': float(ttl), 'hwnd': int(hwnd),
                           'root_hwnd': root_hwnd, 'controls': store,
                           'items': items}

        return {'ok': True, 'id': sid, 'hwnd': int(hwnd),
                'root_hwnd': root_hwnd, 'note': note,
                'count': len(items),
                'elapsed_ms': int((time.time() - t0) * 1000),
                'items': items}


def matches(items, name=None, role=None, aid=None, contains=True):
    """纯函数匹配 + 打分排序。给 find() 和 HTTP 层共用。

    为什么要打分：抖音侧栏有个 GroupControl，名字是
        '精选 推荐 AI抖音 关注 朋友 1 我的 直播'
    纯子串匹配会先撞上它，点下去等于点容器，什么都不会发生
    （实测第一次就点错了）。所以必须让「精确命中 + 可点」排前面。
    """
    type_name = _norm_type(role)

    def name_score(it):
        n = (it.get('name') or '')
        if not name:
            return 0
        if n == name:
            return 100
        nl, ql = n.lower(), name.lower()
        if contains and ql in nl:
            # 名字越短越可能是精确目标（容器名通常很长）
            return 40 - min(30, len(n) // 8)
        return -1

    out = []
    for it in items:
        if type_name and (it.get('type') or '') != type_name:
            continue
        if aid and (it.get('aid') or '') != aid:
            continue
        if name:
            sc = name_score(it)
            if sc < 0:
                continue
        else:
            sc = 0
        if it.get('pats') and 'invoke' in it['pats']:
            sc += 50
        if (it.get('type') or '') in INTERACTIVE_TYPES:
            sc += 30
        out.append((sc, it.get('d', 99), it))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in out]


def find(sid, name=None, role=None, aid=None, contains=True, index=0):
    """在快照里找 ref（带排序，最像目标排第一）"""
    with _snap_lock:
        snap = _snaps.get(sid)
    if not snap:
        return {'ok': False, 'err': 'stale_snapshot',
                'msg': '快照不存在或已过期，重新 snapshot'}
    if time.time() - snap['ts'] > snap['ttl']:
        return {'ok': False, 'err': 'stale_snapshot',
                'msg': '快照已过期 %.0fs' % (time.time() - snap['ts'])}
    hits = matches(snap['items'], name=name, role=role, aid=aid,
                   contains=contains)
    if not hits:
        by_type = {}
        for it in snap['items']:
            by_type[it['type']] = by_type.get(it['type'], 0) + 1
        return {'ok': False, 'err': 'not_found',
                'msg': '快照里没找到（共 %d 个节点，快照有深度上限）'
                       % len(snap['items']),
                'hint': '控件可能在更深处 → 改用 locate()/act_live() 现场查，'
                        '它们不受快照深度限制',
                'by_type': by_type}
    if not (-len(hits) <= index < len(hits)):
        return {'ok': False, 'err': 'index_out_of_range',
                'msg': '命中 %d 个，index=%d 越界' % (len(hits), index)}
    return {'ok': True, 'total': len(hits), 'item': hits[index],
            'candidates': [{'ref': h['ref'], 'type': h['type'],
                            'name': h['name'][:60]} for h in hits[:6]]}


def act(sid, ref, op='invoke', text=None, hwnd=None):
    """对快照里的 ref 执行操作。

    元素失效（COMError -2147220991）会明确返回 stale，
    不假装成功 —— 页面一变就得重新 snapshot。
    """
    with _snap_lock:
        snap = _snaps.get(sid)
        if not snap:
            return {'ok': False, 'err': 'stale_snapshot', 'msg': '快照不存在'}
        if time.time() - snap['ts'] > snap['ttl']:
            return {'ok': False, 'err': 'stale_snapshot',
                    'msg': '快照已过期 %.0fs' % (time.time() - snap['ts'])}
        ctrl = snap['controls'].get(ref)
        if ctrl is None:
            return {'ok': False, 'err': 'bad_ref', 'msg': '快照里没有 %s' % ref}
        tgt = hwnd or snap['hwnd']
    with auto.UIAutomationInitializerInThread():
        try:
            return _do(ctrl, op, text=text, hwnd=tgt)
        except Exception as e:
            m = repr(e)
            kind = 'com_error' if ('COMError' in type(e).__name__
                                   or '-2147220' in m) else 'error'
            return {'ok': False, 'err': kind, 'msg': m,
                    'hint': '页面可能已变化，重新 snapshot' if kind == 'com_error' else ''}


def drop(sid=None):
    with _snap_lock:
        if sid is None:
            _snaps.clear()
        else:
            _snaps.pop(sid, None)


def stats():
    with _snap_lock:
        now = time.time()
        return {'snapshots': len(_snaps),
                'ids': {k: {'age_s': round(now - v['ts'], 1),
                            'items': len(v['items']),
                            'refs': len(v['controls'])}
                        for k, v in _snaps.items()}}


# ---------------------------------------------------------------- 其他

def active_doc_url(hwnd):
    """读浏览器窗口当前 URL（DocumentControl 的 Value）。"""
    with auto.UIAutomationInitializerInThread():
        root_hwnd, _ = locate_root(int(hwnd))
        try:
            root = auto.ControlFromHandle(root_hwnd)
            doc = auto.DocumentControl(searchFromControl=root,
                                       searchDepth=6)
            if not doc.Exists(0, 0):
                return {'ok': False, 'err': 'no_document'}
            vp = _try(doc, 'GetValuePattern')
            return {'ok': True, 'url': vp.Value if vp else None,
                    'title': _safe(lambda: doc.Name)}
        except Exception as e:
            return {'ok': False, 'err': 'failed', 'msg': repr(e)}


VK = {
    'esc': 0x1B, 'enter': 0x0D, 'space': 0x20, 'tab': 0x09,
    'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12, 'back': 0x08,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'home': 0x24, 'end': 0x23, 'del': 0x2E, 'ins': 0x2D,
}
for _c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    VK[_c.lower()] = ord(_c)
for _d in '0123456789':
    VK[_d] = ord(_d)
del _c, _d


def vk_of(name):
    s = str(name).strip().lower()
    if s in VK:
        return VK[s]
    if s.startswith('0x'):
        return int(s, 16)
    if s.isdigit():
        return int(s)
    raise ValueError('不认识的键: %s' % name)
