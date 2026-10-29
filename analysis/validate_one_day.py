"""正解生成の妥当性を1日分で目視検証する。"""
import os, sys, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
from google.cloud import bigquery

PROJECT = "mizuho-basuroke-f370e"
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-05-19"
OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)

client = bigquery.Client(project=PROJECT)
sql = f"""
SELECT event_id, ts_unix, CAST(jst_date AS STRING) jst_date, lat, lon, accepted,
       lock_state, locked_trip_id, tm_trip_id
FROM `{PROJECT}.bus_gps_data.gps_flat`
WHERE CAST(jst_date AS STRING) = '{DAY}' AND lat IS NOT NULL
ORDER BY ts_unix
"""
rows = list(client.query(sql).result())
pts = [{"event_id": r["event_id"], "ts": int(r["ts_unix"]), "jst_date": r["jst_date"],
        "lat": float(r["lat"]), "lon": float(r["lon"]), "accepted": bool(r["accepted"]),
        "lock_state": r["lock_state"], "locked_trip_id": r["locked_trip_id"],
        "tm_trip_id": r["tm_trip_id"]} for r in rows]

gtfs = G.load_gtfs(); pc = G.load_precomputed()
by_day = defaultdict(list)
for p in pts: by_day[p["jst_date"]].append(p)
gt, runs, ddiag = G.infer_ground_truth(by_day, gtfs, pc, diag_days={DAY})

lines = [f"=== {DAY} 正解生成検証 ==="]
lines.append(f"total points: {len(pts)}, accepted: {sum(1 for p in pts if p['accepted'])}")
lines.append(f"runs detected: {len(runs)}")
lines.append("")
lines.append(f"{'run_id':<16}{'trip_id':<18}{'route':<6}{'pts':>5}  {'start':<9}{'end':<9} med_delay")
from datetime import datetime, timezone, timedelta
JST = timezone(timedelta(hours=9))
def hhmm(ts): return datetime.fromtimestamp(ts, JST).strftime("%H:%M:%S")
for r in runs:
    s = hhmm(r["start_ts"]); e = hhmm(r["end_ts"])
    md = r.get("median_delay_sec", "-")
    tid = r["trip_id"] or "(none)"
    lines.append(f"{r['run_id']:<16}{tid:<18}{str(r['route_id'] or '-'):<6}{r['n_points']:>5}  {s:<9}{e:<9}{str(md):>8}")
lines.append(f"diag: {ddiag.get(DAY)}")

# 便固定era なら locked_trip_id との簡易一致も見る
lines.append("")
lines.append("=== 当時の推定(locked/tm) と 正解 の点単位一致（この日）===")
n_in=n_corr=n_wrong=n_miss=0
for p in pts:
    if not p["accepted"]: continue
    g = gt.get(p["event_id"])
    if not g or not g["gt_in_service"]: continue
    n_in += 1
    est = p["locked_trip_id"] if p["lock_state"]=="TRIP_LOCKED" else p["tm_trip_id"]
    if est is None: n_miss += 1
    elif est == g["gt_trip_id"]: n_corr += 1
    else: n_wrong += 1
lines.append(f"gt運行中点: {n_in}, 便一致: {n_corr}, 便不一致: {n_wrong}, 推定なし: {n_miss}")
if n_in: lines.append(f"便精度: {100*n_corr/n_in:.1f}%")

txt = "\n".join(lines)
open(os.path.join(OUT, f"validate_{DAY}.txt"), "w", encoding="utf-8").write(txt+"\n")
print("done ->", os.path.join(OUT, f"validate_{DAY}.txt"))
