"""
報告書用の図表生成。
出力: analysis/out/figures/
  fig1_accuracy_pdca.png      便推定精度の推移とPDCAマイルストーン
  fig2_delay_map.html         遅延の瑞穂町マップオーバーレイ（folium, 実地図タイル）
  fig2_delay_map.png          同・静止画（報告書貼り付け用, 路線図＋停留所遅延）
  fig3_delay_by_hour.png      時間帯別の遅延
  fig4_weather_weekday.png    天候別・曜日別の遅延
"""
import os
import sys
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
from google.cloud import bigquery

# 日本語フォント
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


def delay_color(sec):
    if sec is None:
        return "#999999"
    if sec < 60:
        return "#2ca02c"
    if sec < 120:
        return "#bcbd22"
    if sec < 240:
        return "#ff7f0e"
    return "#d62728"


# ============ Fig1: 精度推移 + PDCA ============
def fig1_accuracy():
    rows = list(client.query(f"""
      SELECT CAST(jst_date AS STRING) d,
        ROUND(100*SUM(CASE WHEN gt_in_service AND gt_confident THEN trip_match_correct END)
              /NULLIF(COUNTIF(gt_in_service AND gt_confident),0),1) acc
      FROM `{PROJECT}.bus_gps_data.gps_eval`
      WHERE jst_date >= '2026-04-23'
      GROUP BY d ORDER BY d
    """).result())
    dates = [datetime.strptime(r["d"], "%Y-%m-%d") for r in rows]
    acc = [r["acc"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.plot(dates, acc, "-o", color="#1f77b4", lw=1.8, ms=4, label="便推定精度（日次）")

    # PDCAマイルストーン
    milestones = [
        ("2026-04-30", "v3 便固定型へ刷新", "#d62728"),
        ("2026-05-02", "折返し回送/早期終了 修正", "#ff7f0e"),
        ("2026-05-09", "v3.2 始発保留/\nシーケンシャル", "#9467bd"),
        ("2026-05-11", "v3.3 トポロジー型\n(欠番対応)", "#2ca02c"),
    ]
    for i, (d, label, col) in enumerate(milestones):
        x = datetime.strptime(d, "%Y-%m-%d")
        ax.axvline(x, color=col, ls="--", lw=1.3, alpha=0.8)
        ax.annotate(label, xy=(x, 40), xytext=(x, 40 - (i % 2) * 7),
                    fontsize=8.5, color=col, ha="left", va="bottom")

    # 期間平均の水平線
    ax.axhline(np.nanmean(acc), color="gray", ls=":", lw=1, alpha=0.7)
    ax.set_ylim(30, 100)
    ax.set_ylabel("便推定精度 [%]")
    ax.set_xlabel("日付（2026年）")
    ax.set_title("便推定精度の推移とPDCAによる改善（実証実験 4/23〜5/30）", fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(os.path.join(OUTDIR, "fig1_accuracy_pdca.png"), dpi=150)
    plt.close(fig)
    print("fig1 done")


# ============ 停留所別 中央値遅延 + 座標 ============
def stop_delays():
    rows = list(client.query(f"""
      SELECT stop_id, COUNT(*) n,
        APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med
      FROM `{PROJECT}.bus_gps_data.stop_events`
      GROUP BY stop_id HAVING n >= 30
    """).result())
    gtfs = G.load_gtfs()
    out = []
    for r in rows:
        s = gtfs["stops"].get(r["stop_id"])
        if s:
            out.append((r["stop_id"], s["name"], s["lat"], s["lon"], int(r["med"]), r["n"]))
    return out, gtfs


# ============ Fig2: 遅延マップ（folium + 静止画）============
def fig2_map():
    stops, gtfs = stop_delays()
    pc = G.load_precomputed()
    shapes = pc["shapes"]

    # --- folium ---
    try:
        import folium
        lat0 = np.mean([s[2] for s in stops]); lon0 = np.mean([s[3] for s in stops])
        m = folium.Map(location=[lat0, lon0], zoom_start=14, tiles="OpenStreetMap")
        for sid, pts in shapes.items():
            folium.PolyLine([(p["lat"], p["lon"]) for p in pts],
                            color="#4444aa", weight=2, opacity=0.4).add_to(m)
        for sid, nm, la, lo, med, n in stops:
            folium.CircleMarker(
                [la, lo], radius=5 + min(med, 420) / 60.0,
                color=delay_color(med), fill=True, fill_color=delay_color(med),
                fill_opacity=0.85,
                popup=f"{nm} 中央値遅延 {med}s (n={n})",
                tooltip=f"{nm}: {med}s").add_to(m)
        # 凡例
        legend = ('<div style="position:fixed;bottom:30px;left:30px;z-index:9999;'
                  'background:white;padding:10px;border:1px solid #888;font-size:12px">'
                  '<b>停留所別 中央値到着遅延</b><br>'
                  '<span style="color:#2ca02c">●</span> &lt;1分　'
                  '<span style="color:#bcbd22">●</span> 1-2分<br>'
                  '<span style="color:#ff7f0e">●</span> 2-4分　'
                  '<span style="color:#d62728">●</span> 4分以上</div>')
        m.get_root().html.add_child(folium.Element(legend))
        m.save(os.path.join(OUTDIR, "fig2_delay_map.html"))
        print("fig2 html done")
    except Exception as e:
        print("folium skip:", e)

    # --- 静止画 ---
    fig, ax = plt.subplots(figsize=(9, 9))
    for sid, pts in shapes.items():
        xs = [p["lon"] for p in pts]; ys = [p["lat"] for p in pts]
        ax.plot(xs, ys, "-", color="#cccccc", lw=1.2, zorder=1)
    for sid, nm, la, lo, med, n in stops:
        ax.scatter(lo, la, s=40 + min(med, 420) / 2.5, c=delay_color(med),
                   edgecolors="k", linewidths=0.4, zorder=3)
    # 遅延上位に名前ラベル
    for sid, nm, la, lo, med, n in sorted(stops, key=lambda x: -x[4])[:8]:
        ax.annotate(f"{nm}\n{med//60}分{med%60}秒", (lo, la), fontsize=7.5,
                    xytext=(3, 3), textcoords="offset points", zorder=4)
    # 凡例
    from matplotlib.lines import Line2D
    leg = [Line2D([0],[0], marker='o', color='w', markerfacecolor=c, markersize=9, label=l)
           for c, l in [("#2ca02c","1分未満"),("#bcbd22","1-2分"),("#ff7f0e","2-4分"),("#d62728","4分以上")]]
    ax.legend(handles=leg, title="中央値到着遅延", loc="upper right", fontsize=9)
    ax.set_title("停留所別の到着遅延（中央値）— 瑞穂町コミュニティバス", fontsize=13)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / np.cos(np.radians(35.77)))
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(OUTDIR, "fig2_delay_map.png"), dpi=150)
    plt.close(fig)
    print("fig2 png done")


# ============ Fig3: 時間帯別遅延 ============
def fig3_hour():
    rows = list(client.query(f"""
      SELECT CAST(DIV(actual_sec, 3600) AS INT64) h, COUNT(*) n,
        APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late
      FROM `{PROJECT}.bus_gps_data.stop_events`
      GROUP BY h HAVING COUNT(*) >= 20 ORDER BY h
    """).result())
    hrs = [r["h"] for r in rows]
    med = [r["med"] for r in rows]
    late = [r["pct_late"] for r in rows]
    fig, ax1 = plt.subplots(figsize=(10, 4.6))
    ax1.bar(hrs, [m/60 for m in med], color="#1f77b4", alpha=0.8, label="中央値遅延[分]")
    ax1.set_ylabel("中央値 到着遅延 [分]", color="#1f77b4")
    ax1.set_xlabel("時刻（時）")
    ax2 = ax1.twinx()
    ax2.plot(hrs, late, "-o", color="#d62728", label="2分超え遅延の割合[%]")
    ax2.set_ylabel("2分超え遅延の割合 [%]", color="#d62728")
    ax1.set_title("時間帯別の到着遅延（正解ベース）", fontsize=13)
    ax1.set_xticks(hrs)
    ax1.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(OUTDIR, "fig3_delay_by_hour.png"), dpi=150)
    plt.close(fig)
    print("fig3 done")


# ============ Fig4: 天候別・曜日別 ============
def fig4_weather_weekday():
    wx = list(client.query(f"""
      WITH j AS (SELECT s.*, w.rain_mm,
          CASE WHEN w.rain_mm=0 THEN '晴' WHEN w.rain_mm<10 THEN '雨(0-10mm)' ELSE '大雨(10mm+)' END grp
        FROM `{PROJECT}.bus_gps_data.stop_events` s
        JOIN `{PROJECT}.bus_gps_data.weather` w ON s.date=w.date
        WHERE EXTRACT(DAYOFWEEK FROM s.date) BETWEEN 2 AND 6)
      SELECT grp, APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late
      FROM j GROUP BY grp ORDER BY grp
    """).result())
    dw = list(client.query(f"""
      SELECT EXTRACT(DAYOFWEEK FROM date) dow,
        APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late
      FROM `{PROJECT}.bus_gps_data.stop_events` GROUP BY dow ORDER BY dow
    """).result())
    wdname = {1:'日',2:'月',3:'火',4:'水',5:'木',6:'金',7:'土'}

    fig, (axa, axb) = plt.subplots(1, 2, figsize=(12, 4.6))
    g = [r["grp"] for r in wx]; gm = [r["med"]/60 for r in wx]; gl=[r["pct_late"] for r in wx]
    axa.bar(g, gm, color="#4c78a8")
    for i,(v,l) in enumerate(zip(gm,gl)):
        axa.text(i, v+0.02, f"{v:.1f}分\n遅延{l:.0f}%", ha="center", fontsize=8.5)
    axa.set_title("天候別 中央値到着遅延（平日）", fontsize=12)
    axa.set_ylabel("中央値 到着遅延 [分]")
    axa.set_ylim(0, max(gm)*1.4)

    d = [wdname[r["dow"]] for r in dw]; dm=[r["med"]/60 for r in dw]; dl=[r["pct_late"] for r in dw]
    colors=["#e45756" if x in ('土','日') else "#4c78a8" for x in d]
    axb.bar(d, dm, color=colors)
    for i,(v,l) in enumerate(zip(dm,dl)):
        axb.text(i, v+0.02, f"{l:.0f}%", ha="center", fontsize=8.5)
    axb.set_title("曜日別 中央値到着遅延（土日=赤）", fontsize=12)
    axb.set_ylabel("中央値 到着遅延 [分]")
    axb.set_ylim(0, max(dm)*1.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUTDIR, "fig4_weather_weekday.png"), dpi=150)
    plt.close(fig)
    print("fig4 done")


if __name__ == "__main__":
    fig1_accuracy()
    fig2_map()
    fig3_hour()
    fig4_weather_weekday()
    print("all figures ->", OUTDIR)
