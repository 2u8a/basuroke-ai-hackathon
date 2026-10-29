"""
「予定到着時刻の瞬間に、バスが停留所からどれだけ離れていたか」を距離で見る分析。

delay_by_stop / delay_boxplot は「停留所に着いた時刻 − 予定時刻」= 時間軸の遅れを見る。
本図はその逆で、各停留所の予定到着時刻ちょうどの瞬間に、バスが実際どこにいたかを
「停留所までの符号付き距離[m]」で測る（時間軸ではなく空間軸の定時性）。

  符号: プラス = 予定時刻にまだ停留所の手前＝これから到着＝遅れている
        マイナス = 予定時刻に既に通過済み＝早い

方法（1台構成・実測ベース、補間なし）:
  各 run（正解便）の各時刻表停留所について、
    1. その停留所の予定時刻に「時間的に最も近い」GPS受信点を取る（±10分以内のみ）。
    2. その点から停留所座標までの直線距離(Haversine)を測る。
    3. 直後の点で停留所へ近づいていれば手前(+)、遠ざかっていれば通過済み(−)と符号付け。
  循環で同名停留所を2回通る問題は「予定時刻に時間的最近傍」で自然に往復分離される。

出力: analysis/out/reliability/pos_at_sched_route{R}.png / _noterm.png
"""
import os
import sys
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
import stop_events as SE          # fetch_points, haversine, tod_sec
import delay_by_stop as D         # rep_trips, rep_order, ROUTE_NAME, COLORS, OUT, MIN_SAMPLES, EXCLUDE

NEAR_T_MAX = 600                  # 予定時刻とのこの秒数以内に点が無ければ「不在」でスキップ


def build():
    """(route, stop_id, role) -> [signed_dist_m, ...]。"""
    g = G.load_gtfs(); pc = G.load_precomputed()
    tp = pc["trip_progress"]; stops = g["stops"]
    trips = g["trips"]
    runs = SE.fetch_points()
    data = defaultdict(list)
    for run_id, pts in runs.items():
        if not pts:
            continue
        trip_id = pts[0]["trip_id"]
        prog = tp.get(trip_id)
        if not prog or trip_id not in trips:
            continue
        route = trips[trip_id]["route_id"]
        tods = np.array([SE.tod_sec(p["ts"]) for p in pts])
        lat = [p["lat"] for p in pts]
        lon = [p["lon"] for p in pts]
        n = len(pts); n_stops = len(prog)
        for si_idx, e in enumerate(prog):
            sched = int(e["time_sec"])
            s = stops.get(e["stop_id"], {})
            slat, slon = s.get("lat"), s.get("lon")
            if slat is None:
                continue
            k = int(np.argmin(np.abs(tods - sched)))    # 予定時刻に時間的最近傍の点
            if abs(int(tods[k]) - sched) > NEAR_T_MAX:
                continue                                # その時刻に記録なし
            dk = SE.haversine(slat, slon, lat[k], lon[k])   # 予定時刻の瞬間の位置ずれ[m]
            # 符号(方式a): その停留所を実際に通過したのが予定の前か後か。
            #   通過時刻 = 予定近傍窓[sched-600, sched+720]で停留所へ最接近した点の時刻
            #   （stop_events.py の到着検出と同基準なので、時間軸の遅延分析と符号が一致する）
            lo, hi = sched - 600, sched + 720
            cand = [j for j in range(n) if lo <= tods[j] <= hi]
            if cand:
                jbest = min(cand, key=lambda j: SE.haversine(slat, slon, lat[j], lon[j]))
                t_pass = int(tods[jbest])
            else:
                t_pass = int(tods[k])
            sign = 1.0 if t_pass >= sched else -1.0   # 遅れて通過→予定時刻には手前(+/遅れ)
            role = "O" if si_idx == 0 else "T" if si_idx == n_stops - 1 else "M"
            # (方式a=符号付き距離, 方式c=絶対距離) を両方保持
            data[(route, e["stop_id"], role)].append((sign * dk, dk))
    return data


def collect(route, order, data, include_terminal, mode):
    """mode='a': 符号付き距離（実通過が予定の前後で符号）, 'c': 絶対距離。"""
    idx = 0 if mode == "a" else 1
    labels, series = [], []
    seen = {}
    for seq, stop_id, name, is_origin, is_terminal in order:
        if (route, name) in D.EXCLUDE:
            continue
        if is_terminal and not include_terminal:
            continue
        role = "O" if is_origin else "T" if is_terminal else "M"
        arr = data.get((route, stop_id, role))
        if not arr or len(arr) < D.MIN_SAMPLES:
            continue
        if role == "O":
            label = f"{name}(発)"
        elif role == "T":
            label = f"{name}(着)"
        else:
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name}(復)"
        labels.append(label)
        series.append(np.array([v[idx] for v in arr], dtype=float))
    return labels, series


def make_box(route, order, data, include_terminal, mode):
    labels, series = collect(route, order, data, include_terminal, mode)
    n = len(labels)
    fig, ax = plt.subplots(figsize=(max(10, 0.62 * n + 3), 6.8))
    bp = ax.boxplot(
        series, showfliers=True, patch_artist=True, widths=0.6, whis=1.5,
        medianprops=dict(color="black", lw=1.6),
        boxprops=dict(edgecolor=D.COLORS[route]),
        whiskerprops=dict(color=D.COLORS[route]),
        capprops=dict(color=D.COLORS[route]),
        flierprops=dict(marker="o", markersize=3.5, markerfacecolor="#d62728",
                        markeredgecolor="none", alpha=0.5))
    for box in bp["boxes"]:
        box.set(facecolor=D.COLORS[route], alpha=0.30)
    ax.axhline(0, color="gray", lw=1)
    ax.set_xticks(range(1, n + 1))
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    term = "" if include_terminal else "、終点駅は除外"
    if mode == "a":
        ax.set_ylabel("予定時刻の瞬間の停留所からの距離 [m]\n（＋=まだ手前／遅れ、−=通過済み／早い）")
        ax.set_title(f"{D.ROUTE_NAME[route]} 予定到着時刻の瞬間にバスがいた位置（符号付き=方式a）"
                     f" — 運行順（循環）\n（＋がその時点で手前＝遅れ。符号は実通過が予定の前後か{term}）",
                     fontsize=12)
    else:
        ax.set_ylabel("予定時刻の瞬間の停留所からの距離 [m]（絶対値）")
        ax.set_title(f"{D.ROUTE_NAME[route]} 予定到着時刻の瞬間にバスがいた位置（絶対距離=方式c）"
                     f" — 運行順（循環）\n（0に近いほど予定時刻ちょうどに停留所付近にいた{term}）",
                     fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    suffix = "" if include_terminal else "_noterm"
    path = os.path.join(D.OUT, f"pos_at_sched_route{route}_{mode}{suffix}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    data = build()
    rep = D.rep_trips()
    order = D.rep_order(rep)
    for route in ["1", "2", "4"]:
        if route not in order:
            continue
        for mode in ("a", "c"):
            for inc in (True, False):
                p = make_box(route, order[route], data, include_terminal=inc, mode=mode)
                print("written", os.path.basename(p))


if __name__ == "__main__":
    main()
