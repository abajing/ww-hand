# -*- coding: utf-8 -*-
"""实验盘断言：老动作不回归 + 新动作真能用"""
import json, os, sys, time, urllib.parse, urllib.request

PORT = int(os.environ.get('WW_PORT', '8792'))
BASE = 'http://127.0.0.1:%d' % PORT
ROOT = os.environ.get('WW_HOME') or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
TOK = open(os.path.join(ROOT, '_ipc', 'broker.token')).read().strip()

PASS = FAIL = 0
FAILS = []

def req(path, data=None, timeout=120):
    url = BASE + path + (('&' if '?' in path else '?') + 'token=' + TOK)
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    h = {'Content-Type': 'application/json'} if body else {}
    r = urllib.request.Request(url, data=body, headers=h,
                               method='POST' if body else 'GET')
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

def chk(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS %-42s %s' % (name, extra))
    else:
        FAIL += 1
        FAILS.append(name)
        print('  FAIL %-42s %s' % (name, extra))

def do(acts, name=None):
    r = req('/do', {'actions': acts})
    res = (r.get('results') or [{}])[0].get('r') or {}
    return r, res

print('=== 0. 健康与鉴权 ===')
h = req('/health')
chk('health 不需要 token', h.get('ok') is True, 'pid=%s admin=%s' % (h.get('pid'), h.get('admin')))
try:
    urllib.request.urlopen(BASE + '/status', timeout=5)
    chk('无 token 访问 /status 被拒', False, '居然通了')
except urllib.error.HTTPError as e:
    chk('无 token 访问 /status 被拒', e.code == 403, 'code=%s' % e.code)
except Exception as e:
    chk('无 token 访问 /status 被拒', False, repr(e))

print('=== 1. 老能力不回归 ===')
s = req('/status')
chk('/status 结构', all(k in s for k in ('ok','pid','admin','windows','screen','game','fg')),
    'windows=%s' % s.get('windows'))
w = req('/windows')
chk('/windows 有结果', w.get('ok') and w.get('count', 0) > 0, 'count=%s' % w.get('count'))

# 找记事本之类不需要游戏的窗口做照度测试；找不到就用抖音
target_hwnd = None
for win in w.get('windows') or []:
    if '抖音' in (win.get('title') or ''):
        target_hwnd = win['hwnd']; break
chk('找到测试窗口', target_hwnd is not None, 'hwnd=%s' % target_hwnd)

sh = req('/shot?hwnd=%s' % target_hwnd, timeout=90)
chk('/shot 落盘', sh.get('ok') and os.path.isfile(sh.get('path') or ''),
    '%s %s' % (os.path.basename(sh.get('path') or ''), sh.get('size')))
chk('/shot 尺寸合理', (sh.get('size') or [0])[0] > 100, str(sh.get('size')))

r, res = do([{'t': 'wait', 'ms': 30}])
chk('/do wait', r.get('ok') and res.get('ok'))

r, res = do([{'t': 'script', 'code': 'RESULT = 6*7'}])
chk('/script 执行', r.get('ok') and '42' in str(res.get('out')), str(res.get('out'))[:40])

r, res = do([{'t': 'wins'}])
chk('/do wins', res.get('ok') and res.get('count', 0) > 0, 'count=%s' % res.get('count'))

print('=== 2. 输入能力探测 ===')
r, res = do([{'t': 'probe', 'hwnd': target_hwnd}])
chk('probe 返回', res.get('ok') is True,
    'cls=%s recommend=%s' % (res.get('cls'), res.get('recommend')))
chk('probe 判定 Chromium', 'Chrome' in (res.get('cls') or ''), res.get('cls'))
chk('probe 建议走 uia', res.get('recommend') == 'uia', res.get('recommend'))

print('=== 3. 控件级定位（新能力）===')
r, res = do([{'t': 'uia', 'hwnd': target_hwnd, 'name': '精选',
              'role': 'link', 'op': 'get'}])
chk('uia 定位 精选', res.get('ok') is True,
    '%s %r' % ((res.get('target') or {}).get('type'),
               (res.get('target') or {}).get('name')))

r, res = do([{'t': 'uia_snap', 'hwnd': target_hwnd, 'depth': 14}])
snap_id = res.get('id')
chk('uia_snap 成功', res.get('ok') is True,
    'id=%s count=%s %sms' % (snap_id, res.get('count'), res.get('elapsed_ms')))
chk('uia_snap 节点数合理', (res.get('count') or 0) > 50, str(res.get('count')))
chk('uia_snap 走了渲染层', res.get('note') == 'render_host', res.get('note'))

r, res = do([{'t': 'uia_find', 'snap': snap_id, 'role': 'edit'}])
chk('uia_find role=edit', res.get('ok') is True,
    'ref=%s' % ((res.get('item') or {}).get('ref')))
edit_ref = (res.get('item') or {}).get('ref')

r, res = do([{'t': 'uia_find', 'snap': snap_id, 'name': '精选'}])
chk('uia_find 排序正确(不选容器)',
    res.get('ok') and (res.get('item') or {}).get('type') == 'HyperlinkControl',
    (res.get('item') or {}).get('type'))

print('=== 4. 写值 + 读回（关键：真的生效）===')
if edit_ref:
    r, res = do([{'t': 'uia_act', 'snap': snap_id, 'ref': edit_ref,
                  'op': 'set', 'text': '融合版自检7733'}])
    chk('uia_act set', res.get('ok') is True, str(res.get('set')))
    r, res2 = do([{'t': 'uia_act', 'snap': snap_id, 'ref': edit_ref, 'op': 'get'}])
    chk('读回 = 写入', (res2.get('value') or '') == '融合版自检7733',
        '读回=%r' % (res2.get('value'),))
else:
    chk('uia_act set', False, '没有 edit_ref')

print('=== 5. 语义点击（真跳转才算）===')
r, res = do([{'t': 'uia_url', 'hwnd': target_hwnd}])
url0 = res.get('url')
chk('uia_url 读到 URL', bool(url0), str(url0)[:55])
# 自适应：点「当前没在的那个」页签。第一版这里固定点「推荐」，
# 可是起始页就已经是 ?recommend=1（就是推荐页），点了等于原地不动，
# 断言必然红 —— 是测试写错，不是代码错。改掉。
nav = '精选' if 'recommend' in (url0 or '') else '推荐'
r, res = do([{'t': 'uia', 'hwnd': target_hwnd, 'name': nav,
              'role': 'link', 'op': 'invoke'}])
chk('语义点击 %s' % nav, res.get('ok') is True, res.get('mechanism'))
time.sleep(2.4)
r, res = do([{'t': 'uia_url', 'hwnd': target_hwnd}])
url1 = res.get('url')
chk('URL 真变了', url0 != url1, '%s -> %s' % (str(url0)[:34], str(url1)[:34]))

# 反向再点回去，确认不是碰巧
back = '推荐' if nav == '精选' else '精选'
do([{'t': 'uia', 'hwnd': target_hwnd, 'name': back, 'role': 'link',
     'op': 'invoke'}])
time.sleep(2.4)
r, res = do([{'t': 'uia_url', 'hwnd': target_hwnd}])
chk('反向点 %s 也生效' % back, res.get('url') != url1, str(res.get('url'))[:34])

print('=== 6. HTTP /uia 端点 ===')
u = req('/uia?' + urllib.parse.urlencode({'hwnd': target_hwnd, 'role': 'button'}))
chk('/uia GET 可用', u.get('ok') is True,
    '%s %r' % ((u.get('target') or {}).get('type'),
               (u.get('target') or {}).get('name')[:20]))

print('=== 7. 文本输入 ===')
r, res = do([{'t': 'ptext', 'hwnd': target_hwnd, 'text': 'ab'}])
chk('ptext 投递', res.get('ok') is True and res.get('sent') == 2, str(res.get('sent')))

print('=== 8. 原生 Win32 文本投递（真写进去才算）===')
# 这一段是补上一个假通过：原来只查 ptext 报了几字，
# 可它把 WM_CHAR 投到了顶层窗口，报成功 8 字、控件里其实是空的。
# 断言必须读到控件内容才算数。
import subprocess
np = subprocess.Popen(['notepad.exe'])
time.sleep(2.2)
import ctypes
uu = ctypes.windll.user32
uu.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_ulong)]
nph = 0
def _cb(h, l):
    global nph
    pid = ctypes.c_ulong()
    uu.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value == np.pid and uu.IsWindowVisible(h) \
            and uu.GetWindowTextLengthW(h) > 0:
        nph = h
    return True
uu.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                 ctypes.c_void_p)(_cb), 0)
try:
    r, res = do([{'t': 'probe', 'hwnd': nph}])
    chk('记事本 probe 走 wm_char', res.get('recommend') == 'wm_char',
        str(res.get('recommend')))
    ec = (res.get('uia') or {}).get('edit_child')
    chk('记事本找到编辑子控件', bool(ec), str(ec))

    r, res = do([{'t': 'ptext', 'hwnd': nph, 'text': '真写进去才算'}])
    chk('ptext 报投递 6 字', res.get('sent') == 6, str(res.get('sent')))
    time.sleep(0.7)
    r, res = do([{'t': 'uia', 'hwnd': nph, 'role': 'edit', 'op': 'get'}])
    got = res.get('value') or ''
    chk('ptext 内容真的进去了', got == '真写进去才算', '读回=%r' % got)
finally:
    np.kill()

print()
print('=' * 56)
print('通过 %d / %d' % (PASS, PASS + FAIL))
if FAILS:
    print('失败项:')
    for f in FAILS:
        print('  -', f)
sys.exit(0 if FAIL == 0 else 1)