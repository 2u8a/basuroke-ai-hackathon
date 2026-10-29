"""
停留所ごとの到着遅れ ―― 運行データ版（実証当時にシステムが出力していた遅延）。

delay_by_stop.py は「正解データ(ground_truth)」ベース（stop_events.delay_sec）。
本図はその対照で、停留所通過時点で運用システムが算出・保持していた遅延
（stop_events.op_delay_sec）を停留所別に集計する。

対象期間: 2026-05-11 以降（アルゴリズム最終版 v3.3＋トポロジー修正が安定した以降）。
X軸の停留所順・色・名称は正解版と共通（delay_by_stop.py を再利用）＝直接比較できる。

出力: analysis/out/reliability/delay_by_stop_op_route{R}_{median,mean}{,_noterm}.png
"""
import os
import sys
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))
import delay_by_stop as D   # rep_trips, rep_order, ROUTE_NAME, COLORS, EXCLUDE, MIN_SAMPLES, OUT, PROJECT, client

SINCE = "2026-05-11"


def agg_op(route):
    """(stop_id, role) -> [op_delay_sec, ...]。5/11以降・op_delay_sec非NULLのみ。"""
    q = f"""SELECT stop_id, op_delay_sec, is_origin, is_terminal
            FROM `{D.PROJECT}.bus_gps_data.stop_events`
            WHERE route_id = '{route}'
              AND date >= DATE('{SINCE}')
              AND op_delay_sec IS NOT NULL"""
    d = defaultdict(list)
    for r in D.client.query(q).result():
        role = "O" if r["is_origin"] else "T" if r["is_terminal"] else "M"
        d[(r["stop_id"], role)].append(r["op_delay_sec"])
    return d


def make_op(route, order, delays, stat, include_terminal=True):
    labels, vals = [], []
    seen = {}
    for seq, stop_id, name, is_origin, is_terminal in order:
        if (route, name) in D.EXCLUDE:
            continue
        if is_terminal and not include_terminal:
            continue
        role = "O" if is_origin else "T" if is_terminal else "M"
        arr = delays.get((stop_id, role))
        if not arr or len(arr) < D.MIN_SAMPLES:
            continue
        a = np.array(arr)
        if role == "O":
            label = f"{name}(発)"
        elif role == "T":
            label = f"{name}(着)"
        else:
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name}(復)"
        labels.append(label)
        vals.append((np.median(a) if stat == "median" else np.mean(a)) / 60.0)
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(8, 0.7 * len(labels) + 3), 6))
    ax.plot(x, vals, "-o", color=D.COLORS[route], lw=2.2, ms=7)
    for xi, v in zip(x, vals):
        ax.annotate(f"{v:.1f}", (xi, v), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=8, color="#333")
    ax.axhline(0, color="gray", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    st = "中央値" if stat == "median" else "平均値"
    ax.set_ylabel("システムが出力していた遅れ [分]（プラス＝遅れ）")
    if include_terminal:
        subtitle = "（箱根ケ崎駅発→循環→駅着。(発)=始発 (着)=終点 (復)=復路。プラスが遅れ）"
    else:
        subtitle = ("（箱根ケ崎駅発→循環、終点駅は除く。(発)=始発 (復)=復路。プラスが遅れ）")
    ax.set_title(f"{D.ROUTE_NAME[route]} 停留所ごとの遅れ（運行データ・{st}）— 5/11以降・運行順（循環）\n"
                 f"{subtitle}", fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    suffix = "" if include_terminal else "_noterm"
    path = os.path.join(D.OUT, f"delay_by_stop_op_route{route}_{stat}{suffix}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    rep = D.rep_trips()
    order = D.rep_order(rep)          # X軸順は正解版と共通
    for route in ["1", "2", "4"]:
        if route not in order:
            continue
        delays = agg_op(route)
        for stat in ["median", "mean"]:
            for inc in (True, False):
                p = make_op(route, order[route], delays, stat, include_terminal=inc)
                print("written", os.path.basename(p))


if __name__ == "__main__":
    main()
