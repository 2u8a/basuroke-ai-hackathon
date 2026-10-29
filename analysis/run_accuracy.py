"""
便推定精度の照合パイプライン

1. BigQuery の gps_flat から accepted 点を取得
2. ground_truth.infer_ground_truth で正解便を生成
3. 当時のリアルタイム推定（tm_trip_id / locked_trip_id）と点単位で照合
4. 期間別（アルゴリズム版）に精度を集計し、CSV / サマリを出力

出力先: analysis/out/
"""

import os
import sys
import json
import csv as _csv
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G

from google.cloud import bigquery

PROJECT = "mizuho-basuroke-f370e"
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT_DIR, exist_ok=True)

# アルゴリズム版の期間境界（PDCA記録より）。start<=date<=end。
ERAS = [
    ("pre_v3_再推定",      "2026-04-23", "2026-04-30"),
    ("v3_便固定",          "2026-05-01", "2026-05-08"),
    ("v3.2_始発保留",      "2026-05-09", "2026-05-09"),
    ("v3.3_topology",      "2026-05-10", "2026-05-30"),
]


def era_of(date_str):
    for name, s, e in ERAS:
        if s <= date_str <= e:
            return name
    return "out_of_period"


def fetch_points():
    client = bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT event_id, ts_unix, CAST(jst_date AS STRING) AS jst_date,
           lat, lon, accepted, accuracy,
           lock_state, locked_trip_id, tm_trip_id, tm_route_id,
           tm_delay_sec, tm_distance_error_m
    FROM `{PROJECT}.bus_gps_data.gps_flat`
    WHERE lat IS NOT NULL AND lon IS NOT NULL
    ORDER BY ts_unix
    """
    rows = list(client.query(sql).result())
    pts = []
    for r in rows:
        pts.append({
            "event_id": r["event_id"],
            "ts": int(r["ts_unix"]),
            "jst_date": r["jst_date"],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "accepted": bool(r["accepted"]),
            "accuracy": r["accuracy"],
            "lock_state": r["lock_state"],
            "locked_trip_id": r["locked_trip_id"],
            "tm_trip_id": r["tm_trip_id"],
            "tm_route_id": r["tm_route_id"],
            "tm_delay_sec": r["tm_delay_sec"],
            "tm_distance_error_m": r["tm_distance_error_m"],
        })
    return pts


def upload_ground_truth(gt):
    """正解を BigQuery の bus_gps_data.ground_truth テーブルへ保存（下流SQL用）。"""
    import pandas as pd
    rows = [{"event_id": ev, "gt_trip_id": g["gt_trip_id"],
             "gt_route_id": g["gt_route_id"], "gt_in_service": g["gt_in_service"],
             "gt_confident": g.get("gt_confident", False),
             "gt_delay_sec": g["gt_delay_sec"], "run_id": g["run_id"],
             "gt_nearest_stop_id": g.get("gt_nearest_stop_id")}
            for ev, g in gt.items()]
    df = pd.DataFrame(rows)
    client = bigquery.Client(project=PROJECT)
    table_id = f"{PROJECT}.bus_gps_data.ground_truth"
    job = client.load_table_from_dataframe(
        df, table_id,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
    job.result()
    print(f"  uploaded ground_truth: {len(df)} rows -> {table_id}")


def main():
    print("fetching points from BigQuery ...")
    pts = fetch_points()
    print(f"  fetched {len(pts)} rows")

    by_day = defaultdict(list)
    for p in pts:
        by_day[p["jst_date"]].append(p)

    gtfs = G.load_gtfs()
    pc = G.load_precomputed()

    print("inferring ground truth ...")
    gt, runs, _ddiag = G.infer_ground_truth(by_day, gtfs, pc)
    print(f"  runs detected: {len(runs)}")

    upload_ground_truth(gt)
    matched_runs = sum(1 for r in runs if r["trip_id"])
    print(f"  runs matched to a trip: {matched_runs}/{len(runs)}")

    trips = gtfs["trips"]

    # 点単位の照合
    # 当時の推定: est_trip = locked_trip_id if TRIP_LOCKED else tm_trip_id
    per_era = defaultdict(lambda: Counter())
    per_day = defaultdict(lambda: Counter())
    route_confusion = Counter()   # (gt_route, est_route)
    detail_rows = []

    for p in pts:
        if not p["accepted"]:
            continue
        g = gt.get(p["event_id"])
        if not g:
            continue
        era = era_of(p["jst_date"])
        day = p["jst_date"]
        dc = per_day[day]
        if g["gt_in_service"] and g.get("gt_confident", False):
            dc["gt_in"] += 1
            _est = p["locked_trip_id"] if p["lock_state"] == "TRIP_LOCKED" else p["tm_trip_id"]
            if _est == g["gt_trip_id"]:
                dc["correct"] += 1

        est_trip = p["locked_trip_id"] if p["lock_state"] == "TRIP_LOCKED" else p["tm_trip_id"]
        est_route = None
        if est_trip and est_trip in trips:
            est_route = trips[est_trip]["route_id"]
        elif p["tm_route_id"]:
            est_route = p["tm_route_id"]

        gt_in = g["gt_in_service"]
        gt_trip = g["gt_trip_id"]
        gt_route = g["gt_route_id"]

        c = per_era[era]
        c["total_accepted"] += 1
        # 低信頼の正解点は精度分母から除外（人工物混入を避ける）
        if gt_in and not g.get("gt_confident", False):
            c["gt_low_conf_excluded"] += 1
            continue
        if gt_in:
            c["gt_in_service"] += 1
            if est_trip is None:
                c["missed_no_estimate"] += 1     # 正解は運行中だが推定なし
            elif est_trip == gt_trip:
                c["trip_correct"] += 1
                if est_route == gt_route:
                    c["route_correct"] += 1
            else:
                c["trip_wrong"] += 1
                if est_route == gt_route:
                    c["route_correct_trip_wrong"] += 1  # 系統は合うが便違い
                route_confusion[(gt_route, est_route)] += 1
        else:
            c["gt_not_in_service"] += 1
            if est_trip is not None:
                c["false_positive"] += 1          # 正解は非運行だが推定あり

    # サマリ出力
    summary = {}
    for era, c in per_era.items():
        gt_in = c["gt_in_service"]
        summary[era] = {
            "total_accepted": c["total_accepted"],
            "gt_in_service": gt_in,
            "gt_low_conf_excluded": c["gt_low_conf_excluded"],
            "gt_not_in_service": c["gt_not_in_service"],
            "trip_correct": c["trip_correct"],
            "trip_wrong": c["trip_wrong"],
            "missed_no_estimate": c["missed_no_estimate"],
            "route_correct": c["route_correct"],
            "false_positive": c["false_positive"],
            "trip_accuracy_pct": round(100 * c["trip_correct"] / gt_in, 1) if gt_in else None,
            "route_accuracy_pct": round(100 * (c["route_correct"] + c["route_correct_trip_wrong"]) / gt_in, 1) if gt_in else None,
        }

    with open(os.path.join(OUT_DIR, "accuracy_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # runs 詳細CSV
    with open(os.path.join(OUT_DIR, "runs.csv"), "w", encoding="utf-8-sig", newline="") as f:
        if runs:
            keys = sorted({k for r in runs for k in r.keys()})
            w = _csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in runs:
                w.writerow(r)

    # route混同CSV
    with open(os.path.join(OUT_DIR, "route_confusion.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["gt_route", "est_route", "count"])
        for (gtr, estr), n in route_confusion.most_common():
            w.writerow([gtr, estr, n])

    # コンソール向けサマリ（UTF-8ファイルにも）
    lines = []
    lines.append("=== 便推定精度サマリ（期間別）===")
    hdr = f"{'era':<16}{'gt運行点':>9}{'便正解':>8}{'便精度%':>9}{'系統精度%':>10}{'推定漏れ':>9}{'誤検出':>8}"
    lines.append(hdr)
    for name, _, _ in ERAS:
        if name in summary:
            s = summary[name]
            lines.append(f"{name:<16}{s['gt_in_service']:>9}{s['trip_correct']:>8}"
                         f"{str(s['trip_accuracy_pct']):>9}{str(s['route_accuracy_pct']):>10}"
                         f"{s['missed_no_estimate']:>9}{s['false_positive']:>8}")
    out_txt = "\n".join(lines)
    with open(os.path.join(OUT_DIR, "accuracy_summary.txt"), "w", encoding="utf-8") as f:
        f.write(out_txt + "\n")

    # 日別精度CSV + テキスト
    dlines = ["date,era,gt_in_conf,correct,trip_accuracy_pct"]
    for day in sorted(per_day.keys()):
        dc = per_day[day]
        gin = dc["gt_in"]; cor = dc["correct"]
        acc = round(100 * cor / gin, 1) if gin else ""
        dlines.append(f"{day},{era_of(day)},{gin},{cor},{acc}")
    with open(os.path.join(OUT_DIR, "accuracy_by_day.csv"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(dlines) + "\n")

    print("written to", OUT_DIR)


if __name__ == "__main__":
    main()
