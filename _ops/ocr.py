# -*- coding: utf-8 -*-
"""读游戏截图里的东西：左下角坐标、任务文字、按钮文字
用法: python ocr.py <图片> [区域名]
区域名: coord / task / all
"""
import sys, io, re, os
import numpy as np
from PIL import Image

_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def ocr_img(img):
    """img: PIL.Image -> list of (text, score, box)"""
    arr = np.array(img.convert('RGB'))
    res, _ = get_ocr()(arr)
    out = []
    if res:
        for item in res:
            box, text, score = item[0], item[1], item[2]
            x = sum(p[0] for p in box) / 4
            y = sum(p[1] for p in box) / 4
            out.append({'text': text, 'score': float(score), 'x': x, 'y': y, 'box': box})
    return out


def read_coord(img):
    """左下角坐标，返回 (x, y, z) 或 None"""
    w, h = img.size
    crop = img.crop((0, int(h * 0.90), int(w * 0.40), h))
    # 放大有助于 OCR
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    for item in ocr_img(crop):
        t = item['text'].replace(' ', '')
        m = re.search(r'(-?\d+),(-?\d+),(-?\d+)', t)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def read_all_flat(img):
    items = ocr_img(img)
    return [(it['text'], round(it['x']), round(it['y']), round(it['score'], 2)) for it in items]


if __name__ == '__main__':
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'all'
    img = Image.open(path)
    if mode == 'coord':
        print('coord =', read_coord(img))
    elif mode == 'task':
        w, h = img.size
        crop = img.crop((0, int(h * 0.22), int(w * 0.30), int(h * 0.50)))
        for t, x, y, s in read_all_flat(crop):
            print('  %-40s (%d,%d) %.2f' % (t, x, y, s))
    else:
        print('size =', img.size)
        for t, x, y, s in read_all_flat(img):
            print('  %-40s (%d,%d) %.2f' % (t, x, y, s))
