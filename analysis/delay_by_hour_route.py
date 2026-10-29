"""
時間帯別の到着遅延 ―― 路線別（運行時間帯の偏りを考慮）。

合算の時間帯別遅延（figures_fig3）は「午後〜夕方で遅延増」を示すが、系統ごとに
運行される時間帯が偏っている（系統1は夕方〜夜まで・最も遅れやすい／系統2は夕方前
まで／系統4は日中のみ）ため、合算では交絡が生じる。本図は路線別に分けて示し、
あわせて時間帯ごとの便数（観測数）の偏りも下段に表示して交絡の所在を明らかにする。

データ源: stop_events（正解データ delay_sec、始発・終点駅は除外）。予定時刻の時で集計。
出力: analysis/out/figures/fig3b_delay_by_hour_route.png
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
OUTDIR = os.path.join(os.path.dirname(__file__), "out", "figures")
os.makedirs(OUTDIR, exist_ok=True)
client = bigquery.Client(project=PROJECT)

ROUTE_NAME = {"1": "元狭山コース（系統1）", "2": "元狭山・長岡コース（系統2）", "4": "ふれあいセンターコース（系統4）"}
COLORS = {"1": "#1f77b4", "2": "#d62728", "4": "#2ca02c"}
ROUTES = ["1", "2", "4"]
HOURS = list(range(6, 21))     # 6〜20時
MIN_N = 20                     # これ未満の時間帯は中央値が不安定として点を薄く


def fetch():
    q = f"""SELECT route_id AS rt, CAST(FLOOR(sched_sec/3600) AS INT64) AS hr,
              COUNT(*) AS n,
              APPROX_QUANTILES(delay_sec, 2)[OFFSET(1)] AS med
            FROM `{PROJECT}.bus_gps_data.stop_events`
            WHERE is_origin = false AND is_terminal = false AND route_id IN ('1','2','4')
            GROUP BY rt, hr"""
    med = defaultdict(dict)
    cnt = defaultdict(dict)
    for r in client.query(q).result():
        med[r["rt"]][int(r["hr"])] = float(r["med"]) / 60.0
        cnt[r["rt"]][int(r["hr"])] = int(r["n"])
    return med, cnt


def main():
    med, cnt = fetch()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8.2), sharex=True,
                                   gridspec_kw={"height_ratios": [2.1, 1]})

    # --- 上段: 路線別 中央値遅延 ---
    for rid in ROUTES:
        xs, ys, faint_x, faint_y = [], [], [], []
        for h in HOURS:
            if h in med[rid]:
                if cnt[rid].get(h, 0) >= MIN_N:
                    xs.append(h); ys.append(med[rid][h])
                else:
                    faint_x.append(h); faint_y.append(med[rid][h])
        ax1.plot(xs, ys, "-o", color=COLORS[rid], lw=2.2, ms=8, label=ROUTE_NAME[rid], zorder=3)
        if faint_x:  # 便数僅少の点は中抜きで
            ax1.plot(faint_x, faint_y, "o", mfc="white", mec=COLORS[rid], mew=1.5, ms=7, zorder=3)
        # 直接ラベル（線の右端）
        if xs:
            ax1.annotate(ROUTE_NAME[rid].split("（")[0], (xs[-1], ys[-1]),
                         textcoords="offset points", xytext=(8, 0), va="center",
                         fontsize=9, color=COLORS[rid], fontweight="bold")
    ax1.set_ylabel("到着の遅れ 中央値 [分]")
    ax1.set_ylim(0, None)
    ax1.grid(True, axis="y", alpha=0.3)
    ax1.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax1.set_title("路線別・時間帯別の到着遅延（正解データ・中央値）\n"
                  "運行時間帯の偏りを考慮（白丸＝便数僅少で参考値）", fontsize=13)

    # --- 下段: 時間帯ごとの便数（観測数）の偏り ---
    w = 0.26
    offs = {"1": -w, "2": 0.0, "4": w}
    for rid in ROUTES:
        xs = [h + offs[rid] for h in HOURS]
        ns = [cnt[rid].get(h, 0) for h in HOURS]
        ax2.bar(xs, ns, width=w, color=COLORS[rid], alpha=0.85, label=ROUTE_NAME[rid].split("（")[0])
    ax2.set_ylabel("観測便数（停留所到着数）")
    ax2.set_xlabel("時刻（時）")
    ax2.set_xticks(HOURS)
    ax2.grid(True, axis="y", alpha=0.3)
    ax2.set_title("時間帯ごとの運行（観測）便数 ― 路線で運行時間帯が偏っている", fontsize=11)

    fig.tight_layout()
    path = os.path.join(OUTDIR, "fig3b_delay_by_hour_route.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print("written", os.path.basename(path))


if __name__ == "__main__":
    main()
