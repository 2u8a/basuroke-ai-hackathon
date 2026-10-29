"""
ルート別 路線グラデーション遅延マップ。

要件:
  - 系統(route)ごとに1枚。
  - 遅延値は運用データ(tm_delay_sec) または 正解データ(gt_delay_sec)。
  - 停留所の点ではなく、路線ポリラインに沿った連続グラデーション（緑→黄→赤）。
  - 往路/復路が同じ道を通る区間は、進行方向の右側へ一定量オフセットして分離。
    （右側オフセットは逆走する往路・復路を自動的に反対側へ振り分ける＝交通図の定石。
     方向IDでは分離できない「単一shape内の往復」も分離できる。）
  - 往復誤認識などのエラーは外れ値トリムで除外（|delay|>15分 を除去 + ビン内MAD)。

位置は運用値を信用せず各GPS点を代表shapeへ独立再投影して求める
（運用の往復かぶり誤りが位置に混入しないように）。

使い方: python route_delay_map.py <source: op|gt> <period: full|stable>
出力: analysis/out/figures/route_delay/<source>_<period>_route<R>.png
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
from matplotlib.collections import LineCollection
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

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
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures", "route_delay")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

# --- パラメータ ---
BIN_M = 100.0            # 距離ビン幅
OFFSET_M = 28.0          # 右側オフセット量（往路/復路を視認できる量。地図は誇張表示）
OUTLIER_ABS_S = 900.0    # |delay|>これ を外れ値として除外（往復誤認識など）
BIN_MAD_K = 3.5          # ビン内 median±K*MAD を外れ値除外
MIN_BIN_PTS = 8          # このビン点数未満は灰色マスク
ONROUTE_MAX_OFF = 80.0   # 経路からこの横ずれ超の点は除外
VMAX_S = 360.0           # 赤に振り切る遅延[s]
PERIODS = {
    "full": ("2026-04-23", "2026-05-30"),
    "stable": ("2026-05-10", "2026-05-30"),
}
ROUTE_NAME = {"1": "系統1", "2": "系統2", "3": "系統3", "4": "系統4", "5": "系統5"}


def dominant_shape_per_route(gtfs, pc):
    cnt = defaultdict(lambda: defaultdict(int))
    for tid, t in gtfs["trips"].items():
        if t["shape_id"] in pc["shapes"]:
            cnt[t["route_id"]][t["shape_id"]] += 1
    out = {}
    for rid, sh in cnt.items():
        out[rid] = max(sh, key=sh.get)
    return out


def trips_of_shape(gtfs, shape_id):
    return {tid for tid, t in gtfs["trips"].items() if t["shape_id"] == shape_id}


def fetch_route_points(source, period):
    """
    正解が運行中と判定した点を系統ごとに集める。位置は代表shapeへ独立投影で求め、
    色の遅延値だけ source で切替える（op=tm_delay_sec, gt=gt_delay_sec）。
    戻り: route_id -> [(lat, lon, delay), ...]
    """
    s, e = PERIODS[period]
    delay_col = "tm_delay_sec" if source == "op" else "gt_delay_sec"
    sql = f"""
      SELECT gt_route_id AS route_id, lat, lon, {delay_col} AS delay
      FROM `{PROJECT}.bus_gps_data.gps_eval`
      WHERE lat IS NOT NULL AND lon IS NOT NULL
        AND jst_date BETWEEN '{s}' AND '{e}'
        AND gt_in_service AND gt_confident AND gt_route_id IS NOT NULL
        AND {delay_col} IS NOT NULL
    """
    rows = list(client.query(sql).result())
    by_route = defaultdict(list)
    for r in rows:
        by_route[r["route_id"]].append((float(r["lat"]), float(r["lon"]), float(r["delay"])))
    return by_route


def offset_right(shape_pts, meters):
    """ポリラインを進行方向の右側へ meters 平行移動した (lat[], lon[]) を返す。"""
    lat = np.array([p["lat"] for p in shape_pts])
    lon = np.array([p["lon"] for p in shape_pts])
    lat0 = lat.mean()
    mlat = 111000.0
    mlon = 111000.0 * math.cos(math.radians(lat0))
    x = lon * mlon
    y = lat * mlat
    n = len(x)
    ox = np.zeros(n)
    oy = np.zeros(n)
    for i in range(n):
        # 進行方向（前後平均）
        if i == 0:
            dx, dy = x[1] - x[0], y[1] - y[0]
        elif i == n - 1:
            dx, dy = x[-1] - x[-2], y[-1] - y[-2]
        else:
            dx, dy = x[i + 1] - x[i - 1], y[i + 1] - y[i - 1]
        norm = math.hypot(dx, dy) or 1.0
        # 右手法線 = 進行方向を -90度回転 = (dy, -dx)
        nx, ny = dy / norm, -dx / norm
        ox[i] = x[i] + nx * meters
        oy[i] = y[i] + ny * meters
    return oy / mlat, ox / mlon  # lat, lon


def bin_delays(shape_pts, points, shape_id, pc):
    """各点を代表shapeへ独立投影し距離ビンごとの遅延中央値を返す。
    points: [(lat,lon,delay), ...]。ループ路線は自己重複しないため各点が一意に定まる。"""
    si = G.ShapeIndex(shape_pts)
    lats = np.array([p[0] for p in points])
    lons = np.array([p[1] for p in points])
    delays = np.array([p[2] for p in points])
    off, dist_along = si.project_many(lats, lons)
    keep = (off < ONROUTE_MAX_OFF) & (np.abs(delays) <= OUTLIER_ABS_S)
    dist_along = dist_along[keep]
    delays = delays[keep]
    total = pc["shape_total_distance"][shape_id]
    nb = int(total // BIN_M) + 1
    bin_med = np.full(nb, np.nan)
    bin_n = np.zeros(nb, dtype=int)
    for b in range(nb):
        m = (dist_along >= b * BIN_M) & (dist_along < (b + 1) * BIN_M)
        d = delays[m]
        if len(d) == 0:
            continue
        # ビン内MADトリム
        med = np.median(d)
        mad = np.median(np.abs(d - med)) or 1.0
        d2 = d[np.abs(d - med) <= BIN_MAD_K * 1.4826 * mad]
        if len(d2) < MIN_BIN_PTS:
            bin_n[b] = len(d2)
            if len(d2) > 0:
                bin_med[b] = np.median(d2)
            continue
        bin_med[b] = np.median(d2)
        bin_n[b] = len(d2)
    return bin_med, bin_n, nb


def make_route_figure(rid, shape_id, shape_pts, points, n_pts, pc, source, period):
    bin_med, bin_n, nb = bin_delays(shape_pts, points, shape_id, pc)
    olat, olon = offset_right(shape_pts, OFFSET_M)

    # セグメントごとに、その中点の距離→ビン→色
    dist = np.array([p["dist_traveled"] for p in shape_pts])
    base_segs = []          # 全セグメント（薄いベースライン）
    data_segs = []          # データのあるセグメント（色付き上書き）
    data_cols = []
    norm = Normalize(vmin=0.0, vmax=VMAX_S)
    cmap = plt.get_cmap("RdYlGn_r")
    n_masked = 0
    for i in range(len(shape_pts) - 1):
        seg = [(olon[i], olat[i]), (olon[i + 1], olat[i + 1])]
        base_segs.append(seg)
        mid = 0.5 * (dist[i] + dist[i + 1])
        b = min(int(mid // BIN_M), nb - 1)
        if bin_n[b] < MIN_BIN_PTS or np.isnan(bin_med[b]):
            n_masked += 1
        else:
            data_segs.append(seg)
            data_cols.append(cmap(norm(max(0.0, bin_med[b]))))

    fig, ax = plt.subplots(figsize=(9.5, 9))
    # 背景に全shapeを薄く
    for sid, pts in pc["shapes"].items():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                "-", color="#f0f0f0", lw=1, zorder=0)
    # 対象shapeのベースライン（細い薄灰）
    ax.add_collection(LineCollection(base_segs, colors=[(0.82, 0.82, 0.82, 1.0)],
                                     linewidths=2.5, zorder=2, capstyle="round"))
    # データのある区間を太く色付きで上書き
    ax.add_collection(LineCollection(data_segs, colors=data_cols,
                                     linewidths=6.5, zorder=3, capstyle="round"))

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("遅延（中央値）[秒]  緑=定時 / 赤=大遅延")
    cbar.set_ticks([0, 60, 120, 180, 240, 300, 360])

    src_label = "運用データ" if source == "op" else "正解データ"
    per_label = "全期間 4/23-5/30" if period == "full" else "安定期 5/10-5/30"
    ax.set_title(f"{ROUTE_NAME.get(rid, rid)} 路線別 到着遅延グラデーション"
                 f"（{src_label} / {per_label}）\n"
                 f"往路・復路は進行方向右側に{OFFSET_M:.0f}mずらして描画", fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.autoscale()
    ax.margins(0.05)
    ax.grid(True, alpha=0.2)
    ax.text(0.01, 0.01, f"shape={shape_id}  対象点={n_pts}  灰色=データ僅少",
            transform=ax.transAxes, fontsize=8, color="#555")
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"{source}_{period}_route{rid}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, n_pts, n_masked


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "op"
    period = sys.argv[2] if len(sys.argv) > 2 else "full"
    gtfs = G.load_gtfs()
    pc = G.load_precomputed()
    dom = dominant_shape_per_route(gtfs, pc)
    by_route = fetch_route_points(source, period)

    for rid in sorted(dom):
        shape_id = dom[rid]
        shape_pts = pc["shapes"][shape_id]
        points = by_route.get(rid, [])
        n_pts = len(points)
        if n_pts < 30:
            print(f"route {rid}: skip (points={n_pts})")
            continue
        path, n, nm = make_route_figure(rid, shape_id, shape_pts, points, n_pts, pc, source, period)
        print(f"route {rid}: {os.path.basename(path)}  points={n} masked_segs={nm}")


if __name__ == "__main__":
    main()
