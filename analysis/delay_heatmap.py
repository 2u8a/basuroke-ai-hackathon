"""
遅延ヒートマップ — どこで遅延が発生しているかを面的に可視化。

地図を約35m四方のセルに区切り、各セルを通過した運行中GPS点の
平均遅延で着色する（淡黄→橙→赤 = 遅延が大きい）。
点数の少ないセルは透明（マスク）。全系統まとめ + 系統別を出力。

遅延源: gt=正解(gt_delay_sec, gt_confidentのみ) / op=運用(tm_delay_sec)。
人工物除去のため個別遅延は [-120, 1800] 秒にクリップして平均。

使い方: python delay_heatmap.py <gt|op>
出力: analysis/out/figures/heatmap/<src>_all.png, <src>_route{1,2,4}.png
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
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "heatmap")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

CELL_M = 30.0            # セル一辺
MIN_CELL_PTS = 6         # セル中央値を採る最小点数
SMOOTH_SIGMA_CELLS = 2.6 # ガウスぼかしのσ（セル数）≒ 80m。道路周辺へ熱を広げる
MIN_DENSITY = 1.2        # ぼかし後の点数密度がこの値未満は透明（マスク）
DELAY_CLIP = (-120.0, 1800.0)
VMAX_S = 360.0           # 濃赤に振り切る中央遅延[s]（6分）
PERIOD = ("2026-04-23", "2026-05-30")
ROUTES = ["1", "2", "4"]
ROUTE_NAME = {"1": "系統1", "2": "系統2", "4": "系統4", "all": "全系統"}


def fetch(src):
    s, e = PERIOD
    dcol = "gt_delay_sec" if src == "gt" else "tm_delay_sec"
    cond = "gt_in_service AND gt_confident" if src == "gt" else "gt_in_service"
    sql = f"""
      SELECT gt_route_id AS route_id, lat, lon, {dcol} AS delay
      FROM `{PROJECT}.bus_gps_data.gps_eval`
      WHERE lat IS NOT NULL AND lon IS NOT NULL
        AND jst_date BETWEEN '{s}' AND '{e}'
        AND {cond} AND gt_route_id IS NOT NULL AND {dcol} IS NOT NULL
    """
    rows = list(client.query(sql).result())
    lat = np.array([float(r["lat"]) for r in rows])
    lon = np.array([float(r["lon"]) for r in rows])
    delay = np.clip(np.array([float(r["delay"]) for r in rows]), *DELAY_CLIP)
    route = np.array([r["route_id"] for r in rows])
    return lat, lon, delay, route


def _gauss_blur(a, sigma):
    """numpy だけの分離型ガウスぼかし。"""
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


def heatmap(lat, lon, delay, extent, title, path, pc):
    lon_min, lon_max, lat_min, lat_max = extent
    lat0 = 0.5 * (lat_min + lat_max)
    dlon = CELL_M / (111000.0 * math.cos(math.radians(lat0)))
    dlat = CELL_M / 111000.0
    nx = int((lon_max - lon_min) / dlon) + 1
    ny = int((lat_max - lat_min) / dlat) + 1
    ix = np.clip(((lon - lon_min) / dlon).astype(int), 0, nx - 1)
    iy = np.clip(((lat - lat_min) / dlat).astype(int), 0, ny - 1)
    # セルごとの中央値（平均は上側の裾に引っ張られ過大になるため中央値を使う）
    import pandas as pd
    cid = iy * nx + ix
    cnt = np.zeros(ny * nx)
    np.add.at(cnt, cid, 1)
    df = pd.DataFrame({"c": cid, "d": delay})
    med = df.groupby("c")["d"].median()
    med_grid = np.zeros(ny * nx)
    present = np.zeros(ny * nx)
    ok = med.index[cnt[med.index] >= MIN_CELL_PTS]
    med_grid[ok] = med.loc[ok].values
    present[ok] = 1.0
    med_grid = med_grid.reshape(ny, nx)
    present = present.reshape(ny, nx)
    cnt_grid = cnt.reshape(ny, nx)
    # 色: セル中央値を近傍で距離重み平均してなめらかに（中央値の重み付き平滑）
    bnum = _gauss_blur(med_grid * present, SMOOTH_SIGMA_CELLS)
    bden = _gauss_blur(present, SMOOTH_SIGMA_CELLS)
    with np.errstate(invalid="ignore"):
        mean = bnum / np.where(bden > 0, bden, np.nan)
    # マスク: 実点数のぼかし密度で（データのある道路周辺のみ表示）
    dens = _gauss_blur(cnt_grid, SMOOTH_SIGMA_CELLS)
    grid = np.ma.masked_where((dens < MIN_DENSITY) | np.isnan(mean), mean)

    fig, ax = plt.subplots(figsize=(10, 9.2))
    for pts in pc["shapes"].values():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#d5d5d5", lw=1.2, zorder=1)
    im = ax.imshow(grid, extent=[lon_min, lon_max, lat_min, lat_max],
                   origin="lower", cmap="YlOrRd", vmin=0, vmax=VMAX_S,
                   interpolation="bilinear", zorder=2, alpha=0.9,
                   aspect=1.0 / math.cos(math.radians(lat0)))
    cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("平均遅延 [秒]（淡=定時 / 濃赤=大遅延）")
    cbar.set_ticks([0, 60, 120, 180, 240, 300, 360])
    ax.set_title(title, fontsize=13)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return int((~grid.mask).sum()) if np.ma.is_masked(grid) else grid.size


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "gt"
    pc = G.load_precomputed()
    lat, lon, delay, route = fetch(src)
    extent = grid_extent(pc)
    src_label = "正解データ" if src == "gt" else "運用データ"

    # 全系統
    p = os.path.join(OUTDIR, f"{src}_all.png")
    n = heatmap(lat, lon, delay, extent,
                f"遅延ヒートマップ 全系統（{src_label} / 4-5月）— どこで遅延が発生しているか",
                p, pc)
    print(f"all: {os.path.basename(p)} cells={n} points={len(lat)}")

    # 系統別
    for rid in ROUTES:
        m = route == rid
        if m.sum() < 200:
            print(f"route {rid}: skip (points={int(m.sum())})")
            continue
        p = os.path.join(OUTDIR, f"{src}_route{rid}.png")
        n = heatmap(lat[m], lon[m], delay[m], extent,
                    f"遅延ヒートマップ {ROUTE_NAME[rid]}（{src_label} / 4-5月）",
                    p, pc)
        print(f"route {rid}: {os.path.basename(p)} cells={n} points={int(m.sum())}")


if __name__ == "__main__":
    main()
