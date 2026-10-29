"""
5分以上の遅延が発生した「地点」の分布図（系統1・2・4）。

- 遅延 300秒以上 の GPS 点を、その系統の代表 shape へ投影し実位置に散布。
- 往路（経路前半）＝青 / 復路（経路後半）＝赤 で色分けし、さらに進行方向の
  右側へ少しオフセットして往路・復路を空間的にも分離する。
- 遅延源: gt=正解(gt_delay_sec) / op=運用(tm_delay_sec)。人工物除外のため
  300〜1800秒（5〜30分）に限定し、正解は gt_confident のみ。

使い方: python late_delay_map.py <gt|op>
出力: analysis/out/figures/late_delay/<src>_route{1,2,4}.png
"""
import os
import sys
import math
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
from google.cloud import bigquery

for cand in ["Yu Gothic", "Meiryo", "MS Gothic"]:
    try:
        font_manager.findfont(cand, fallback_to_default=False)
        plt.rcParams["font.family"] = cand
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

PROJECT = "mizuho-basuroke-f370e"
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "late_delay")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

LATE_MIN_S = 300.0        # 5分
LATE_MAX_S = 1800.0       # 30分（超は人工物として除外）
ONROUTE_MAX_OFF = 80.0
OFFSET_M = 22.0           # 往路/復路の空間分離量（右側）
ROUTES = ["1", "2", "4"]
ROUTE_NAME = {"1": "系統1", "2": "系統2", "4": "系統4"}
PERIOD = ("2026-04-23", "2026-05-30")


def dominant_shape(gtfs, pc):
    cnt = defaultdict(lambda: defaultdict(int))
    for tid, t in gtfs["trips"].items():
        if t["shape_id"] in pc["shapes"]:
            cnt[t["route_id"]][t["shape_id"]] += 1
    return {rid: max(sh, key=sh.get) for rid, sh in cnt.items()}


def fetch_late(src):
    s, e = PERIOD
    dcol = "gt_delay_sec" if src == "gt" else "tm_delay_sec"
    cond = "gt_in_service AND gt_confident" if src == "gt" else "gt_in_service"
    sql = f"""
      SELECT gt_route_id AS route_id, lat, lon, {dcol} AS delay
      FROM `{PROJECT}.bus_gps_data.gps_eval`
      WHERE lat IS NOT NULL AND lon IS NOT NULL
        AND jst_date BETWEEN '{s}' AND '{e}'
        AND {cond} AND gt_route_id IS NOT NULL
        AND {dcol} BETWEEN {LATE_MIN_S} AND {LATE_MAX_S}
    """
    by_route = defaultdict(list)
    for r in client.query(sql).result():
        by_route[r["route_id"]].append((float(r["lat"]), float(r["lon"]), float(r["delay"])))
    return by_route


def seg_offsets(shape_pts, dist_along, meters):
    """各 dist_along の位置で、進行方向右側へ meters ずらす (dlat[], dlon[]) を返す。"""
    dv = np.array([p["dist_traveled"] for p in shape_pts])
    lat = np.array([p["lat"] for p in shape_pts])
    lon = np.array([p["lon"] for p in shape_pts])
    lat0 = lat.mean()
    mlat = 111000.0
    mlon = 111000.0 * math.cos(math.radians(lat0))
    seg = np.clip(np.searchsorted(dv, dist_along) - 1, 0, len(shape_pts) - 2)
    dx = (lon[seg + 1] - lon[seg]) * mlon
    dy = (lat[seg + 1] - lat[seg]) * mlat
    norm = np.hypot(dx, dy)
    norm[norm == 0] = 1.0
    nx = dy / norm      # 右手法線 = (dy,-dx)
    ny = -dx / norm
    dlon = nx * meters / mlon
    dlat = ny * meters / mlat
    return dlat, dlon


def make_fig(rid, shape_id, shape_pts, points, pc, src):
    si = G.ShapeIndex(shape_pts)
    lats = np.array([p[0] for p in points])
    lons = np.array([p[1] for p in points])
    delays = np.array([p[2] for p in points])
    off, dist_along = si.project_many(lats, lons)
    keep = off < ONROUTE_MAX_OFF
    lats, lons, delays, dist_along = lats[keep], lons[keep], delays[keep], dist_along[keep]

    total = pc["shape_total_distance"][shape_id]
    outbound = dist_along < total / 2.0    # 前半=往路
    dlat, dlon = seg_offsets(shape_pts, dist_along, OFFSET_M)
    # 往路は右、復路は逆側（左）にオフセットして分離を強調
    sign = np.where(outbound, 1.0, -1.0)
    plat = lats + dlat * sign
    plon = lons + dlon * sign

    fig, ax = plt.subplots(figsize=(9.5, 9))
    for sid, pts in pc["shapes"].items():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#e8e8e8", lw=1, zorder=0)
    ax.plot([p["lon"] for p in shape_pts], [p["lat"] for p in shape_pts],
            "-", color="#c8c8c8", lw=2, zorder=1)
    n_out = int(outbound.sum()); n_ret = int((~outbound).sum())
    ax.scatter(plon[outbound], plat[outbound], s=22, c="#1f77b4", alpha=0.35,
               edgecolors="none", zorder=3, label=f"往路(経路前半) {n_out}件")
    ax.scatter(plon[~outbound], plat[~outbound], s=22, c="#d62728", alpha=0.35,
               edgecolors="none", zorder=3, label=f"復路(経路後半) {n_ret}件")

    src_label = "正解データ" if src == "gt" else "運用データ"
    ax.set_title(f"{ROUTE_NAME[rid]} 5分以上の遅延が発生した地点の分布"
                 f"（{src_label} / 4-5月）\n"
                 f"往路=青 / 復路=赤、進行方向にずらして分離", fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.autoscale(); ax.margins(0.05)
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.text(0.01, 0.01, f"shape={shape_id}  5分以上遅延点={len(delays)}  (5-30分)",
            transform=ax.transAxes, fontsize=8, color="#555")
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"{src}_route{rid}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, len(delays), n_out, n_ret


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "gt"
    gtfs = G.load_gtfs(); pc = G.load_precomputed()
    dom = dominant_shape(gtfs, pc)
    by_route = fetch_late(src)
    for rid in ROUTES:
        pts = by_route.get(rid, [])
        if len(pts) < 5:
            print(f"route {rid}: skip (late points={len(pts)})")
            continue
        path, n, no, nr = make_fig(rid, dom[rid], pc["shapes"][dom[rid]], pts, pc, src)
        print(f"route {rid}: {os.path.basename(path)}  late={n} (往路{no}/復路{nr})")


if __name__ == "__main__":
    main()
