"""
ルート位置に対する「予定より遅れて到着」プロット（生データ）。

目的: どのあたり（ルート上の位置）で予定より遅れて到着しやすいかを、
      パディング補正などをせず生の遅延データで示す。
      仮説「ルート終盤ほど遅延が大きくなる（＝終点で合わせる余裕ダイヤ）」の検証用。

- X: ルート上の位置（0=始発, 1=終点、便距離ベース）
- Y: 時刻表に対する到着遅延 [分]（生データ）
- 全ルート（1・2・4。3・5は信頼できる運行データなし）
- 中央値版と平均値版を別々に出力

注記: 出発は法令上オンタイムが厳守されており、始発の小さな値はGPSの時刻ブレ。
      早着の主張はしない（GPSブレのため断定不可）。

出力: analysis/out/reliability/
  delay_by_position_median.png
  delay_by_position_mean.png
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

NBINS = 12
MIN_BIN = 8
ROUTE_NAME = {"1": "系統1", "2": "系統2", "4": "系統4"}
COLORS = {"1": "#1f77b4", "2": "#d62728", "4": "#2ca02c"}


def load():
    pc = G.load_precomputed()
    frac = {}
    for tid, prog in pc["trip_progress"].items():
        rl = prog[-1]["distance_m"] or 1.0
        for e in prog:
            frac[(tid, int(e["stop_sequence"]))] = e["distance_m"] / rl
    # 始発・終点駅は発着前後の待機やGPS途切れで到着時刻検出がずれるため除外する。
    sql = f"""SELECT route_id, trip_id, stop_seq, delay_sec
              FROM `{PROJECT}.bus_gps_data.stop_events`
              WHERE is_origin = false AND is_terminal = false"""
    by = defaultdict(list)   # route -> [(frac, delay)]
    for r in client.query(sql).result():
        f = frac.get((r["trip_id"], r["stop_seq"]))
        if f is not None:
            by[r["route_id"]].append((f, r["delay_sec"]))
    return by


def make(by, stat, title, path):
    fig, ax = plt.subplots(figsize=(11, 5.8))
    bins = np.linspace(0, 1, NBINS + 1)
    for rid in ["1", "2", "4"]:
        pts = by.get(rid, [])
        if len(pts) < 30:
            continue
        fr = np.array([p[0] for p in pts]); dl = np.array([p[1] for p in pts])
        bx, bv = [], []
        for i in range(NBINS):
            if i < NBINS - 1:
                m = (fr >= bins[i]) & (fr < bins[i + 1])
            else:
                m = (fr >= bins[i]) & (fr <= bins[i + 1])
            if m.sum() < MIN_BIN:
                continue
            d = dl[m]
            bx.append((bins[i] + bins[i + 1]) / 2)
            bv.append((np.median(d) if stat == "median" else np.mean(d)) / 60.0)
        ax.plot(bx, bv, "-o", color=COLORS[rid], lw=2.2, ms=6,
                label=f"{ROUTE_NAME[rid]}（{len(pts)}点）")
    ax.axhline(0, color="gray", lw=1)
    ax.set_xlabel("ルート上の位置（0=始発, 1=終点）")
    st = "中央値" if stat == "median" else "平均値"
    ax.set_ylabel(f"時刻表に対する到着遅延 [分]（{st}, +遅れ）")
    ax.set_title(title, fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    by = load()
    make(by, "median",
         "ルート位置ごとの到着遅延（中央値・生データ）— 終盤ほど遅れが大きくなるか",
         os.path.join(OUT, "delay_by_position_median.png"))
    make(by, "mean",
         "ルート位置ごとの到着遅延（平均値・生データ）— 終盤ほど遅れが大きくなるか",
         os.path.join(OUT, "delay_by_position_mean.png"))
    print("points per route:", {rid: len(by.get(rid, [])) for rid in ["1", "2", "4"]})
    print("written median/mean")


if __name__ == "__main__":
    main()
