"""
停留所ごとの到着の遅れの「分布」を箱ひげ図で示す。

線グラフ（delay_by_stop.py）が中央値/平均の1点だけを見せるのに対し、
本図は各停留所の四分位範囲（箱）・ひげ・外れ値（点）まで見せる。
- ばらつきの大小（定時性の良し悪し）が停留所ごとに読める
- 外れ値の点は、循環誤マッチ等の「検出システム上のエラー」の残存を可視化する
  （＝どの停留所で異常値が出やすいかの点検にも使える）

集計は delay_by_stop.py と同じ（全便を stop_id×role で集約、循環1周を運行順に、
始発(発)・終点(着)・復路(復)を区別）。終点あり版と終点なし版を出力。

出力: analysis/out/reliability/delay_boxplot_route{R}.png / _noterm.png
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))
import delay_by_stop as D   # rep_trips, rep_order, agg, ROUTE_NAME, COLORS, EXCLUDE, MIN_SAMPLES, OUT


def collect(route, order, delays, include_terminal):
    """order 順に (ラベル, 遅延配列[分]) を集める。delay_by_stop.make と同じ選別。"""
    labels, data = [], []
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
        if role == "O":
            label = f"{name}(発)"
        elif role == "T":
            label = f"{name}(着)"
        else:
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name}(復)"
        labels.append(label)
        data.append(np.array(arr, dtype=float) / 60.0)
    return labels, data


def make_box(route, order, delays, include_terminal=True):
    labels, data = collect(route, order, delays, include_terminal)
    n = len(labels)
    fig, ax = plt.subplots(figsize=(max(10, 0.62 * n + 3), 6.8))
    bp = ax.boxplot(
        data, showfliers=True, patch_artist=True, widths=0.6, whis=1.5,
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
    ax.set_ylabel("到着の遅れ [分]（プラス＝遅れ）")
    if include_terminal:
        subtitle = ("（箱＝四分位、線＝中央値、点＝外れ値。(発)=始発 (着)=終点 (復)=復路。"
                    "終点(着)は便終了のGPS記録停止と重なる点に注意）")
    else:
        subtitle = "（箱＝四分位、線＝中央値、点＝外れ値。終点駅は除外。(発)=始発 (復)=復路）"
    ax.set_title(f"{D.ROUTE_NAME[route]} 停留所ごとの到着の遅れの分布（箱ひげ図）— 運行順（循環）\n"
                 f"{subtitle}", fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    suffix = "" if include_terminal else "_noterm"
    path = os.path.join(D.OUT, f"delay_boxplot_route{route}{suffix}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    rep = D.rep_trips()
    order = D.rep_order(rep)
    for route in ["1", "2", "4"]:
        if route not in order:
            continue
        delays = D.agg(route)
        for inc in (True, False):
            p = make_box(route, order[route], delays, include_terminal=inc)
            print("written", os.path.basename(p))


if __name__ == "__main__":
    main()
