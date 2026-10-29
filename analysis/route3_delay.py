"""
系統3（石畑・殿ケ谷コース）の停留所別到着遅れ。

背景:
  系統3は経路形状 R3_shape 自体は実軌跡とよく合う（本物の系統3走行に対し
  横ずれ中央値21m）が、後知恵の正解便推定 ground_truth.infer が共有区間
  （箱根ケ崎駅〜瑞穂町役場）の点を系統1に吸収してしまい、系統3の run を
  一度も生成できていなかった。運用側推定(est_trip_id)も系統3の過半を系統1と
  混同しており（壊れている前提のデータ）、便割当の根拠に使えない。

方法（1台構成を活用）:
  実証は1台運行なので、ある時刻にバスがいる系統は1つに定まる。系統3の
  「固有停留所」（他系統に存在しない南部の停留所群）へ80m以内に接近した時刻
  クラスタを系統3走行とみなし、その前後を含む1周の GPS 点を切り出す
  （他系統は混ざり得ない）。各走行を運行時刻から系統3の便へ割当て、
  stop_events.py と同じ「停留所座標への時刻順ローカル最接近」で到着遅れを出す。

  ※ 系統1・2・4は正解便推定(ground_truth)ベース、系統3は本スクリプトの
    幾何同定ベースで、便割当の作り方が異なる点は報告書に明記すること。

出力: analysis/out/reliability/delay_by_stop_route3_{median,mean}.png
      analysis/out/reliability/_route3_delay_table.txt
"""
import os
import sys
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from google.cloud import bigquery

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G

for cand in ["Yu Gothic", "Meiryo", "MS Gothic"]:
    try:
        font_manager.findfont(cand, fallback_to_default=False)
        plt.rcParams["font.family"] = cand
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

PROJECT = "mizuho-basuroke-f370e"
OUT = os.path.join(os.path.dirname(__file__), "out", "reliability")
JST = timezone(timedelta(hours=9))
UNIQ_TOL_M = 80.0     # 固有停留所への接近判定
STOP_TOL_M = 150.0    # 停留所座標への到着判定
RUN_GAP_SEC = 900     # 固有区間接近がこの秒数途切れたら別走行
WIN_PAD_SEC = 600     # 走行窓を接近区間の前後に広げる幅
WIN_EARLY_SEC = 600
WIN_LATE_SEC = 2700
MIN_SAMPLES = 5
client = bigquery.Client(project=PROJECT)


def haversine(a, b, c, d):
    p = math.pi / 180
    return 2 * 6_371_000 * math.asin(math.sqrt(
        math.sin((c - a) * p / 2) ** 2
        + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2))


def tod_sec(ts):
    dt = datetime.fromtimestamp(ts, JST)
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def route3_stops(g):
    """route_id -> stop_id集合、系統3固有 stop_id集合。"""
    st = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(G.GTFS_DIR, "stop_times.txt"), encoding="utf-8")):
        st[r["trip_id"]].append(r["stop_id"])
    rs = defaultdict(set)
    for tid, t in g["trips"].items():
        for sid in st[tid]:
            rs[t["route_id"]].add(sid)
    uniq = rs["3"] - (rs["1"] | rs["2"] | rs["4"] | rs["5"])
    return uniq


def load_area_points(g, prog):
    """系統3エリア（全停留所bbox）の全GPS点を時系列で。"""
    la = [g["stops"][e["stop_id"]]["lat"] for e in prog]
    lo = [g["stops"][e["stop_id"]]["lon"] for e in prog]
    q = f"""SELECT ts_unix, CAST(jst_date AS STRING) d, lat, lon
            FROM `{PROJECT}.bus_gps_data.gps_flat`
            WHERE lat BETWEEN {min(la)-0.003} AND {max(la)+0.003}
              AND lon BETWEEN {min(lo)-0.003} AND {max(lo)+0.003}
            ORDER BY ts_unix"""
    return [(int(r["ts_unix"]), r["d"], float(r["lat"]), float(r["lon"]))
            for r in client.query(q).result()]


def identify_clusters(pts, ucoords):
    """固有停留所80m以内の接近時刻クラスタ = 系統3走行。(day, center_ts) を返す。"""
    hit = [(ts, d) for ts, d, lat, lon in pts
           if min(haversine(lat, lon, ula, ulo) for ula, ulo in ucoords) <= UNIQ_TOL_M]
    clusters = []
    s = p = sd = None
    for ts, d in hit:
        if s is None:
            s = ts; sd = d
        elif ts - p > RUN_GAP_SEC:
            clusters.append((sd, (s + p) // 2)); s = ts; sd = d
        p = ts
    if s is not None:
        clusters.append((sd, (s + p) // 2))
    return clusters


def assign_trip(day, center_ts, prog_by_trip, g):
    """走行を運行時刻から系統3の便に割当てる。曜日でservice、固有停留所の
    予定時刻中央に運行の中心時刻が最も近い便を選ぶ。"""
    wd = datetime.strptime(day, "%Y-%m-%d").weekday()
    service = "土休日" if wd >= 5 else "平日"
    center_tod = tod_sec(center_ts)
    best = None
    for tid, prog in prog_by_trip.items():
        if not tid.startswith(f"3_{service}_"):
            continue
        mid = prog[len(prog) // 2]["time_sec"]
        gap = abs(center_tod - mid)
        if best is None or gap < best[0]:
            best = (gap, tid)
    return best[1] if best else None


def detect_stops(wp, prog, g):
    """stop_events.py と同じ谷cursor方式。始発除外。→ [(stop_id, seq, name, delay)]。"""
    tods = [t for _, t, _, _ in wp]
    lat = [la for _, _, la, _ in wp]
    lon = [lo for _, _, _, lo in wp]
    out = []
    cursor = 0
    for si_idx, e in enumerate(prog):
        s = g["stops"].get(e["stop_id"], {})
        slat, slon = s.get("lat"), s.get("lon")
        if slat is None:
            continue
        sched = int(e["time_sec"])
        bk, bd = -1, float("inf")
        k = cursor
        while k < len(wp) and tods[k] <= sched + WIN_LATE_SEC:
            if tods[k] >= sched - WIN_EARLY_SEC:
                dd = haversine(slat, slon, lat[k], lon[k])
                if dd < bd:
                    bd, bk = dd, k
                elif bd <= STOP_TOL_M and dd > bd * 1.5:
                    break
            k += 1
        if bk < 0 or bd > STOP_TOL_M:
            continue
        cursor = bk
        if si_idx == 0:
            continue  # 始発除外
        out.append((e["stop_id"], int(e["stop_sequence"]), s.get("name", "?"),
                    tods[bk] - sched))
    return out


def make_plot(stat, seqorder, byid, namemap):
    labels, vals = [], []
    seen = {}
    for sid in seqorder:
        arr = byid.get(sid)
        if not arr or len(arr) < MIN_SAMPLES:
            continue
        d = np.array(arr)
        nm = namemap[sid]
        seen[nm] = seen.get(nm, 0) + 1
        labels.append(nm if seen[nm] == 1 else f"{nm}(復)")
        vals.append((np.median(d) if stat == "median" else np.mean(d)) / 60.0)
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(8, 0.7 * len(labels) + 3), 6))
    ax.plot(x, vals, "-o", color="#ff7f0e", lw=2.2, ms=7)
    for xi, v in zip(x, vals):
        ax.annotate(f"{v:.1f}", (xi, v), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=8, color="#333")
    ax.axhline(0, color="gray", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    st = "中央値" if stat == "median" else "平均値"
    ax.set_ylabel("到着の遅れ [分]（プラス＝遅れ）")
    ax.set_title(f"系統3 停留所ごとの到着の遅れ（{st}）— 運行順（循環）\n"
                 f"（固有区間で幾何同定・1台構成前提。始発・終点駅は除外。プラスが遅れ）",
                 fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    path = os.path.join(OUT, f"delay_by_stop_route3_{stat}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    g = G.load_gtfs()
    pc = G.load_precomputed()
    prog_by_trip = {tid: prog for tid, prog in pc["trip_progress"].items()
                    if tid.startswith("3_")}
    rep = sorted(prog_by_trip)[0]
    rep_prog = prog_by_trip[rep]
    uniq = route3_stops(g)
    ucoords = [(g["stops"][s]["lat"], g["stops"][s]["lon"]) for s in uniq]

    pts = load_area_points(g, rep_prog)
    runs = identify_runs(pts, ucoords)

    byid = defaultdict(list)
    namemap, seqmap = {}, {}
    used = 0
    for day, center_ts, wp in runs:
        tid = assign_trip(day, center_ts, prog_by_trip, g)
        if not tid:
            continue
        det = detect_stops(wp, prog_by_trip[tid], g)
        if len(det) < 3:
            continue
        used += 1
        for sid, seq, nm, delay in det:
            byid[sid].append(delay)
            namemap[sid] = nm
            seqmap[sid] = seq

    seqorder = sorted(byid, key=lambda s: seqmap[s])
    # テーブル出力
    L = [f"走行同定={len(runs)}  便割当・停留所検出できた走行={used}  検出停留所={len(byid)}",
         "seq\tstop\tn\tmed分\tmean分"]
    for sid in seqorder:
        arr = np.array(byid[sid])
        L.append(f"{seqmap[sid]}\t{namemap[sid]}\tn={len(arr)}\t"
                 f"med={np.median(arr)/60:.1f}\tmean={np.mean(arr)/60:.1f}")
    open(os.path.join(OUT, "_route3_delay_table.txt"), "w", encoding="utf-8").write("\n".join(L))
    print(L[0])
    for stat in ["median", "mean"]:
        p = make_plot(stat, seqorder, byid, namemap)
        print("written", os.path.basename(p))


if __name__ == "__main__":
    main()
