"""
「5分以上／10分以上の遅延が発生している場所」ヒートマップ。

検証済みの stop_events（停留所での実到着遅延）を土台にする。各停留所での
「その停留所に閾値以上遅れて到着した割合（発生率）」を停留所位置に置き、
ガウスぼかしで面的なヒートマップにする。遅延の大きさではなく "そこで大遅延が
どれくらいの頻度で起きるか" を示す。発生率なので系統の運行本数差に依らない。

per-GPS点の gt_delay は上側の裾で過大報告するため使わず、停留所到着遅延を使う
（stop_events: gt=実到着遅延 delay_sec / op=停留所通過時点の運用遅延 op_delay_sec）。

データ:
  gt  = 正解データ（停留所実到着遅延, 全期間 4/23-5/30）
  op  = 最終バージョンの運用データ（停留所通過時点の運用遅延, 安定期 5/10-5/30）
閾値: 5分(300s) / 10分(600s)。人工物除去のため 1800s(30分) 超は計数しない。

出力: analysis/out/figures/late_heatmap/{gt,op}_{5min,10min}.png
"""
import os
import sys
import math

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
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "late_heatmap")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

CELL_M = 30.0
SMOOTH_SIGMA_CELLS = 5.0     # ≒150m。停留所を面的につなぐ
MIN_DENSITY = 1.2            # ぼかし後の件数密度マスク
ARTIFACT_CAP = 1800.0       # 30分超は人工物として計数しない
# 閾値ごとの表示上限（発生率の色スケール）
VMAX = {300.0: 0.50, 600.0: 0.25}
THRESH = {"5min": 300.0, "10min": 600.0}
# (遅延列, 日付条件, ラベル)
SOURCES = {
    "gt": ("delay_sec", "date BETWEEN '2026-04-23' AND '2026-05-30'", "正解データ（停留所到着遅延）/ 4-5月"),
    "op": ("op_delay_sec", "date BETWEEN '2026-05-10' AND '2026-05-30' AND op_delay_sec IS NOT NULL",
           "最終版 運用データ / 5-10〜5-30"),
}


def _gauss_blur(a, sigma):
    radius = max(1, int(3 * sigma))
    x = np.arange(-radius, radius + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2)); k /= k.sum()
    b = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, a)
    b = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, b)
    return b


def fetch(src):
    dcol, cond, _ = SOURCES[src]
    sql = f"""
      SELECT stop_lat AS lat, stop_lon AS lon, {dcol} AS delay
      FROM `{PROJECT}.bus_gps_data.stop_events`
      WHERE stop_lat IS NOT NULL AND {dcol} IS NOT NULL AND {cond}
    """
    rows = list(client.query(sql).result())
    lat = np.array([float(r["lat"]) for r in rows])
    lon = np.array([float(r["lon"]) for r in rows])
    delay = np.array([float(r["delay"]) for r in rows])
    return lat, lon, delay


def grid_extent(pc):
    xs, ys = [], []
    for pts in pc["shapes"].values():
        xs += [p["lon"] for p in pts]; ys += [p["lat"] for p in pts]
    m = 0.004
    return min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m


def make(src, thr_label, lat, lon, delay, extent, pc):
    thr = THRESH[thr_label]
    lon_min, lon_max, lat_min, lat_max = extent
    lat0 = 0.5 * (lat_min + lat_max)
    dlon = CELL_M / (111000.0 * math.cos(math.radians(lat0)))
    dlat = CELL_M / 111000.0
    nx = int((lon_max - lon_min) / dlon) + 1
    ny = int((lat_max - lat_min) / dlat) + 1
    ix = np.clip(((lon - lon_min) / dlon).astype(int), 0, nx - 1)
    iy = np.clip(((lat - lat_min) / dlat).astype(int), 0, ny - 1)

    total = np.zeros((ny, nx))
    late = np.zeros((ny, nx))
    is_late = ((delay >= thr) & (delay <= ARTIFACT_CAP)).astype(float)
    np.add.at(total, (iy, ix), 1.0)
    np.add.at(late, (iy, ix), is_late)

    btot = _gauss_blur(total, SMOOTH_SIGMA_CELLS)
    blate = _gauss_blur(late, SMOOTH_SIGMA_CELLS)
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = blate / btot
    grid = np.ma.masked_where(btot < MIN_DENSITY, rate)

    fig, ax = plt.subplots(figsize=(10, 9.2))
    for pts in pc["shapes"].values():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#d5d5d5", lw=1.2, zorder=1)
    vmax = VMAX[thr]
    im = ax.imshow(grid * 100.0, extent=[lon_min, lon_max, lat_min, lat_max],
                   origin="lower", cmap="YlOrRd", vmin=0, vmax=vmax * 100.0,
                   interpolation="bilinear", zorder=2, alpha=0.9,
                   aspect=1.0 / math.cos(math.radians(lat0)))
    cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    minute = "5分" if thr_label == "5min" else "10分"
    cbar.set_label(f"{minute}以上遅延の発生率 [%]（濃いほど高頻度）")
    _, _, src_label = SOURCES[src]
    ax.set_title(f"{minute}以上の遅延が発生している場所（{src_label}）", fontsize=13)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"{src}_{thr_label}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    vis = int((~grid.mask).sum()) if np.ma.is_masked(grid) else grid.size
    return path, vis, len(lat), float(is_late.sum())


def main():
    pc = G.load_precomputed()
    extent = grid_extent(pc)
    for src in ["gt", "op"]:
        lat, lon, delay = fetch(src)
        for tl in ["5min", "10min"]:
            path, vis, n, nlate = make(src, tl, lat, lon, delay, extent, pc)
            print(f"{src} {tl}: {os.path.basename(path)} cells={vis} points={n} late_events={int(nlate)}")


if __name__ == "__main__":
    main()
