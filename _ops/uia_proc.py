# -*- coding: utf-8 -*-
"""uia_proc.py —— UIA 专用子进程。

为什么需要它
------------
uiautomation 的 `_AutomationClient._instance` 是**进程级单例**：
第一个用到它的线程通过 comtypes 创建 IUIAutomation，指针存在类属性上，
之后所有线程直接复用同一个接口指针。comtypes 的 CoInitializeEx 走的是 STA，
STA 的接口指针有线程亲和性 —— 换个线程直接用就是解引用失效代理，
access violation 崩在 `ElementFromHandle`（uiautomation.py:10461）。

实测证据：
  - 单独线程里连调 locate 60 次        → 不崩
  - HTTP 每请求新线程、只调 locate     → 60 次不崩
  - 走 worker.do_action（真实路径）    → 第 2 次必崩，exit 0xC0000005

换成 MTA（CoInitializeEx(None, 0)）也没用 —— 单例指针仍然被跨 apartment 复用。

所以：把整个 UIA 调用搬进一个**单线程**子进程，worker 通过 JSON 行协议请求。
子进程只有一个线程碰 UIA，单例指针永远属于那个线程，不存在跨线程复用；
万一它还是崩了，worker 只是收到 EOF，重拉一个子进程重试，**worker 本身不死**。

协议
----
请求（一行 JSON）：{"id":1,"fn":"act_live","args":[...],"kwargs":{...}}
响应（一行 JSON）：{"id":1,"ok":true,"r":{...}}
"""
import json
import os
import sys

os.environ['WW_UIA_INPROC'] = '1'          # 让 uia_x 用本地实现，不递归转发
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uia_x  # noqa: E402

FNS = {}
for _n in ('act_live', 'snapshot', 'find', 'act', 'drop', 'stats',
           'active_doc_url', 'probe', 'locate', 'matches', 'locate_root',
           'vk_of'):
    _f = getattr(uia_x, _n, None)
    if _f is not None:
        FNS[_n] = _f
del _n, _f


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get('id')
        name = req.get('fn')
        fn = FNS.get(name)
        try:
            if fn is None:
                rep = {'id': rid, 'ok': False,
                       'r': {'ok': False, 'err': 'no_such_fn', 'fn': name}}
            else:
                r = fn(*(req.get('args') or []), **(req.get('kwargs') or {}))
                if isinstance(r, dict) and 'ctrl' in r:
                    r = dict(r)
                    r.pop('ctrl', None)      # COM 对象不能跨进程
                rep = {'id': rid, 'ok': True, 'r': r}
        except Exception as e:
            rep = {'id': rid, 'ok': False,
                   'r': {'ok': False, 'err': 'proc_exc', 'msg': repr(e)}}
        try:
            out.write(json.dumps(rep, ensure_ascii=False, default=str) + '\n')
            out.flush()
        except Exception:
            break


main()
