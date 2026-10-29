"""
コスト実証（その1）: Firestore 書込回数の実測とスケール推計。

実証で得た日別のGPS書込回数（=Firestore ドキュメント書込回数）から、
- 1台構成での書込が Firebase 無料枠（20,000 書込/日）に収まること
- 何台まで無料枠内で運用できるか（平均基準・ピーク基準）
を実データで示す。機材費・SIM通信費・Cloud Run 実行費は実費入力が別途必要。

出力:
  analysis/out/cost/firestore_writes_by_day.png
  analysis/out/cost/firestore_scaling.csv
"""
import os
import csv

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
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
OUT = os.path.join(os.path.dirname(__file__), "out", "cost")
os.makedirs(OUT, exist_ok=True)
client = bigquery.Client(project=PROJECT)

FREE_TIER_WRITES = 20000    # Firestore 無料枠: ドキュメント書込 20,000 件/日/プロジェクト
# 本番運用として扱う期間（準備・試験日を除く実証本番）
PROD_START = "2026-04-22"


def load():
    q = f"""SELECT CAST(jst_date AS STRING) d, COUNT(*) writes
            FROM `{PROJECT}.bus_gps_data.gps_flat`
            GROUP BY d ORDER BY d"""
    return [(r["d"], int(r["writes"])) for r in client.query(q).result()]


def main():
    rows = load()
    prod = [(d, w) for d, w in rows if d >= PROD_START]
    dates = [d for d, _ in prod]
    writes = np.array([w for _, w in prod])
    avg = float(writes.mean())
    peak = int(writes.max())
    peak_day = dates[int(writes.argmax())]

    # 何台まで無料枠内か（1台=1日あたり書込 avg / peak 基準、切り捨て）
    cars_avg = int(FREE_TIER_WRITES // avg)
    cars_peak = int(FREE_TIER_WRITES // peak)

    # --- 図: 日別書込 + 無料枠ライン ---
    x = np.arange(len(dates))
    fig, ax = plt.subplots(figsize=(max(10, 0.32 * len(dates) + 3), 5.5))
    ax.bar(x, writes, color="#1f77b4", width=0.8, label="1台の実測書込/日")
    ax.axhline(FREE_TIER_WRITES, color="#d62728", lw=2, ls="--",
               label=f"Firebase 無料枠 {FREE_TIER_WRITES:,} 書込/日")
    ax.axhline(avg, color="#2ca02c", lw=1.5, ls=":",
               label=f"実証期間の平均 {avg:,.0f} 書込/日")
    ax.set_xticks(x[::2])
    ax.set_xticklabels([d[5:] for d in dates[::2]], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Firestore 書込回数 [件/日]")
    ax.set_ylim(0, FREE_TIER_WRITES * 1.08)
    ax.set_title("1台構成の Firestore 書込回数（実測）と無料枠\n"
                 f"1台のピークでも {peak:,} 件/日（{peak_day}）で無料枠 {FREE_TIER_WRITES:,} 件の"
                 f"約 {peak/FREE_TIER_WRITES*100:.0f}% — 書込課金は 0 円",
                 fontsize=12)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    p = os.path.join(OUT, "firestore_writes_by_day.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print("written", os.path.basename(p))

    # --- CSV: スケール推計サマリ ---
    csv_path = os.path.join(OUT, "firestore_scaling.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["指標", "値", "備考"])
        w.writerow(["実証本番期間", f"{dates[0]}〜{dates[-1]}", f"{len(dates)}日"])
        w.writerow(["1台 平均書込/日", f"{avg:,.0f}", "GPSログ=Firestore書込1回"])
        w.writerow(["1台 ピーク書込/日", f"{peak:,}", peak_day])
        w.writerow(["Firestore無料枠", f"{FREE_TIER_WRITES:,}", "書込/日/プロジェクト"])
        w.writerow(["1台の無料枠使用率(ピーク)", f"{peak/FREE_TIER_WRITES*100:.0f}%", ""])
        w.writerow(["無料枠内で運用可能な台数(平均基準)", f"約{cars_avg}台", f"{FREE_TIER_WRITES:,}÷{avg:,.0f}"])
        w.writerow(["無料枠内で運用可能な台数(ピーク基準)", f"約{cars_peak}台", f"{FREE_TIER_WRITES:,}÷{peak:,}"])
        w.writerow(["1台構成の書込課金", "0円", "無料枠内"])
    print("written", os.path.basename(csv_path))
    print(f"avg={avg:.0f}/日 peak={peak}({peak_day}) 無料枠内 平均基準約{cars_avg}台 ピーク基準約{cars_peak}台")


if __name__ == "__main__":
    main()
