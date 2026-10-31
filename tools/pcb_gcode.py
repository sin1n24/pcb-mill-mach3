#!/usr/bin/env python3
"""片面基板の配置データ(JSON) → 切削用 .ncd（φ0.5 フラットエンドミル1本で 絶縁 → 穴 → 外形）

  python pcb_gcode.py layout.json out --thickness 1.0

- 加工は銅箔面を上にして行うので、部品面の座標を X 反転する。
- 原点 = 銅箔面を上にしたときの基板の左下、Z0 = 銅箔の表面。
- GND は配線せず、絶縁溝を彫った残りの銅箔をベタとして使う。
- 板厚は必須。既定値を置くと確認せずに削ってしまう（1.6mm 前提で 1.0mm 板を削り、捨て板を削り過ぎた）。

必要: shapely（プレビューを出すなら matplotlib も）。JSON の形式は README を参照。
"""
import argparse
import json
import math
import os
import re

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('layout')
ap.add_argument('out')
ap.add_argument('--thickness', type=float, required=True, help='板厚 mm（実物をノギスで測る）')
ap.add_argument('--tool', type=float, default=0.5, help='工具径 mm')
ap.add_argument('--iso-z', type=float, default=-0.20, help='絶縁の深さ mm（-0.10 は切れ残り、-0.15 は手調整が要った）')
ap.add_argument('--iso-f', type=int, default=200, help='絶縁の送り mm/min')
ap.add_argument('--hole-f', type=int, default=120, help='穴の送り mm/min')
ap.add_argument('--out-f', type=int, default=150, help='外形の送り mm/min')
ap.add_argument('--plunge-f', type=int, default=60, help='切り込みの送り mm/min')
ap.add_argument('--step', type=float, default=0.35, help='穴・外形の1回の切り込み mm')
ap.add_argument('--through', type=float, default=0.15, help='貫通で板厚を超える量 mm')
ap.add_argument('--tab', type=float, default=0.5, help='外形タブの残し厚 mm')
ap.add_argument('--spindle', type=int, default=10000, help='主軸回転数')
ap.add_argument('--safe', type=float, default=2.0, help='安全高さ mm')
ap.add_argument('--spoke', type=float, default=0.8, help='サーマルのスポーク幅 mm')
ap.add_argument('--tied', action='append', default=[], metavar='REF', help='GND ピン同士が内部でつながっている部品（例: U1）')
a = ap.parse_args()

d = json.load(open(a.layout, encoding='utf-8'))
os.makedirs(a.out, exist_ok=True)
W, H = d['W'], d['H']
R = a.tool/2
THRU = -(a.thickness + a.through)
TAB_Z = -(a.thickness - a.tab)
pads = d['pads']
MH = d.get('mholes', [])


def padgeom(p):
    if p['shape'][0] == 'C':
        return Point(p['x'], p['y']).buffer(p['shape'][1]/2, 32)
    w, h = p['shape'][1], p['shape'][2]
    return LineString([(p['x'] - (w - h)/2, p['y']), (p['x'] + (w - h)/2, p['y'])]).buffer(h/2, 32)


def rings(geom):
    out = []
    for g in (geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]):
        out.append(list(g.exterior.coords))
        out += [list(i.coords) for i in g.interiors]
    return out


# ---- 異ネット間の間隔（工具が通らないなら止める） ----
cu = {}
for p in pads:
    cu.setdefault(p['net'], []).append(padgeom(p))
for t in d.get('tracks', []):
    cu.setdefault(t['net'], []).append(LineString(t['pts']).buffer(t['w']/2, 16))
cu = {n: unary_union(g) for n, g in cu.items()}
names = sorted(cu)
pairs = [(cu[x].distance(cu[y]), x, y) for i, x in enumerate(names) for y in names[i + 1:]]
gap = min(pairs) if pairs else (9.0, '', '')
print('最小の銅箔間隔 %.3f mm (%s - %s)' % gap)
assert gap[0] >= a.tool + 0.02, '銅箔の間隔が工具径より狭い'

# ---- 絶縁パス（GND 以外の銅箔を一周） ----
iso = []
for n in cu:
    if n != 'GND':
        iso += rings(cu[n].buffer(R + 0.02, 24))

# ---- サーマル ----
# GND パッドの周囲を一周彫り、上下左右に幅 spoke のスポークを残す。ただし本体のベタに出られず、
# そのパッド1点だけでつながる銅箔（袋）へのスポークは残さない（アンテナになってノイズを拾うため）。
gnd = [p for p in pads if p['net'] == 'GND']
DIRS = {'L': (-1, 0), 'R': (1, 0), 'U': (0, 1), 'D': (0, -1)}


def half(p):
    return (p['shape'][1]/2, p['shape'][1]/2) if p['shape'][0] == 'C' else (p['shape'][1]/2, p['shape'][2]/2)


def key(p):
    return p['ref'] + '.' + p['num']


ring = {key(p): LineString(rings(padgeom(p).buffer(R + 0.02, 24))[0]) for p in gnd}
holes_geo = [Point(m['x'], m['y']).buffer(m['d']/2, 48) for m in MH]
pl = box(0, 0, W, H).difference(unary_union([LineString(q).buffer(R, 12) for q in iso]
                                            + [r.buffer(R, 12) for r in ring.values()] + holes_geo))
regs = sorted(list(pl.geoms) if pl.geom_type == 'MultiPolygon' else [pl], key=lambda g: -g.area)
spoke = {}
for p in gnd:
    hw, hh = half(p)
    for k, (dx, dy) in DIRS.items():
        pt = Point(p['x'] + dx*(hw + 2*R + 0.35), p['y'] + dy*(hh + 2*R + 0.35))
        spoke[key(p), k] = next((i for i, g in enumerate(regs) if g.contains(pt)), None)
users = {}
for (name, k), i in spoke.items():
    if i is not None:
        users.setdefault(i, set()).add(name)
thermal, removed = [], []
for p in gnd:
    gaps = []
    g = a.spoke/2 + R
    for k, (dx, dy) in DIRS.items():
        i = spoke[key(p), k]
        if i is None:
            continue
        if i != 0 and len(users[i]) == 1:
            removed.append(key(p) + ':' + k)
            continue
        gaps.append(box(min(p['x'], p['x'] + dx*20) if dx else p['x'] - g, min(p['y'], p['y'] + dy*20) if dy else p['y'] - g,
                        max(p['x'], p['x'] + dx*20) if dx else p['x'] + g, max(p['y'], p['y'] + dy*20) if dy else p['y'] + g))
    seg = ring[key(p)].difference(unary_union(gaps)) if gaps else ring[key(p)]
    thermal += [list(t.coords) for t in (seg.geoms if hasattr(seg, 'geoms') else [seg]) if t.length > 0.05]
if removed:
    print('袋へのスポークを外した:', removed)

# ---- GND のつながり（実際に彫る溝の形で判定。幅 0.6mm 未満のくびれはつながっていないとみなす） ----
cut = unary_union([LineString(q).buffer(R, 12) for q in iso + thermal] + holes_geo)
pl = box(0, 0, W, H).difference(cut).buffer(-0.3, 8).buffer(0.3, 8)
regs = list(pl.geoms) if pl.geom_type == 'MultiPolygon' else [pl]
groups = {}
for p in gnd:
    pg = padgeom(p)
    groups.setdefault(next((i for i, r in enumerate(regs) if r.intersects(pg)), -1000 - len(groups)), []).append(p)
for ref in a.tied:
    ks = [k for k, v in groups.items() if any(p['ref'] == ref for p in v)]
    for k in ks[1:]:
        groups[ks[0]] += groups.pop(k)
if len(groups) > 1:
    print('警告: GND が %d 個に分かれている: %s' % (len(groups), [[key(p) for p in v] for v in groups.values()]))
elif groups:
    print('GND は全パッドが1つにつながっている')

# ---- 穴（工具径より小さい穴は作れない） ----
holes = []
for p in pads:
    dia = p['hole'][1]
    assert dia > a.tool, '%s の穴が工具より細い' % key(p)
    if p['hole'][0] == 'R':
        r = dia/2 - R
        holes.append([(p['x'] + r*math.cos(2*math.pi*k/24), p['y'] + r*math.sin(2*math.pi*k/24)) for k in range(25)])
    else:
        L = p['hole'][2]
        holes.append(list(LineString([(p['x'] - (L - dia)/2, p['y']), (p['x'] + (L - dia)/2, p['y'])]).buffer(dia/2 - R, 12).exterior.coords))
for m in MH:
    r = m['d']/2 - R
    holes.append([(m['x'] + r*math.cos(2*math.pi*k/48), m['y'] + r*math.sin(2*math.pi*k/48)) for k in range(49)])

# ---- 外形（工具の外側。4辺の中央にタブ） ----
ol = [(-R, -R), (W + R, -R), (W + R, H + R), (-R, H + R), (-R, -R)]
TABW = 2.5
tabs = [((W/2, -R), 'x'), ((W/2, H + R), 'x'), ((-R, H/2), 'y'), ((W + R, H/2), 'y')]


def in_tab(x, y):
    for (tx, ty), ax in tabs:
        if ax == 'x' and abs(y - ty) < 1e-6 and abs(x - tx) < TABW/2 + R:
            return True
        if ax == 'y' and abs(x - tx) < 1e-6 and abs(y - ty) < TABW/2 + R:
            return True
    return False


# ---- 出力 ----
def MX(x):
    return W - x


def fmt(v):
    s = '%.3f' % v        # 必ず小数点を付ける（"Y10" は 0.010mm と解釈され、原点付近へ一直線に動く）
    return '0.000' if s == '-0.000' else s


def head():
    return ['G00 G21 G17 G90 G40 G49', 'G80', 'G94', 'G00 Z%s' % fmt(a.safe), 'S%d M03' % a.spindle]


def tail():
    return ['G00 Z%s' % fmt(a.safe + 3), 'M05', 'G00 X0.000 Y0.000', 'M30']


def order_nn(paths):      # 空走を減らす簡易な最近傍順
    rest, out, cur = paths[:], [], (0, 0)
    while rest:
        k = min(range(len(rest)), key=lambda i: math.dist(cur, rest[i][0]))
        out.append(rest.pop(k))
        cur = out[-1][-1]
    return out


iso_l = head()
for pth in order_nn(iso + thermal):
    x0, y0 = pth[0]
    iso_l += ['G00 Z%s' % fmt(a.safe), 'G00 X%s Y%s' % (fmt(MX(x0)), fmt(y0)), 'G01 Z%s F%d' % (fmt(a.iso_z), a.plunge_f)]
    for i, (x, y) in enumerate(pth[1:]):
        iso_l.append('G01 X%s Y%s%s' % (fmt(MX(x)), fmt(y), (' F%d' % a.iso_f) if i == 0 else ''))
    iso_l.append('G00 Z%s' % fmt(a.safe))
iso_l += tail()

hole_l = head()
for pth in order_nn(holes):
    z = 0.0
    hole_l += ['G00 Z%s' % fmt(a.safe), 'G00 X%s Y%s' % (fmt(MX(pth[0][0])), fmt(pth[0][1])), 'G01 Z%s F%d' % (fmt(0.2), a.plunge_f)]
    while z > THRU + 1e-6:
        z = max(z - a.step, THRU)
        hole_l.append('G01 Z%s F%d' % (fmt(z), a.plunge_f))
        for i, (x, y) in enumerate(pth[1:]):
            hole_l.append('G01 X%s Y%s%s' % (fmt(MX(x)), fmt(y), (' F%d' % a.hole_f) if i == 0 else ''))
    hole_l.append('G00 Z%s' % fmt(a.safe))
hole_l += tail()

seg = []
for (x1, y1), (x2, y2) in zip(ol, ol[1:]):
    n = max(1, int(math.dist((x1, y1), (x2, y2))/0.25))
    seg += [(x1 + (x2 - x1)*k/n, y1 + (y2 - y1)*k/n) for k in range(n)]
seg.append(ol[-1])
out_l = head() + ['G00 X%s Y%s' % (fmt(MX(seg[0][0])), fmt(seg[0][1])), 'G01 Z%s F%d' % (fmt(0.2), a.plunge_f)]
z = 0.0
while z > THRU + 1e-6:
    z = max(z - a.step, THRU)
    out_l.append('G01 Z%s F%d' % (fmt(z if not in_tab(*seg[0]) else max(z, TAB_Z)), a.plunge_f))
    last = None
    for i, (x, y) in enumerate(seg[1:]):
        zz = max(z, TAB_Z) if in_tab(x, y) else z
        if zz != last:
            out_l.append('G01 Z%s' % fmt(zz))
            last = zz
        out_l.append('G01 X%s Y%s%s' % (fmt(MX(x)), fmt(y), (' F%d' % a.out_f) if i == 0 else ''))
out_l += tail()


def save(name, lines):
    for l in lines:
        assert not re.search(r'[XYZ]-?\d+(?![\d.])', l), '小数点のない座標: ' + l
        assert '(' not in l and ';' not in l, 'コメント: ' + l
        assert not re.search(r'\bM0?[01](?!\d)', l), 'M0/M1: ' + l
    with open(os.path.join(a.out, name), 'w', newline='\r\n') as f:
        f.write('\n'.join(lines + ['%']) + '\n')


save('01_isolation.ncd', iso_l)
save('02_holes.ncd', hole_l)
save('03_outline.ncd', out_l)
NH, NT = len(head()), len(tail())
save('00_all_in_one.ncd', head() + iso_l[NH:-NT] + hole_l[NH:-NT] + out_l[NH:-NT] + tail())
print('出力: %s （基板 %.1f x %.1f mm、板厚 %.2f、絶縁 Z%s、貫通 Z%s、タブ Z%s）' % (a.out, W, H, a.thickness, fmt(a.iso_z), fmt(THRU), fmt(TAB_Z)))

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 8*H/W))
    for n, g in cu.items():
        for gg in (g.geoms if g.geom_type == 'MultiPolygon' else [g]):
            xs, ys = gg.exterior.xy
            ax.fill([MX(x) for x in xs], ys, color='#d08030' if n != 'GND' else '#c0a040', alpha=.6)
    for paths, c in ((iso, 'b-'), (thermal, 'm-'), (holes, 'r-')):
        for pth in paths:
            ax.plot([MX(x) for x, y in pth], [y for x, y in pth], c, lw=.6)
    ax.plot([MX(x) for x, y in ol], [y for x, y in ol], 'k-', lw=.8)
    for (tx, ty), _ in tabs:
        ax.plot(MX(tx), ty, 'gs', ms=5)
    ax.set_title('Copper side (machining view, mirrored)  %.1f x %.1f mm' % (W, H), fontsize=8)
    ax.set_aspect('equal')
    ax.grid(alpha=.2)
    plt.savefig(os.path.join(a.out, 'toolpath_preview.png'), dpi=120, bbox_inches='tight')
except ImportError:
    print('matplotlib が無いのでプレビューは省略')
