"""
路線遅延グラデーション ―― 道路形状(shape)追従版。往路/復路は左14mで自動分離。

route_delay_gradient.py は停留所を直線で結んでいたが、本版は実際の道路形状
（代表便の shape ポリライン）に沿ってグラデーションを乗せる。

方式:
  - 代表便の shape（駅→往路→折返し→復路→駅 の全ループ、道路に沿う）を使う。
  - 各 shape 点の遅延は、trip_progress の各停留所の距離位置(distance_m)と
    delay_by_stop の平均遅延から、shape 上距離で線形補間して与える。
    （往路の停留所は 12_1、復路は 12_2 と別 stop_id なので往復の遅延は分離済み。）
  - shape 全体を「進行方向を向いて左へ 14m」ずらす。shape は往路と復路で同じ道を
    逆向きに通るため、左ずらしで往路・復路が自動的に反対側へ分かれる。
  - ずらした shape を平均遅延[分]で緑→黄→赤の連続グラデーション着色。

出力: analysis/out/figures/route_delay/gt_gradient_road_route{R}.png
"""
import os
import sys
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
import delay_by_stop as D   # rep_trips, agg, ROUTE_NAME, MIN_SAMPLES

SHIFT_M = 14.0
VMAX_MIN = 5.0
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "route_delay")
os.makedirs(OUTDIR, exist_ok=True)


def stop_delay_profile(prog, agg_data):
    """trip_progress の各停留所について (shape上距離, 平均遅延[分]) を返す（有効値のみ）。"""
    n = len(prog)
    dists, delays = [], []
    for i, e in enumerate(prog):
        role = "O" if i == 0 else "T" if i == n - 1 else "M"
        arr = agg_data.get((e["stop_id"], role))
        if not arr or len(arr) < D.MIN_SAMPLES:
            continue
        dists.append(float(e["distance_m"]))
        delays.append(float(np.mean(arr)) / 60.0)
    return np.array(dists), np.array(delays)


def shift_left_shape(shape_pts):
    """shape を進行方向左へ SHIFT_M ずらした (slat[], slon[]) を返す。"""
    lat = np.array([p["lat"] for p in shape_pts])
    lon = np.array([p["lon"] for p in shape_pts])
    lat0 = float(lat.mean())
    mlat = 111000.0
    mlon = 111000.0 * math.cos(math.radians(lat0))
    x = lon * mlon
    y = lat * mlat
    n = len(shape_pts)
    sx = np.zeros(n)
    sy = np.zeros(n)
    prev = (1.0, 0.0)
    for i in range(n):
        if i < n - 1:
            dx, dy = x[i + 1] - x[i], y[i + 1] - y[i]
        else:
            dx, dy = x[i] - x[i - 1], y[i] - y[i - 1]
        nrm = math.hypot(dx, dy)
        if nrm < 1e-6:
            dx, dy = prev
            nrm = math.hypot(dx, dy) or 1.0
        prev = (dx, dy)
        nx, ny = -dy / nrm, dx / nrm            # 進行方向を向いて左
        sx[i] = x[i] + nx * SHIFT_M
        sy[i] = y[i] + ny * SHIFT_M
    return sy / mlat, sx / mlon


def make_figure(rid, shape_pts, prog, agg_data, pc):
    sdist, sdelay = stop_delay_profile(prog, agg_data)
    if len(sdist) < 2:
        return None, 0
    slat, slon = shift_left_shape(shape_pts)
    shp_dist = np.array([p["dist_traveled"] for p in shape_pts])
    # shape 上距離で各点の遅延を線形補間（範囲外は端で一定）
    pt_delay = np.interp(shp_dist, sdist, sdelay)

    norm = Normalize(vmin=0.0, vmax=VMAX_MIN)
    cmap = plt.get_cmap("RdYlGn_r")
    segs, cols = [], []
    for i in range(len(shape_pts) - 1):
        segs.append([(slon[i], slat[i]), (slon[i + 1], slat[i + 1])])
        dm = 0.5 * (pt_delay[i] + pt_delay[i + 1])
        cols.append(cmap(norm(max(0.0, dm))))

    fig, ax = plt.subplots(figsize=(9.5, 9))
    for sid, pts in pc["shapes"].items():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#f2f2f2", lw=1, zorder=0)
    ax.add_collection(LineCollection(segs, colors=cols, linewidths=6.0,
                                     zorder=3, capstyle="round"))

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("平均到着遅れ [分]  緑=定時 / 赤=大遅延")
    cbar.set_ticks([0, 1, 2, 3, 4, 5])

    ax.set_title(f"{D.ROUTE_NAME[rid]} 路線別 到着遅延グラデーション（正解データ・平均値）\n"
                 f"道路形状に沿って描画・往路/復路は進行方向左へ{SHIFT_M:.0f}mずらして自動分離",
                 fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.autoscale(); ax.margins(0.06)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"gt_gradient_road_route{rid}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, len(shape_pts)


def main():
    gtfs = G.load_gtfs()
    pc = G.load_precomputed()
    rep = D.rep_trips()
    for rid in ["1", "2", "4"]:
        tid = rep.get(rid)
        if not tid or tid not in pc["trip_progress"]:
            print(f"route {rid}: skip (no trip_progress)")
            continue
        shape_id = gtfs["trips"][tid]["shape_id"]
        if shape_id not in pc["shapes"]:
            print(f"route {rid}: skip (no shape)")
            continue
        agg_data = D.agg(rid)
        path, n = make_figure(rid, pc["shapes"][shape_id], pc["trip_progress"][tid], agg_data, pc)
        if path:
            print(f"written {os.path.basename(path)}  shape_pts={n}")


if __name__ == "__main__":
    main()
