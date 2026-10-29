"""
GPS精度ヒートマップ — 運行ルート上でどこGPSが弱くなりやすいかを面的に可視化。

各GPS点の accuracy（推定誤差[m]、大きいほど不確か）を約30m四方のセルに集計し、
セル中央値で着色する（淡=精度良好 / 赤=GPSが弱い）。系統では分けず全体1枚。

対象点: 回送・車庫(out_of_service_area)を除く全点（走行中の受理点＋低精度で
弾かれた点を含む＝弱いスポットも反映）。丘陵地でGPSが弱くなる仮説の確認に使う。

出力: analysis/out/figures/heatmap/gps_accuracy.png
"""
import os
import sys
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

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
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "heatmap")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

CELL_M = 30.0
MIN_CELL_PTS = 6
SMOOTH_SIGMA_CELLS = 2.6
MIN_DENSITY = 1.2
ACC_CLIP = (0.0, 200.0)     # 極端な外れ値を抑える
VMIN, VMAX = 20.0, 55.0     # 淡=20m(良好) / 赤=55m以上(弱い)
PERIOD = ("2026-04-23", "2026-05-30")


def fetch():
    s, e = PERIOD
    sql = f"""
      SELECT lat, lon, accuracy
      FROM `{PROJECT}.bus_gps_data.gps_flat`
      WHERE lat IS NOT NULL AND lon IS NOT NULL AND accuracy IS NOT NULL
        AND jst_date BETWEEN '{s}' AND '{e}'
        AND (reject_reason IS NULL OR reject_reason <> 'out_of_service_area')
    """
    rows = list(client.query(sql).result())
    lat = np.array([float(r["lat"]) for r in rows])
    lon = np.array([float(r["lon"]) for r in rows])
    acc = np.clip(np.array([float(r["accuracy"]) for r in rows]), *ACC_CLIP)
    return lat, lon, acc


def _gauss_blur(a, sigma):
    radius = max(1, int(3 * sigma))
    x = np.arange(-radius, radius + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2))
    k /= k.sum()
    b = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, a)
    b = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, b)
    return b


def grid_extent(pc):
    xs, ys = [], []
    for pts in pc["shapes"].values():
        xs += [p["lon"] for p in pts]
        ys += [p["lat"] for p in pts]
    mx = 0.004
    return min(xs) - mx, max(xs) + mx, min(ys) - mx, max(ys) + mx


def heatmap(lat, lon, acc, extent, pc):
    lon_min, lon_max, lat_min, lat_max = extent
    # 範囲内のみ（縁への積み上がりを防ぐ）
    inside = (lon >= lon_min) & (lon <= lon_max) & (lat >= lat_min) & (lat <= lat_max)
    lat, lon, acc = lat[inside], lon[inside], acc[inside]
    lat0 = 0.5 * (lat_min + lat_max)
    dlon = CELL_M / (111000.0 * math.cos(math.radians(lat0)))
    dlat = CELL_M / 111000.0
    nx = int((lon_max - lon_min) / dlon) + 1
    ny = int((lat_max - lat_min) / dlat) + 1
    ix = np.clip(((lon - lon_min) / dlon).astype(int), 0, nx - 1)
    iy = np.clip(((lat - lat_min) / dlat).astype(int), 0, ny - 1)
    cid = iy * nx + ix
    cnt = np.zeros(ny * nx)
    np.add.at(cnt, cid, 1)
    df = pd.DataFrame({"c": cid, "a": acc})
    med = df.groupby("c")["a"].median()
    med_grid = np.zeros(ny * nx)
    present = np.zeros(ny * nx)
    ok = med.index[cnt[med.index] >= MIN_CELL_PTS]
    med_grid[ok] = med.loc[ok].values
    present[ok] = 1.0
    med_grid = med_grid.reshape(ny, nx)
    present = present.reshape(ny, nx)
    cnt_grid = cnt.reshape(ny, nx)
    bnum = _gauss_blur(med_grid * present, SMOOTH_SIGMA_CELLS)
    bden = _gauss_blur(present, SMOOTH_SIGMA_CELLS)
    with np.errstate(invalid="ignore"):
        mean = bnum / np.where(bden > 0, bden, np.nan)
    dens = _gauss_blur(cnt_grid, SMOOTH_SIGMA_CELLS)
    grid = np.ma.masked_where((dens < MIN_DENSITY) | np.isnan(mean), mean)

    fig, ax = plt.subplots(figsize=(10, 9.2))
    for pts in pc["shapes"].values():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#d5d5d5", lw=1.2, zorder=1)
    im = ax.imshow(grid, extent=[lon_min, lon_max, lat_min, lat_max],
                   origin="lower", cmap="YlOrRd", vmin=VMIN, vmax=VMAX,
                   interpolation="bilinear", zorder=2, alpha=0.9,
                   aspect=1.0 / math.cos(math.radians(lat0)))
    cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("GPS精度（誤差の中央値）[m]  淡=良好 / 赤=弱い")
    cbar.set_ticks([20, 30, 40, 50])
    ax.set_title("GPS精度ヒートマップ 全系統（4-5月）— どこでGPSが弱くなりやすいか\n"
                 "赤いほどGPSが不確か（推定誤差が大きい）", fontsize=13)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    path = os.path.join(OUTDIR, "gps_accuracy.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    n_cells = int((~grid.mask).sum()) if np.ma.is_masked(grid) else grid.size
    return path, n_cells, len(lat)


def main():
    pc = G.load_precomputed()
    lat, lon, acc = fetch()
    path, n_cells, n_pts = heatmap(lat, lon, acc, grid_extent(pc), pc)
    print(f"written {os.path.basename(path)}  cells={n_cells} points={n_pts}")


if __name__ == "__main__":
    main()
