# -*- coding: utf-8 -*-
r"""生成 / 校验 清单.md5。

以前这个清单是手工敲出来的，没有生成脚本，「日后核对有没有被改」这句
等于没有兑现 —— 谁也没法一键核。现在补上：

    python _ops\manifest.py --write     重新生成 清单.md5
    python _ops\manifest.py --check     逐条核对，有任何不一致就 exit 1

排除规则和 .gitignore 保持一致：运行时目录、机器相关文件、备份、日志，
还有本文件自己生成的清单本身。
"""
import hashlib
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, '清单.md5')

SKIP_DIRS = {'.git', '_ipc', '_log', '_shots', '_state', '__pycache__',
             'incoming', '_local'}
SKIP_FILES = {'清单.md5', 'license.key', 'python.txt'}
SKIP_SUFFIX = ('.pyc', '.pyo', '.log', '.bak')


def collect():
    """返回 [(相对路径, md5, 字节数)]，按路径排序，分隔符统一用 /。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace('\\', '/')
            h = hashlib.md5()
            try:
                with open(full, 'rb') as f:
                    for chunk in iter(lambda: f.read(1 << 20), b''):
                        h.update(chunk)
            except OSError as e:
                print('  跳过 %s（%r）' % (rel, e))
                continue
            out.append((rel, h.hexdigest().upper(), os.path.getsize(full)))
    out.sort(key=lambda r: r[0])
    return out


def write():
    rows = collect()
    lines = [
        '# ww-hand 文件清单  %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
        '# 只列入库文件；_ipc / _log / _shots / _state / __pycache__ / '
        'python.txt / license.key 是运行时或机器相关，不纳入',
        '',
    ]
    for rel, md5, size in rows:
        lines.append('%s  %10d  %s' % (md5, size, rel))
    with open(MANIFEST, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    print('已生成 %s（%d 个文件）' % (MANIFEST, len(rows)))
    return 0


def check():
    if not os.path.exists(MANIFEST):
        print('清单不存在：%s' % MANIFEST)
        return 1
    want = {}
    with open(MANIFEST, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n')
            if not line or line.startswith('#'):
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                print('  格式不认识：%r' % line[:60])
                continue
            want[parts[2].strip()] = parts[0].strip().upper()

    have = {rel: md5 for rel, md5, _ in collect()}
    bad = []
    for rel in sorted(set(want) | set(have)):
        if rel not in have:
            bad.append('缺少   %s' % rel)
        elif rel not in want:
            bad.append('多出   %s' % rel)
        elif want[rel] != have[rel]:
            bad.append('被改过 %s' % rel)
    for b in bad:
        print('  ' + b)
    if bad:
        print('%d 处不一致' % len(bad))
        return 1
    print('%d 个文件全部一致' % len(want))
    return 0


def main(argv):
    if '--write' in argv:
        return write()
    if '--check' in argv:
        return check()
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))