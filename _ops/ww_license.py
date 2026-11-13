# -*- coding: utf-8 -*-
"""ww-hand 内测授权校验。

为什么不用 `license.py` 这个名字：Python 自带一个内置模块就叫 license
（打 LICENSE 文本用的），同名会在 import 时抢先命中，校验逻辑永远不执行。

授权码格式：  WWB-<机器码12位>-<到期日YYMMDD>-<签名8位>
  · 机器码   机器码前 12 位，跟这台机器绑死，换机器要重新签
  · 到期日   999999 表示永久
  · 签名     HMAC-SHA256(内测密钥, "机器码|到期日") 的前 8 位，大写

说明白一点：密钥写在买得到的代码里，这不是强加密，挡的是「随手转发」，
不挡会读源码的人。内测阶段够用；正式版要换成服务端校验。
"""
import hashlib
import hmac
import os
import sys

LICENSE_NAME = 'license.key'
SECRET = b'ww-hand-closed-beta-2026-11'


def _root():
    """_ops 的上一层就是安装根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _machine_guid():
    """注册表 MachineGuid：装系统时生成，重启/换盘都不变，重装才变。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Cryptography',
                           0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        v, _ = winreg.QueryValueEx(k, 'MachineGuid')
        winreg.CloseKey(k)
        return str(v)
    except Exception:
        return ''


def machine_id():
    """16 位大写十六进制机器码。取不到注册表就退到 计算机名+用户名。"""
    seed = _machine_guid()
    if not seed:
        seed = '|'.join([os.environ.get('COMPUTERNAME', ''),
                         os.environ.get('USERNAME', '')])
    return hashlib.sha256(('ww-hand|' + seed).encode('utf-8')).hexdigest()[:16].upper()


def sign(mid12, exp):
    msg = ('%s|%s' % (mid12, exp)).encode('utf-8')
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()[:8].upper()


def make_key(mid=None, exp='999999'):
    """签发一个授权码。exp 用 YYMMDD，永久写 999999。"""
    mid = (mid or machine_id()).upper()
    if len(mid) < 12:
        raise ValueError('机器码长度不对：%r' % mid)
    m12 = mid[:12]
    return 'WWB-%s-%s-%s' % (m12, exp, sign(m12, exp))


def parse_key(text):
    """拆授权码，格式不对返回 None。"""
    parts = (text or '').strip().upper().replace('—', '-').split('-')
    if len(parts) != 4 or parts[0] != 'WWB':
        return None
    return {'mid12': parts[1], 'exp': parts[2], 'sig': parts[3]}


def verify(path=None):
    """返回 (是否通过, 一句话说明)。永不抛异常，调用方只管分支。"""
    mid = machine_id()
    path = path or os.path.join(_root(), '_ops', LICENSE_NAME)
    if not os.path.exists(path):
        return False, '没有找到授权文件 _ops/%s（机器码 %s）' % (LICENSE_NAME, mid)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except Exception as e:
        return False, '授权文件读不出来：%r' % (e,)

    # 允许文件里带注释行，第一个非空非 # 的行就是码
    code = ''
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            code = line
            break

    info = parse_key(code)
    if not info:
        return False, '授权码格式不对：%r' % (code[:40],)
    if info['mid12'] != mid[:12]:
        return False, '授权码不是这台机器的（码 %s / 本机 %s）' % (
            info['mid12'], mid[:12])
    if not hmac.compare_digest(info['sig'], sign(info['mid12'], info['exp'])):
        return False, '授权码签名不对，可能被改过'
    if info['exp'] != '999999':
        import datetime
        try:
            exp = datetime.date(2000 + int(info['exp'][:2]),
                                int(info['exp'][2:4]), int(info['exp'][4:6]))
        except Exception:
            return False, '到期日解析不了：%r' % (info['exp'],)
        if datetime.date.today() > exp:
            return False, '授权已到期（%s）' % exp.isoformat()
        return True, '内测授权有效，到期 %s' % exp.isoformat()
    return True, '内测授权有效（永久）'


def main():
    """命令行：python ww_license.py  → 打印本机机器码和是否已授权。"""
    ok, why = verify()
    print('机器码: %s' % machine_id())
    print('状态  : %s' % ('已授权' if ok else '未授权'))
    print('说明  : %s' % why)
    if not ok:
        print('把上面这行机器码发给作者换授权码，写进 _ops/license.key 即可。')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())