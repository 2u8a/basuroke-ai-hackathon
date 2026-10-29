"""
運行データからの示唆（報告書用集計）

すべて「正解便推定(ground_truth)」に基づく:
  - 遅延分析は gt_delay_sec（正解便に対する実際の遅延）を使用。当時システムが
    出していた tm_delay_sec は使わない。
  - 遅延の集計は gt_in_service AND gt_confident の点のみ（人工物除外）。

分析:
  A. 遅延分析（時間帯別 / 停留所別 / 系統別）
  B. GPS品質（accuracy分布 / 地域別 / reject理由）
  C. 雨天 vs 晴天の差（遅延 / 便精度 / GPS品質）

出力: analysis/out/insights_*.csv / insights_summary.txt
"""
import os
import sys
from google.cloud import bigquery

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G

PROJECT = "mizuho-basuroke-f370e"
OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)
client = bigquery.Client(project=PROJECT)

# 停留所名（GTFSから、文字化け回避のため直接）
GTFS = G.load_gtfs()
STOP_NAME = {sid: s["name"] for sid, s in GTFS["stops"].items()}


def q(sql):
    return list(client.query(sql).result())


def recreate_view():
    sql = open(os.path.join(os.path.dirname(__file__), "sql", "02_create_gps_eval_view.sql"),
               encoding="utf-8").read()
    client.query(sql).result()


def write_csv(name, header, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join("" if v is None else str(v) for v in r) + "\n")
    return path


EVAL = f"`{PROJECT}.bus_gps_data.gps_eval`"
WX = f"`{PROJECT}.bus_gps_data.weather`"

lines = []
def out(s=""):
    lines.append(s)
    print(s)


def main():
    recreate_view()

    # ============ A. 遅延分析（gt_delay_sec ベース）============
    out("=== A1. 時間帯別の遅延（正解ベース, 運行中・高信頼のみ）===")
    rows = q(f"""
      SELECT jst_hour,
        COUNT(*) n,
        CAST(ROUND(AVG(gt_delay_sec)) AS INT64) mean_delay,
        CAST(ROUND(APPROX_QUANTILES(gt_delay_sec, 100)[OFFSET(50)]) AS INT64) med_delay,
        CAST(ROUND(APPROX_QUANTILES(gt_delay_sec, 100)[OFFSET(90)]) AS INT64) p90_delay,
        ROUND(100*COUNTIF(gt_delay_sec >= 300)/COUNT(*),1) pct_late5min
      FROM {EVAL}
      WHERE gt_in_service AND gt_confident AND gt_delay_sec IS NOT NULL
      GROUP BY jst_hour ORDER BY jst_hour
    """)
    out(f"{'時':>3}{'n':>7}{'平均遅延s':>9}{'中央s':>7}{'P90s':>7}{'5分超%':>8}")
    csvA1 = [["hour", "n", "mean_delay_s", "median_delay_s", "p90_delay_s", "pct_late5min"]]
    for r in rows:
        out(f"{r['jst_hour']:>3}{r['n']:>7}{r['mean_delay']:>10}{r['med_delay']:>9}{r['p90_delay']:>8}{r['pct_late5min']:>8}")
        csvA1.append([r['jst_hour'], r['n'], r['mean_delay'], r['med_delay'], r['p90_delay'], r['pct_late5min']])
    write_csv("insights_delay_by_hour.csv", csvA1[0], csvA1[1:])

    out("")
    out("=== A2. 停留所別の遅延 上位15（遅れが出やすい地点）===")
    rows = q(f"""
      SELECT gt_nearest_stop_id,
        COUNT(*) n,
        CAST(ROUND(AVG(gt_delay_sec)) AS INT64) mean_delay,
        CAST(ROUND(APPROX_QUANTILES(gt_delay_sec, 100)[OFFSET(50)]) AS INT64) med_delay
      FROM {EVAL}
      WHERE gt_in_service AND gt_confident AND gt_delay_sec IS NOT NULL
        AND gt_nearest_stop_id IS NOT NULL
      GROUP BY gt_nearest_stop_id
      HAVING n >= 100
      ORDER BY mean_delay DESC LIMIT 15
    """)
    out(f"{'stop_id':<8}{'停留所名':<18}{'n':>6}{'平均遅延s':>9}{'中央s':>7}")
    csvA2 = [["stop_id", "stop_name", "n", "mean_delay_s", "median_delay_s"]]
    for r in rows:
        nm = STOP_NAME.get(r['gt_nearest_stop_id'], '?')
        out(f"{r['gt_nearest_stop_id']:<8}{nm:<18}{r['n']:>6}{r['mean_delay']:>9}{r['med_delay']:>7}")
        csvA2.append([r['gt_nearest_stop_id'], nm, r['n'], r['mean_delay'], r['med_delay']])
    write_csv("insights_delay_by_stop.csv", csvA2[0], csvA2[1:])

    out("")
    out("=== A3. 系統別の遅延 ===")
    rows = q(f"""
      SELECT gt_route_id,
        COUNT(*) n,
        CAST(ROUND(AVG(gt_delay_sec)) AS INT64) mean_delay,
        CAST(ROUND(APPROX_QUANTILES(gt_delay_sec, 100)[OFFSET(50)]) AS INT64) med_delay,
        ROUND(100*COUNTIF(gt_delay_sec >= 300)/COUNT(*),1) pct_late5min
      FROM {EVAL}
      WHERE gt_in_service AND gt_confident AND gt_delay_sec IS NOT NULL
      GROUP BY gt_route_id ORDER BY gt_route_id
    """)
    out(f"{'系統':>4}{'n':>8}{'平均遅延s':>9}{'中央s':>7}{'5分超%':>8}")
    for r in rows:
        out(f"{str(r['gt_route_id']):>4}{r['n']:>8}{r['mean_delay']:>9}{r['med_delay']:>7}{r['pct_late5min']:>8}")

    # ============ B. GPS品質 ============
    out("")
    out("=== B1. GPS accuracy 分布（全受信、accepted判定前の生値含む）===")
    rows = q(f"""
      SELECT
        COUNT(*) n,
        ROUND(AVG(accuracy),1) mean_acc,
        APPROX_QUANTILES(accuracy,100)[OFFSET(50)] med_acc,
        APPROX_QUANTILES(accuracy,100)[OFFSET(90)] p90_acc,
        ROUND(100*COUNTIF(accuracy > 30)/COUNT(*),1) pct_gt30m,
        ROUND(100*COUNTIF(accuracy > 50)/COUNT(*),1) pct_gt50m
      FROM `{PROJECT}.bus_gps_data.gps_flat`
      WHERE accuracy IS NOT NULL
    """)
    r = rows[0]
    out(f"n={r['n']} 平均={r['mean_acc']}m 中央={r['med_acc']}m P90={r['p90_acc']}m  >30m:{r['pct_gt30m']}%  >50m:{r['pct_gt50m']}%")

    out("")
    out("=== B2. 停留所別 GPS accuracy 悪い順 上位15（GPS弱点地域）===")
    rows = q(f"""
      SELECT gt_nearest_stop_id,
        COUNT(*) n,
        ROUND(AVG(accuracy),1) mean_acc,
        ROUND(100*COUNTIF(accuracy > 30)/COUNT(*),1) pct_gt30m
      FROM {EVAL}
      WHERE accuracy IS NOT NULL AND gt_nearest_stop_id IS NOT NULL
      GROUP BY gt_nearest_stop_id HAVING n >= 100
      ORDER BY mean_acc DESC LIMIT 15
    """)
    out(f"{'stop_id':<8}{'停留所名':<20}{'n':>6}{'平均acc(m)':>11}{'>30m%':>7}")
    csvB2 = [["stop_id", "stop_name", "n", "mean_accuracy_m", "pct_gt30m"]]
    for r in rows:
        nm = STOP_NAME.get(r['gt_nearest_stop_id'], '?')
        out(f"{r['gt_nearest_stop_id']:<8}{nm:<20}{r['n']:>6}{r['mean_acc']:>11}{r['pct_gt30m']:>7}")
        csvB2.append([r['gt_nearest_stop_id'], nm, r['n'], r['mean_acc'], r['pct_gt30m']])
    write_csv("insights_gps_by_stop.csv", csvB2[0], csvB2[1:])

    out("")
    out("=== B3. reject理由の内訳 ===")
    rows = q(f"""
      SELECT reject_reason, COUNT(*) n
      FROM `{PROJECT}.bus_gps_data.gps_flat`
      WHERE accepted = false
      GROUP BY reject_reason ORDER BY n DESC
    """)
    for r in rows:
        out(f"  {str(r['reject_reason']):<28}{r['n']:>7}")

    # ============ C. 雨天 vs 晴天 ============
    out("")
    out("=== C. 雨天日 vs 非雨天日（正解ベース）===")
    rows = q(f"""
      WITH j AS (
        SELECT e.*, w.rain_mm, w.is_rain_day, w.daytime_rain
        FROM {EVAL} e
        JOIN {WX} w ON e.jst_date = w.date
      )
      SELECT
        is_rain_day,
        COUNT(DISTINCT jst_date) days,
        ROUND(AVG(CASE WHEN gt_in_service AND gt_confident THEN gt_delay_sec END),1) mean_delay,
        ROUND(100*SUM(CASE WHEN gt_in_service AND gt_confident THEN trip_match_correct END)
              /NULLIF(COUNTIF(gt_in_service AND gt_confident),0),1) trip_acc,
        ROUND(AVG(accuracy),1) mean_acc,
        ROUND(100*COUNTIF(accuracy>30)/COUNT(*),1) pct_gps_gt30m
      FROM j
      GROUP BY is_rain_day ORDER BY is_rain_day
    """)
    out(f"{'雨天':>5}{'日数':>5}{'平均遅延s':>10}{'便精度%':>9}{'平均acc':>9}{'GPS>30m%':>10}")
    csvC = [["is_rain_day", "days", "mean_delay_s", "trip_acc_pct", "mean_accuracy_m", "pct_gps_gt30m"]]
    for r in rows:
        out(f"{str(r['is_rain_day']):>5}{r['days']:>5}{str(r['mean_delay']):>10}{str(r['trip_acc']):>9}{str(r['mean_acc']):>9}{str(r['pct_gps_gt30m']):>10}")
        csvC.append([r['is_rain_day'], r['days'], r['mean_delay'], r['trip_acc'], r['mean_acc'], r['pct_gps_gt30m']])
    write_csv("insights_rain_vs_clear.csv", csvC[0], csvC[1:])

    out("")
    out("=== C2. 雨量段階別 ===")
    rows = q(f"""
      WITH j AS (
        SELECT e.*, w.rain_mm,
          CASE WHEN w.rain_mm=0 THEN '0_晴' WHEN w.rain_mm<5 THEN '1_少雨(0-5mm)'
               WHEN w.rain_mm<15 THEN '2_雨(5-15mm)' ELSE '3_大雨(15mm+)' END grp
        FROM {EVAL} e JOIN {WX} w ON e.jst_date=w.date
      )
      SELECT grp,
        COUNT(DISTINCT jst_date) days,
        ROUND(AVG(CASE WHEN gt_in_service AND gt_confident THEN gt_delay_sec END),1) mean_delay,
        ROUND(100*SUM(CASE WHEN gt_in_service AND gt_confident THEN trip_match_correct END)
              /NULLIF(COUNTIF(gt_in_service AND gt_confident),0),1) trip_acc,
        ROUND(AVG(accuracy),1) mean_acc
      FROM j GROUP BY grp ORDER BY grp
    """)
    out(f"{'雨量段階':<16}{'日数':>5}{'平均遅延s':>10}{'便精度%':>9}{'平均acc':>9}")
    for r in rows:
        out(f"{r['grp']:<16}{r['days']:>5}{str(r['mean_delay']):>10}{str(r['trip_acc']):>9}{str(r['mean_acc']):>9}")

    out("")
    out("=== C3. 雨天 vs 晴天（アルゴリズム安定期 v3.3: 2026-05-10〜05-30 に限定）===")
    out("   ※初期の雨天日はv3不安定期と重なり交絡するため、安定期に限定して比較")
    rows = q(f"""
      WITH j AS (
        SELECT e.*, w.rain_mm, w.is_rain_day
        FROM {EVAL} e JOIN {WX} w ON e.jst_date = w.date
        WHERE e.jst_date BETWEEN '2026-05-10' AND '2026-05-30'
      )
      SELECT is_rain_day,
        COUNT(DISTINCT jst_date) days,
        CAST(ROUND(AVG(CASE WHEN gt_in_service AND gt_confident THEN gt_delay_sec END)) AS INT64) mean_delay,
        ROUND(100*SUM(CASE WHEN gt_in_service AND gt_confident THEN trip_match_correct END)
              /NULLIF(COUNTIF(gt_in_service AND gt_confident),0),1) trip_acc,
        ROUND(AVG(accuracy),1) mean_acc,
        ROUND(100*COUNTIF(accuracy>30)/COUNT(*),1) pct_gps_gt30m
      FROM j GROUP BY is_rain_day ORDER BY is_rain_day
    """)
    out(f"{'雨天':>5}{'日数':>5}{'平均遅延s':>9}{'便精度%':>9}{'平均acc':>9}{'GPS>30m%':>10}")
    for r in rows:
        out(f"{str(r['is_rain_day']):>5}{r['days']:>5}{str(r['mean_delay']):>9}{str(r['trip_acc']):>9}{str(r['mean_acc']):>9}{str(r['pct_gps_gt30m']):>10}")

    with open(os.path.join(OUT, "insights_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    out("")
    out("written to " + OUT)


if __name__ == "__main__":
    main()
