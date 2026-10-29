"""
停留所ベースの路線遅延マップ（往路/復路を正しく分離した版）。

背景:
  route_delay_map.py は GPS点を1本の代表shapeへ投影し距離ビンで着色するため、
  往復が同じ道を通る区間で往路・復路の遅延が混ざり分離できなかった。
  一方 delay_by_stop（stop_events を停留所座標最接近で作り直した版）は、往路と
  復路の同名停留所を別 stop_id（例 瑞穂二本木 往=12_1 / 復=12_2）として
  正しく分離できている。本図はその停留所別・正解データ・平均遅延を地図に置く。

方式:
  - 各停留所を実座標にプロットし、平均到着遅れ[分]で緑→黄→赤に着色。
  - 往路の停留所はそのまま。復路の停留所だけ進行方向の右へ 28m ずらす
    （往復を視覚的に分離。地図上の誇張表示）。
  - 停留所を運行順につなぐ細線で経路の流れを示す。

出力: analysis/out/figures/route_delay/gt_stopwise_route{R}.png
"""
import os
import sys
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
import delay_by_stop as D   # rep_trips, rep_order, agg, ROUTE_NAME, MIN_SAMPLES

OFFSET_M = 28.0
VMAX_MIN = 5.0             # 赤に振り切る平均遅延[分]
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "route_delay")
os.makedirs(OUTDIR, exist_ok=True)


def build_entries(order, agg_data, gtfs):
    """rep_order を運行順にたどり、各停留所の (座標, 平均遅延[分], 往/復) を作る。"""
    entries = []
    seen = {}
    for seq, stop_id, name, is_origin, is_terminal in order:
        role = "O" if is_origin else "T" if is_terminal else "M"
        if is_origin:
            leg = "out"
        elif is_terminal:
            leg = "ret"
        else:
            seen[name] = seen.get(name, 0) + 1
            leg = "out" if seen[name] == 1 else "ret"
        s = gtfs["stops"].get(stop_id, {})
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None:
            continue
        arr = agg_data.get((stop_id, role))
        delay = (float(np.mean(arr)) / 60.0) if arr and len(arr) >= D.MIN_SAMPLES else None
        entries.append({"name": name, "lat": lat, "lon": lon, "leg": leg, "delay": delay})
    return entries


def apply_offset(entries):
    """復路の停留所を進行方向の右へ OFFSET_M ずらした表示座標 (plat, plon) を付与。"""
    lat = np.array([e["lat"] for e in entries])
    lon = np.array([e["lon"] for e in entries])
    lat0 = float(lat.mean())
    mlat = 111000.0
    mlon = 111000.0 * math.cos(math.radians(lat0))
    x = lon * mlon
    y = lat * mlat
    n = len(entries)
    for i, e in enumerate(entries):
        if e["leg"] == "ret" and n >= 2:
            if i == 0:
                dx, dy = x[1] - x[0], y[1] - y[0]
            elif i == n - 1:
                dx, dy = x[-1] - x[-2], y[-1] - y[-2]
            else:
                dx, dy = x[i + 1] - x[i - 1], y[i + 1] - y[i - 1]
            norm = math.hypot(dx, dy) or 1.0
            nx, ny = dy / norm, -dx / norm          # 右手法線
            e["plat"] = (y[i] + ny * OFFSET_M) / mlat
            e["plon"] = (x[i] + nx * OFFSET_M) / mlon
        else:
            e["plat"], e["plon"] = e["lat"], e["lon"]
    return entries


def make_figure(rid, order, agg_data, gtfs, pc, rep_tid):
    entries = apply_offset(build_entries(order, agg_data, gtfs))
    norm = Normalize(vmin=0.0, vmax=VMAX_MIN)
    cmap = plt.get_cmap("RdYlGn_r")

    fig, ax = plt.subplots(figsize=(9.5, 9))
    # 背景: 全shapeを薄く
    for sid, pts in pc["shapes"].items():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#f2f2f2", lw=1, zorder=0)
    # 対象系統のshapeを薄灰で
    shp = gtfs["trips"].get(rep_tid, {}).get("shape_id")
    if shp in pc["shapes"]:
        pts = pc["shapes"][shp]
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#dddddd", lw=1.5, zorder=1)

    # 停留所を運行順につなぐ細線（往路実線・復路は破線で表示）
    px = [e["plon"] for e in entries]
    py = [e["plat"] for e in entries]
    ax.plot(px, py, "-", color="#bbbbbb", lw=1.0, zorder=2)

    # 停留所マーカー（色=平均遅延）。データ僅少は灰色。
    for e in entries:
        if e["delay"] is None:
            ax.plot(e["plon"], e["plat"], "o", ms=8, mfc="#cccccc",
                    mec="#888", mew=0.6, zorder=4)
            continue
        col = cmap(norm(max(0.0, e["delay"])))
        marker = "o" if e["leg"] == "out" else "s"   # 往=丸, 復=四角
        ax.plot(e["plon"], e["plat"], marker, ms=11, mfc=col,
                mec="#222", mew=0.7, zorder=5)

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("平均到着遅れ [分]  緑=定時 / 赤=大遅延")
    cbar.set_ticks([0, 1, 2, 3, 4, 5])

    ax.set_title(f"{D.ROUTE_NAME[rid]} 停留所別 到着遅延マップ（正解データ・平均値）\n"
                 f"○=往路（そのまま） / □=復路（進行方向右へ{OFFSET_M:.0f}mずらして表示）",
                 fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.autoscale(); ax.margins(0.06)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"gt_stopwise_route{rid}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, len(entries)


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
