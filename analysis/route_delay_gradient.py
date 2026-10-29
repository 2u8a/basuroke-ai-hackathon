"""
停留所ベースの路線遅延マップ ―― 連続グラデーション版（往路/復路を左14mで自動分離）。

方式:
  - delay_by_stop（stop_events を停留所座標最接近で作り直した版）の正解・平均遅延を使う。
    往路と復路の同名停留所は別 stop_id（例 瑞穂二本木 往=12_1 / 復=12_2）で分離済み。
  - 各停留所を「次の停留所への進行方向を向いて左へ 14m」ずらす。
    往路と復路は同じ道を逆向きに走るため、左ずらしで自動的に反対側へ分かれる
    （＝クルマが道の片側を走る要領。往路も復路も勝手にずれる）。
  - ずらした停留所列を線でつなぎ、平均遅延[分]で緑→黄→赤の連続グラデーション着色。

出力: analysis/out/figures/route_delay/gt_gradient_route{R}.png
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
import delay_by_stop as D   # rep_trips, rep_order, agg, ROUTE_NAME, MIN_SAMPLES

SHIFT_M = 14.0            # 進行方向左へのずらし量（往復合わせて約28m分離）
VMAX_MIN = 5.0           # 赤に振り切る平均遅延[分]
SUBSEG = 16              # 停留所間をこの数に分割して色を補間（滑らかなグラデーション）
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "route_delay")
os.makedirs(OUTDIR, exist_ok=True)


def build_entries(order, agg_data, gtfs):
    """rep_order を運行順にたどり、各停留所の (座標, 平均遅延[分]) を作る。"""
    entries = []
    seen = {}
    for seq, stop_id, name, is_origin, is_terminal in order:
        role = "O" if is_origin else "T" if is_terminal else "M"
        s = gtfs["stops"].get(stop_id, {})
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None:
            continue
        arr = agg_data.get((stop_id, role))
        delay = (float(np.mean(arr)) / 60.0) if arr and len(arr) >= D.MIN_SAMPLES else None
        entries.append({"name": name, "lat": lat, "lon": lon, "delay": delay})
    return entries


def shift_left(entries):
    """各停留所を「次の停留所への進行方向を向いて左」へ SHIFT_M ずらした
    表示座標 (slat[], slon[]) を返す。"""
    lat = np.array([e["lat"] for e in entries])
    lon = np.array([e["lon"] for e in entries])
    lat0 = float(lat.mean())
    mlat = 111000.0
    mlon = 111000.0 * math.cos(math.radians(lat0))
    x = lon * mlon
    y = lat * mlat
    n = len(entries)
    sx = np.zeros(n)
    sy = np.zeros(n)
    prev_dir = (1.0, 0.0)
    for i in range(n):
        if i < n - 1:
            dx, dy = x[i + 1] - x[i], y[i + 1] - y[i]   # 現停留所→次停留所
        else:
            dx, dy = x[i] - x[i - 1], y[i] - y[i - 1]
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            dx, dy = prev_dir                            # 停留所が同座標のときは前の向き
            norm = math.hypot(dx, dy) or 1.0
        prev_dir = (dx, dy)
        # 進行方向を向いて左 = 進行方向を +90度回転 = (-dy, dx)
        nx, ny = -dy / norm, dx / norm
        sx[i] = x[i] + nx * SHIFT_M
        sy[i] = y[i] + ny * SHIFT_M
    return sy / mlat, sx / mlon                          # slat, slon


def make_figure(rid, order, agg_data, gtfs, pc, rep_tid):
    entries = build_entries(order, agg_data, gtfs)
    slat, slon = shift_left(entries)
    n = len(entries)
    norm = Normalize(vmin=0.0, vmax=VMAX_MIN)
    cmap = plt.get_cmap("RdYlGn_r")

    base_segs, grad_segs, grad_cols = [], [], []
    for i in range(n - 1):
        p0 = (slon[i], slat[i])
        p1 = (slon[i + 1], slat[i + 1])
        base_segs.append([p0, p1])
        d0, d1 = entries[i]["delay"], entries[i + 1]["delay"]
        if d0 is None and d1 is None:
            continue                                     # データ僅少→ベースラインのみ
        a = d0 if d0 is not None else d1
        b = d1 if d1 is not None else d0
        for k in range(SUBSEG):
            t0, t1 = k / SUBSEG, (k + 1) / SUBSEG
            q0 = (slon[i] + (slon[i + 1] - slon[i]) * t0,
                  slat[i] + (slat[i + 1] - slat[i]) * t0)
            q1 = (slon[i] + (slon[i + 1] - slon[i]) * t1,
                  slat[i] + (slat[i + 1] - slat[i]) * t1)
            dm = a + (b - a) * (0.5 * (t0 + t1))
            grad_segs.append([q0, q1])
            grad_cols.append(cmap(norm(max(0.0, dm))))

    fig, ax = plt.subplots(figsize=(9.5, 9))
    # 背景: 全shapeを薄く
    for sid, pts in pc["shapes"].items():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#f2f2f2", lw=1, zorder=0)
    # ずらした経路のベースライン（細い薄灰。データ僅少区間もつながって見える）
    ax.add_collection(LineCollection(base_segs, colors=[(0.85, 0.85, 0.85, 1.0)],
                                     linewidths=2.0, zorder=2, capstyle="round"))
    # 遅延グラデーション（太線）
    ax.add_collection(LineCollection(grad_segs, colors=grad_cols,
                                     linewidths=6.0, zorder=3, capstyle="round"))

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("平均到着遅れ [分]  緑=定時 / 赤=大遅延")
    cbar.set_ticks([0, 1, 2, 3, 4, 5])

    ax.set_title(f"{D.ROUTE_NAME[rid]} 路線別 到着遅延グラデーション（正解データ・平均値）\n"
                 f"往路/復路は進行方向左へ{SHIFT_M:.0f}mずらして自動分離", fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.autoscale(); ax.margins(0.06)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"gt_gradient_route{rid}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, n


def main():
    gtfs = G.load_gtfs()
    pc = G.load_precomputed()
    rep = D.rep_trips()
    order = D.rep_order(rep)
    for rid in ["1", "2", "4"]:
        if rid not in order:
            continue
        agg_data = D.agg(rid)
        path, n = make_figure(rid, order[rid], agg_data, gtfs, pc, rep[rid])
        print(f"written {os.path.basename(path)}  stops={n}")


if __name__ == "__main__":
    main()
