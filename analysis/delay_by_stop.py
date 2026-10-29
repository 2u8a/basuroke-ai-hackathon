"""
停留所ごとの「予定より何分遅れて到着したか」を、停留所名を軸にした線グラフで示す。

- X: 代表便の停留所（運行順, 停留所名）で並べ、値は系統内の全便を stop_id で集約
  （代表便1本だと終盤の停留所ほど到着検出サンプルが枯れて中央値が不安定になるため）
- Y: 到着の遅れ [分]（プラス=遅れ）
- 系統1・2・4、中央値版と平均値版をそれぞれ作成
- 始発は除外（法令上オンタイムが厳守され、発車前待機で検出がずれるため）
- 松山町入口（系統1）は循環経路によるGPS到着検出の誤マッチで遅延が過大に出るため除外

出力: analysis/out/reliability/delay_by_stop_route{R}_{median,mean}.png
"""
import os
import sys
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
OUT = os.path.join(os.path.dirname(__file__), "out", "reliability")
os.makedirs(OUT, exist_ok=True)
client = bigquery.Client(project=PROJECT)

# 正式なコース名（routes.txt の route_long_name より）
ROUTE_NAME = {"1": "元狭山コース", "2": "元狭山・長岡コース", "4": "ふれあいセンターコース"}
COLORS = {"1": "#1f77b4", "2": "#d62728", "4": "#2ca02c"}

# 検出誤マッチ等で除外したい停留所があれば (route_id, stop_name) で指定する。
# （旧版は循環経路のGPS投影バグで松山町入口が過大に出たため除外していたが、
#   stop_events.py を停留所座標への最接近方式に修正して解消したため現在は空。）
EXCLUDE = set()

# サンプルが極端に少ない停留所は中央値が不安定なので表示しない（終点駅など）。
MIN_SAMPLES = 5


def rep_trips():
    """各系統の代表便（到着イベント数が最多の便）。X軸の停留所順を決めるために使う。"""
    q = f"""SELECT route_id, trip_id
            FROM `{PROJECT}.bus_gps_data.stop_events`
            GROUP BY route_id, trip_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY route_id ORDER BY COUNT(*) DESC) = 1"""
    return {r["route_id"]: r["trip_id"] for r in client.query(q).result()}


def rep_order(rep):
    """route -> [(stop_seq, stop_id, stop_name, is_origin, is_terminal), ...]（代表便の運行順）。"""
    order = {}
    for route, tid in rep.items():
        q = f"""SELECT stop_seq, stop_id, stop_name, is_origin, is_terminal
                FROM `{PROJECT}.bus_gps_data.stop_events`
                WHERE trip_id = '{tid}'
                GROUP BY stop_seq, stop_id, stop_name, is_origin, is_terminal
                ORDER BY stop_seq"""
        order[route] = [(r["stop_seq"], r["stop_id"], r["stop_name"],
                         r["is_origin"], r["is_terminal"])
                        for r in client.query(q).result()]
    return order


def agg(route):
    """(stop_id, role) -> [delay_sec, ...]（系統内の全便・全イベント）。
    role: O=始発, T=終点駅, M=途中。始発と終点駅は同じ駅で stop_id が同一なため
    役割で分けて集約する。"""
    q = f"""SELECT stop_id, delay_sec, is_origin, is_terminal
            FROM `{PROJECT}.bus_gps_data.stop_events`
            WHERE route_id = '{route}'"""
    d = defaultdict(list)
    for r in client.query(q).result():
        role = "O" if r["is_origin"] else "T" if r["is_terminal"] else "M"
        d[(r["stop_id"], role)].append(r["delay_sec"])
    return d


def make(route, order, delays, stat, include_terminal=True):
    labels, vals = [], []
    seen = {}                              # 循環で同名停留所が復路に再登場する
    for seq, stop_id, name, is_origin, is_terminal in order:
        if (route, name) in EXCLUDE:
            continue                       # 検出誤マッチのため除外
        if is_terminal and not include_terminal:
            continue                       # 終点駅を除いた版
        role = "O" if is_origin else "T" if is_terminal else "M"
        arr = delays.get((stop_id, role))
        if not arr or len(arr) < MIN_SAMPLES:
            continue                       # サンプル過少は不安定なので除外
        d = np.array(arr)
        if role == "O":
            label = f"{name}(発)"
        elif role == "T":
            label = f"{name}(着)"
        else:
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name}(復)"
        labels.append(label)
        vals.append((np.median(d) if stat == "median" else np.mean(d)) / 60.0)
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(8, 0.7 * len(labels) + 3), 6))
    ax.plot(x, vals, "-o", color=COLORS[route], lw=2.2, ms=7)
    for xi, v in zip(x, vals):
        ax.annotate(f"{v:.1f}", (xi, v), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=8, color="#333")
    ax.axhline(0, color="gray", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    st = "中央値" if stat == "median" else "平均値"
    ax.set_ylabel("到着の遅れ [分]（プラス＝遅れ）")
    if include_terminal:
        subtitle = "（箱根ケ崎駅発→循環→駅着。(発)=始発 (着)=終点 (復)=復路。プラスが遅れ）"
    else:
        subtitle = ("（箱根ケ崎駅発→循環、終点駅は除く。(発)=始発 (復)=復路。プラスが遅れ）\n"
                    "※終点駅は便終了に伴うGPS記録停止と重なり「到着遅れ」として比較不可のため除外")
    ax.set_title(f"{ROUTE_NAME[route]} 停留所ごとの到着の遅れ（{st}）— 運行順（循環）\n"
                 f"{subtitle}", fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    excluded = [n for (r, n) in EXCLUDE if r == route]
    if excluded:
        fig.text(0.01, 0.005,
                 "※" + "・".join(excluded) + "は循環経路によるGPS到着検出の誤マッチのため除外",
                 fontsize=8, color="#666", ha="left")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    suffix = "" if include_terminal else "_noterm"
    path = os.path.join(OUT, f"delay_by_stop_route{route}_{stat}{suffix}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    rep = rep_trips()
    order = rep_order(rep)
    for route in ["1", "2", "4"]:
        if route not in order:
            continue
        delays = agg(route)
        for stat in ["median", "mean"]:
            p = make(route, order[route], delays, stat, include_terminal=True)
            print("written", os.path.basename(p))
            p = make(route, order[route], delays, stat, include_terminal=False)
            print("written", os.path.basename(p))


if __name__ == "__main__":
    main()
