"""
「予定通りにバスが来ない」と感じやすい停留所の分析。

前提（実運行で確認された現象）:
  ダイヤは終点でほぼ定刻に着くよう、手前ほど余裕（パディング）を持たせて組まれている。
  そのため中間停留所は時刻表に対し恒常的に「遅れ」て見え、終点では逆に「早着」する。
  → 遅れの一部は運行の失敗ではなく時刻表設計（パディング）の産物。

分析方針:
  乗客が体感する「予定通り来ない」を、性質の違う3つに分けて評価する。
   (A) 恒常的な遅れ（中央値遅延が大きい）: 予測可能。多くはパディング＝時刻表と実態のズレ。
       → 時刻表の見直しで「体感」を改善できる。
   (B) ばらつき（IQRが大きい）: 予測困難。ここが本当の「当てにならない」体感の源。
   (C) 早発（予定より早く来る）: 乗り逃しリスク。最も強い「来なかった」体感。

出力: analysis/out/reliability/
  fig_pos_delay.png       ルート位置 vs 遅延（パディングの可視化）
  fig_unreliability_map.png 体感不安定度マップ
  fig_top_unreliable.png  不安定な停留所 上位バー
  stop_reliability.csv    停留所別 指標＋分類（報告書の表）
  last_segment_padding.csv 終点手前→終点のダイヤ余裕（walk-faster の裏付け）
  reliability_summary.txt
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

EARLY_S = -60.0       # これより早い到着＝早発（乗り逃しリスク）
LATE2_S = 120.0
MIN_N = 40
gtfs = G.load_gtfs()
pc = G.load_precomputed()
STOP_NAME = {sid: s["name"] for sid, s in gtfs["stops"].items()}


def load_rows():
    # 便ごとの stop_seq -> 正規化位置(距離) と 予定通過時刻
    tp = pc["trip_progress"]
    frac = {}
    for tid, prog in tp.items():
        rl = prog[-1]["distance_m"] or 1.0
        for e in prog:
            frac[(tid, int(e["stop_sequence"]))] = e["distance_m"] / rl
    sql = f"""SELECT trip_id, route_id, stop_id, stop_name, stop_seq, stop_lat, stop_lon,
              sched_sec, actual_sec, delay_sec, is_origin, is_terminal
              FROM `{PROJECT}.bus_gps_data.stop_events`"""
    rows = []
    for r in client.query(sql).result():
        f = frac.get((r["trip_id"], r["stop_seq"]))
        rows.append({**dict(r), "frac": f})
    return rows


def write_csv(name, header, data):
    with open(os.path.join(OUT, name), "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n")
        for row in data:
            f.write(",".join("" if v is None else str(v) for v in row) + "\n")


lines = []
def out(s=""):
    lines.append(s); print(s)


# ============ 1. ルート位置 vs 遅延（パディングの可視化）============
def fig_position(rows):
    fig, ax = plt.subplots(figsize=(11, 5.6))
    colors = {"1": "#1f77b4", "2": "#d62728", "4": "#2ca02c"}
    for rid in ["1", "2"]:
        pts = [(r["frac"], r["delay_sec"], r["is_terminal"]) for r in rows
               if r["route_id"] == rid and r["frac"] is not None]
        if len(pts) < 50:
            continue
        fr = np.array([p[0] for p in pts]); dl = np.array([p[1] for p in pts])
        term = np.array([p[2] for p in pts])
        # 中間停留所（終点を除く）で曲線
        mid = ~term
        bins = np.linspace(0, 1, 12)
        bx, bmed, blo, bhi = [], [], [], []
        for i in range(len(bins) - 1):
            m = (fr >= bins[i]) & (fr < bins[i + 1]) & mid
            if m.sum() < 10:
                continue
            d = dl[m]
            bx.append((bins[i] + bins[i + 1]) / 2)
            bmed.append(np.median(d) / 60.0)
            blo.append(np.percentile(d, 25) / 60.0)
            bhi.append(np.percentile(d, 75) / 60.0)
        ax.plot(bx, bmed, "-o", color=colors[rid], lw=2, label=f"系統{rid} 中央値遅延")
        ax.fill_between(bx, blo, bhi, color=colors[rid], alpha=0.12)
        # 終点は別マーカー（★）で明示
        if term.sum() >= 5:
            tv = np.median(dl[term]) / 60.0
            ax.scatter([1.0], [tv], marker="*", s=260, color=colors[rid],
                       edgecolors="k", zorder=5)
    ax.axhline(0, color="gray", lw=1)
    ax.set_ylim(-4.5, 7)
    ax.set_xlabel("ルート上の位置（0=始発, 1=終点）★=終点")
    ax.set_ylabel("時刻表に対する遅延 [分]（+遅れ / −早着）")
    ax.set_title("ルート位置と遅延 — 中間は恒常的に「遅れ」て見え、終点で早着に転じる\n"
                 "（＝終点で合わせる余裕ダイヤ／中間停留所の時刻表は早めに設定）", fontsize=12)
    ax.annotate("中間〜終点手前:\n恒常的に2〜3分「遅れ」て見える\n(≒時刻表パディング)",
                xy=(0.3, 5.2), fontsize=9.5, color="#444")
    ax.annotate("終点(★): 早着\n= ダイヤの余裕が集約", xy=(0.72, -3.6), fontsize=9.5, color="#b22")
    ax.grid(True, alpha=0.3); ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_pos_delay.png"), dpi=150)
    plt.close(fig)


# ============ 2. 停留所別 指標 + 分類 ============
def stop_metrics(rows):
    # 「特定の便を待つ乗客」の視点で測る。
    # (停留所×便) ごとに日々の遅延を集め、その便の日々のばらつき(within-IQR)・
    # 早発率を出す。停留所へは便を跨いで n 重み平均で集約する。
    # こうするとパディング（便内での位置による系統的ズレ）はグループ内の中央値
    # (bias) に吸収され、ばらつき(unreliability)から切り離せる。
    # 始発・終点は「到着を待つ」対象でないため除外（中間停留所のみ）。
    grp = defaultdict(list)       # (stop_id, trip_id) -> [delays]
    coord, fracby = {}, defaultdict(list)
    for r in rows:
        if r["is_terminal"] or r["is_origin"]:
            continue
        grp[(r["stop_id"], r["trip_id"])].append(r["delay_sec"])
        coord[r["stop_id"]] = (r["stop_lat"], r["stop_lon"])
        if r["frac"] is not None:
            fracby[r["stop_id"]].append(r["frac"])

    perstop = defaultdict(lambda: {"n": 0, "iqr_w": 0.0, "early_w": 0.0,
                                   "late2_w": 0.0, "bias": [], "all": []})
    for (sid, tid), d in grp.items():
        d = np.array(d)
        if len(d) < 5:           # 便あたり最低日数
            continue
        wiqr = np.percentile(d, 75) - np.percentile(d, 25)   # 日々のばらつき
        wearly = np.mean(d < EARLY_S)
        wlate2 = np.mean(d > LATE2_S)
        ps = perstop[sid]
        ps["n"] += len(d)
        ps["iqr_w"] += wiqr * len(d)
        ps["early_w"] += wearly * len(d)
        ps["late2_w"] += wlate2 * len(d)
        ps["bias"].append(np.median(d))
        ps["all"].extend(d.tolist())

    recs = []
    for sid, ps in perstop.items():
        if ps["n"] < MIN_N:
            continue
        n = ps["n"]
        recs.append({
            "stop_id": sid, "name": STOP_NAME.get(sid, "?"), "n": n,
            "median": float(np.median(ps["all"])),          # 恒常オフセット(パディング含む)
            "iqr": float(ps["iqr_w"] / n),                  # 便内の日々ばらつき
            "early_rate": float(ps["early_w"] / n),
            "late2_rate": float(ps["late2_w"] / n),
            "frac": float(np.median(fracby[sid])) if fracby[sid] else None,
            "lat": coord[sid][0], "lon": coord[sid][1],
        })
    # 主指標＝ばらつきIQR（便を待つ乗客の日々の予測困難さ）。
    # 早発率は乗り逃しリスクの副次フラグ。
    for r in recs:
        r["unreliability"] = r["iqr"]                 # 並べ替え・色の主指標(秒)
        r["early_flag"] = r["early_rate"] >= 0.08     # 早発リスク停留所
        if r["early_flag"]:
            r["category"] = "早発リスク(乗り逃し)"
        elif r["iqr"] >= 180:                         # 日々3分以上ばらつく
            r["category"] = "不安定(予測困難)"
        elif r["median"] >= 150:                      # 恒常的に2.5分以上遅い・低ばらつき
            r["category"] = "恒常的遅れ(時刻表パディング疑い)"
        else:
            r["category"] = "安定"
    recs.sort(key=lambda x: -x["unreliability"])
    return recs


def fig_map(recs):
    fig, ax = plt.subplots(figsize=(9.5, 9))
    for pts in pc["shapes"].values():
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts], "-",
                color="#dddddd", lw=1.2, zorder=1)
    u = np.array([r["iqr"] / 60.0 for r in recs])   # 分
    norm = plt.Normalize(0, max(4.0, np.percentile(u, 95)))
    cmap = plt.get_cmap("RdYlGn_r")
    for r in recs:
        # 早発リスク停留所は赤い輪郭で強調
        edge = "#000000"; ew = 0.5
        if r["early_flag"]:
            edge = "#0000ff"; ew = 2.2
        ax.scatter(r["lon"], r["lat"], s=70 + r["n"] / 8.0,
                   c=[cmap(norm(r["iqr"] / 60.0))], edgecolors=edge,
                   linewidths=ew, zorder=3)
    # ばらつき上位 or 早発フラグ の停留所に名前
    labeled = sorted(recs, key=lambda x: -x["iqr"])[:5] + [r for r in recs if r["early_flag"]]
    for r in {id(x): x for x in labeled}.values():
        ax.annotate(r["name"], (r["lon"], r["lat"]), fontsize=8,
                    xytext=(4, 4), textcoords="offset points", zorder=4)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap); sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("到着遅延の日々のばらつき IQR [分]（濃いほど予測困難）")
    ax.set_title("「予定通り来ない」と感じやすい停留所\n"
                 "色=日々のばらつき(予測困難さ) / 青枠=早発リスク(乗り逃し)", fontsize=12)
    ax.set_xlabel("経度"); ax.set_ylabel("緯度")
    ax.set_aspect(1.0 / math.cos(math.radians(35.77)))
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_unreliability_map.png"), dpi=150)
    plt.close(fig)


def fig_bar(recs):
    top = recs[:15]
    catcol = {"不安定(予測困難)": "#d62728", "早発リスク(乗り逃し)": "#9467bd",
              "恒常的遅れ(時刻表パディング疑い)": "#ff7f0e", "安定": "#2ca02c"}
    fig, ax = plt.subplots(figsize=(10, 6.2))
    y = np.arange(len(top))[::-1]
    iqrm = [r["iqr"] / 60.0 for r in top]
    cols = [catcol.get(r["category"], "#888") for r in top]
    ax.barh(y, iqrm, color=cols)
    for i, r in zip(y, top):
        tag = "  ★早発" if r["early_flag"] else ""
        ax.text(r["iqr"] / 60.0 + 0.05, i,
                f"中央{round(r['median']/60,1)}分{tag}", va="center", fontsize=8, color="#333")
    ax.set_yticks(y)
    ax.set_yticklabels([r["name"] for r in top], fontsize=9)
    ax.set_xlabel("到着遅延の日々のばらつき IQR [分]（大きいほど予測困難）")
    ax.set_xlim(0, max(iqrm) * 1.25)
    ax.set_title("「予定通り来ない」と感じやすい停留所 上位15（日々のばらつき順）", fontsize=13)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=l) for l, c in catcol.items()],
              loc="lower right", fontsize=8.5)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_top_unreliable.png"), dpi=150)
    plt.close(fig)


# ============ 3. 終点手前→終点のダイヤ余裕（walk-faster）============
def last_segment(rows):
    # 便ごとに、終点手前(N-1)と終点(N)の予定/実測から最終区間の余裕を出す
    tp = pc["trip_progress"]
    recs = []
    # trip -> 最終2停留所の予定所要
    for tid, prog in tp.items():
        if len(prog) < 2:
            continue
        pen, term = prog[-2], prog[-1]
        sched_gap = term["time_sec"] - pen["time_sec"]
        dist_gap = term["distance_m"] - pen["distance_m"]
        recs.append({"trip_id": tid, "route_id": gtfs["trips"][tid]["route_id"],
                     "pen_stop": STOP_NAME.get(pen["stop_id"], "?"),
                     "term_stop": STOP_NAME.get(term["stop_id"], "?"),
                     "sched_gap_s": int(sched_gap), "dist_m": round(dist_gap, 0)})
    # 系統ごとに代表値
    byroute = defaultdict(list)
    for r in recs:
        byroute[r["route_id"]].append(r)
    out_rows = []
    for rid in sorted(byroute):
        rs = byroute[rid]
        sg = np.array([r["sched_gap_s"] for r in rs])
        dm = np.array([r["dist_m"] for r in rs])
        # 徒歩所要(80m/分=1.33m/s)と自転車的な実走(記録的な平均速度は別途)
        walk = dm / (80.0 / 60.0)   # 秒
        out_rows.append([rid, rs[0]["pen_stop"], rs[0]["term_stop"],
                         int(np.median(sg)), int(np.median(dm)),
                         int(np.median(walk)),
                         "徒歩の方が速い可能性" if np.median(walk) < np.median(sg) else "-"])
    return out_rows


def main():
    rows = load_rows()
    out(f"stop events: {len(rows)}")

    fig_position(rows)
    out("fig_pos_delay.png 生成")

    recs = stop_metrics(rows)
    fig_map(recs); fig_bar(recs)
    out("fig_unreliability_map.png / fig_top_unreliable.png 生成")

    # 表CSV
    header = ["stop_id", "停留所名", "n", "中央値遅延s", "ばらつきIQRs",
              "早発率%", "2分超遅延率%", "ルート位置", "体感不安定度", "分類"]
    data = [[r["stop_id"], r["name"], r["n"], round(r["median"]),
             round(r["iqr"]), round(100 * r["early_rate"], 1),
             round(100 * r["late2_rate"], 1),
             round(r["frac"], 2) if r["frac"] is not None else "",
             round(r["unreliability"], 2), r["category"]] for r in recs]
    write_csv("stop_reliability.csv", header, data)

    out("")
    out("=== 「予定通り来ない」と感じやすい停留所 上位12（日々のばらつきIQR順）===")
    out("  中央遅延=恒常オフセット(多くはパディング) / ばらつき=便を待つ乗客の日々の予測困難さ")
    out(f"{'停留所':<16}{'n':>5}{'中央遅延':>8}{'ばらつき':>8}{'早発%':>7}{'分類':<24}")
    for r in recs[:12]:
        out(f"{r['name']:<16}{r['n']:>5}{round(r['median']):>7}s{round(r['iqr']):>7}s"
            f"{round(100*r['early_rate'],1):>7}  {r['category']}")

    # last segment
    ls = last_segment(rows)
    write_csv("last_segment_padding.csv",
              ["route", "終点手前", "終点", "予定所要s", "距離m", "徒歩推定s", "判定"], ls)
    out("")
    out("=== 終点手前→終点 のダイヤ余裕（walk-faster 検証）===")
    out(f"{'系統':>4}{'区間':<28}{'予定所要':>8}{'距離m':>7}{'徒歩推定':>8}  判定")
    for r in ls:
        out(f"{r[0]:>4}  {r[1]}→{r[2]:<16}{r[3]:>6}s{r[4]:>7}{r[5]:>7}s  {r[6]}")

    with open(os.path.join(OUT, "reliability_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    out("")
    out("written to " + OUT)


if __name__ == "__main__":
    main()
