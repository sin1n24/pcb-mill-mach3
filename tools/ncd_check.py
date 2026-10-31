#!/usr/bin/env python3
"""切削前の .ncd 検査（標準ライブラリのみ）。機械に渡す前に実ファイルそのものを調べる。

  python ncd_check.py 01_isolation.ncd 02_holes.ncd 03_outline.ncd --thickness 1.0 --width 39 --height 41.1

問題があれば理由を表示して終了コード 1 を返す。
"""
import argparse
import re
import sys

COORD = re.compile(r'([XYZ])(-?\d*\.?\d*)')


def check(path, thickness, width, height, through, tool):
    errs = []
    x = y = None
    z = None
    motion = None
    xs, ys, zs = [], [], []
    for no, raw in enumerate(open(path, encoding='ascii', errors='replace'), 1):
        line = raw.strip()
        if not line or line == '%':
            continue
        if '(' in line or ';' in line:
            errs.append((no, 'コメントがある（Mach3 環境によってはここで止まる）', line))
            continue
        if re.search(r'\bM0?[01](?!\d)', line):
            errs.append((no, 'M0/M1 がある（止まったままになる）', line))
        g = re.search(r'\bG0?([01])(?!\d)', line)
        if g:
            motion = g.group(1)
        pos = {}
        for axis, value in COORD.findall(line):
            if '.' not in value:
                errs.append((no, '小数点のない座標 %s%s（0.001mm 単位と解釈されると原点付近へ一直線に動く）' % (axis, value), line))
                continue
            pos[axis] = float(value)
        nx, ny, nz = pos.get('X', x), pos.get('Y', y), pos.get('Z', z)
        moved = nx != x or ny != y
        if motion == '0' and moved and z is not None and z < 0:
            errs.append((no, '切り込んだまま早送りしている（Z=%.3f）' % z, line))
        if motion == '1' and 'Z' in pos and nz is not None and thickness is not None and nz < -(thickness + through):
            errs.append((no, '深すぎる: Z%.3f（板厚 %.2f + 許容 %.2f を超える）' % (nz, thickness, through), line))
        x, y, z = nx, ny, nz
        if z is not None and z < 0 and x is not None and y is not None:
            xs.append(x)
            ys.append(y)
            zs.append(z)
    if xs:
        info = 'X %.2f..%.2f  Y %.2f..%.2f  Z %.3f..%.3f' % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        margin = tool  # 外形は工具半径ぶん外を通るので少し許す
        if width is not None and (min(xs) < -margin or max(xs) > width + margin):
            errs.append((0, '切削範囲が基板の幅 %.2f を超える' % width, info))
        if height is not None and (min(ys) < -margin or max(ys) > height + margin):
            errs.append((0, '切削範囲が基板の高さ %.2f を超える' % height, info))
    else:
        info = '切り込みなし'
    return errs, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--thickness', type=float, help='板厚 mm（指定すると深すぎる切り込みを検出）')
    ap.add_argument('--through', type=float, default=0.2, help='板厚を超えてよい量 mm（既定 0.2）')
    ap.add_argument('--width', type=float, help='基板の幅 mm')
    ap.add_argument('--height', type=float, help='基板の高さ mm')
    ap.add_argument('--tool', type=float, default=0.5, help='工具径 mm（範囲判定の余裕に使う）')
    a = ap.parse_args()
    bad = 0
    for f in a.files:
        errs, info = check(f, a.thickness, a.width, a.height, a.through, a.tool)
        print('%s: %s  [%s]' % (f, 'OK' if not errs else 'NG %d 件' % len(errs), info))
        for no, why, line in errs[:8]:
            print('  行 %d: %s: %s' % (no, why, line))
        if len(errs) > 8:
            print('  ... ほか %d 件' % (len(errs) - 8))
        bad += bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
