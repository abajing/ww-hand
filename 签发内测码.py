# -*- coding: utf-8 -*-
r"""签发 ww-hand 内测授权码（作者自用，不要发给买家）。

用法：
    python 签发内测码.py 60CA690C7038          # 给这台机器签永久码
    python 签发内测码.py 60CA690C7038 260630   # 签到 2026-06-30
    python 签发内测码.py --me                  # 看本机机器码 + 自用码
    python 签发内测码.py --check 授权码        # 手工核对某个码

买家那边只需要：
    1. python _ops\ww_license.py      → 看到「机器码: XXXXXXXXXXXXXXXX」
    2. 把机器码发给你
    3. 你签出 WWB-xxxxxxxxxxxx-xxxxxx-xxxxxxxx 给他
    4. 他打开 _ops\license.key，把那一行换成授权码，存盘，重开 启动.bat
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ops'))
import ww_license as L


def show_me():
    mid = L.machine_id()
    print('本机机器码 : %s' % mid)
    print('自用永久码 : %s' % L.make_key(mid, '999999'))
    try:
        ok, why = L.verify()
    except Exception as e:
        ok, why = False, repr(e)
    print('当前授权   : %s（%s）' % ('通过' if ok else '未通过', why))


def issue(mid, exp):
    mid = mid.strip().upper()
    if len(mid) < 12:
        print('机器码太短，至少要 12 位：%r' % mid)
        return 1
    key = L.make_key(mid, exp)
    print('机器码 : %s' % mid)
    print('到期   : %s' % ('永久' if exp == '999999' else exp))
    print('授权码 : %s' % key)
    print()
    print('发给买家的原文（直接复制）：')
    print('---')
    print('你的 ww-hand 内测授权码：')
    print(key)
    print('把 _ops\\license.key 里那一行换成这串，存盘，重开 启动.bat。')
    print('---')
    return 0


def check(code):
    info = L.parse_key(code)
    if not info:
        print('格式不对，应该是 WWB-xxxxxxxxxxxx-xxxxxx-xxxxxxxx')
        return 1
    good = L.sign(info['mid12'], info['exp']) == info['sig']
    print('机器码前12位 : %s' % info['mid12'])
    print('到期         : %s' % ('永久' if info['exp'] == '999999' else info['exp']))
    print('签名         : %s' % ('对得上' if good else '对不上（被改过或不是这套密钥签的）'))
    return 0 if good else 1


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if argv[0] == '--me':
        show_me()
        return 0
    if argv[0] == '--check':
        if len(argv) < 2:
            print('用法: python 签发内测码.py --check WWB-...')
            return 1
        return check(argv[1])
    exp = argv[1] if len(argv) > 1 else '999999'
    return issue(argv[0], exp)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))