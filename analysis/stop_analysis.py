"""
停留所到着イベント(stop_events)に基づく、町の要望への回答分析。

要望:
  (1) どこで遅れが生じやすいか
  (2) 駅到着の遅延状況
  (3) 天候による遅延差（雨天は遅延しやすいか。GPSでなく遅延）
  (4) 曜日による差異
  (5) 住民の声（時間通りに来ない）の要因＝定時性が悪い停留所
  (6) 早発の発生状況（別資料化）

定義:
  遅延 delay_sec = 実通過時刻 - 時刻表時刻（+が遅れ、-が早い）
  定刻: -60〜+120秒 / 遅延: +120秒超 / 早発: -60秒未満（1分以上早い通過）
出力: analysis/out/req_*.csv, req_summary.txt, early_departures.csv（別資料）
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
SE = f"`{PROJECT}.bus_gps_data.stop_events`"
WX = f"`{PROJECT}.bus_gps_data.weather`"

lines = []
def out(s=""):
    lines.append(s); print(s)

def q(sql):
    return list(client.query(sql).result())

def write_csv(name, header, rows):
    with open(os.path.join(OUT, name), "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join("" if v is None else str(v) for v in r) + "\n")


def main():
    # 全体
    out("=== 全体（停留所到着イベント）===")
    r = q(f"""SELECT COUNT(*) n,
      CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d,
      APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med_d,
      ROUND(100*COUNTIF(delay_sec BETWEEN -60 AND 120)/COUNT(*),1) pct_ontime,
      ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late,
      ROUND(100*COUNTIF(delay_sec< -60)/COUNT(*),1) pct_early
      FROM {SE}""")[0]
    out(f"n={r['n']} 平均遅延={r['mean_d']}s 中央={r['med_d']}s / 定刻{r['pct_ontime']}% 遅延{r['pct_late']}% 早発{r['pct_early']}%")

    # (1) 遅れが生じやすい地点（中央値ベース: 平均は外れ値に弱いため）
    out("")
    out("=== (1) 遅れが生じやすい停留所 上位15（中央値遅延順, n>=50）===")
    rows = q(f"""SELECT stop_id, ANY_VALUE(stop_name) nm, COUNT(*) n,
      APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med_d,
      CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d,
      ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late,
      ROUND(100*COUNTIF(delay_sec>300)/COUNT(*),1) pct_late5m
      FROM {SE} GROUP BY stop_id HAVING n>=50 ORDER BY med_d DESC LIMIT 15""")
    out(f"{'stop':<7}{'停留所名':<18}{'n':>5}{'中央s':>7}{'平均s':>7}{'2分超%':>8}{'5分超%':>8}")
    csv=[["stop_id","stop_name","n","median_delay_s","mean_delay_s","pct_late2m","pct_late5m"]]
    for r in rows:
        out(f"{r['stop_id']:<7}{r['nm']:<18}{r['n']:>5}{r['med_d']:>7}{r['mean_d']:>7}{r['pct_late']:>8}{r['pct_late5m']:>8}")
        csv.append([r['stop_id'],r['nm'],r['n'],r['med_d'],r['mean_d'],r['pct_late'],r['pct_late5m']])
    write_csv("req1_delay_by_stop.csv", csv[0], csv[1:])

    # (2) 駅到着の遅延
    out("")
    out("=== (2) 駅到着の遅延状況（箱根ケ崎駅）===")
    rows = q(f"""SELECT stop_id, ANY_VALUE(stop_name) nm, COUNT(*) n,
      CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d,
      APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med_d,
      APPROX_QUANTILES(delay_sec,100)[OFFSET(90)] p90_d,
      ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late,
      ROUND(100*COUNTIF(delay_sec>300)/COUNT(*),1) pct_late5m
      FROM {SE} WHERE is_station GROUP BY stop_id ORDER BY nm""")
    out(f"{'stop':<7}{'駅':<16}{'n':>5}{'平均s':>7}{'中央s':>7}{'P90s':>7}{'2分超%':>8}{'5分超%':>8}")
    csv=[["stop_id","stop_name","n","mean_delay_s","median_delay_s","p90_delay_s","pct_late2m","pct_late5m"]]
    for r in rows:
        out(f"{r['stop_id']:<7}{r['nm']:<16}{r['n']:>5}{r['mean_d']:>7}{r['med_d']:>7}{r['p90_d']:>7}{r['pct_late']:>8}{r['pct_late5m']:>8}")
        csv.append([r['stop_id'],r['nm'],r['n'],r['mean_d'],r['med_d'],r['p90_d'],r['pct_late'],r['pct_late5m']])
    # 駅=終点着 vs 駅=始発発 の区別も
    out("  （駅の到着/出発別）")
    rows2 = q(f"""SELECT ANY_VALUE(stop_name) nm,
        CASE WHEN is_origin THEN '始発発' WHEN is_terminal THEN '終点着' ELSE '経由' END kind,
        COUNT(*) n, CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d
      FROM {SE} WHERE is_station GROUP BY stop_id, kind ORDER BY nm, kind""")
    for r in rows2:
        out(f"    {r['nm']:<16}{r['kind']:<6}n={r['n']:>4} 平均{r['mean_d']}s")
    write_csv("req2_station_delay.csv", csv[0], csv[1:])

    # (3) 天候による遅延差（平日のみに限定して曜日交絡を除去）
    out("")
    out("=== (3) 天候による遅延差（遅延で評価。平日のみ）===")
    rows = q(f"""WITH j AS (
        SELECT s.*, w.rain_mm, w.is_rain_day,
          CASE WHEN w.rain_mm=0 THEN '0_晴' WHEN w.rain_mm<10 THEN '1_雨(0-10mm)' ELSE '2_大雨(10mm+)' END grp
        FROM {SE} s JOIN {WX} w ON s.date=w.date
        WHERE EXTRACT(DAYOFWEEK FROM s.date) BETWEEN 2 AND 6  -- 月〜金
      )
      SELECT grp, COUNT(DISTINCT date) days, COUNT(*) n,
        CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d,
        APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med_d,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late,
        ROUND(100*COUNTIF(delay_sec>300)/COUNT(*),1) pct_late5m
      FROM j GROUP BY grp ORDER BY grp""")
    out(f"{'天候':<14}{'日数':>4}{'n':>6}{'平均s':>7}{'中央s':>7}{'2分超%':>8}{'5分超%':>8}")
    csv=[["weather_group","days","n","mean_delay_s","median_delay_s","pct_late2m","pct_late5m"]]
    for r in rows:
        out(f"{r['grp']:<14}{r['days']:>4}{r['n']:>6}{r['mean_d']:>7}{r['med_d']:>7}{r['pct_late']:>8}{r['pct_late5m']:>8}")
        csv.append([r['grp'],r['days'],r['n'],r['mean_d'],r['med_d'],r['pct_late'],r['pct_late5m']])
    write_csv("req3_delay_by_weather.csv", csv[0], csv[1:])

    # (4) 曜日別差異
    out("")
    out("=== (4) 曜日別の遅延差 ===")
    rows = q(f"""SELECT EXTRACT(DAYOFWEEK FROM date) dow, COUNT(DISTINCT date) days, COUNT(*) n,
        CAST(ROUND(AVG(delay_sec)) AS INT64) mean_d,
        APPROX_QUANTILES(delay_sec,100)[OFFSET(50)] med_d,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late
      FROM {SE} GROUP BY dow ORDER BY dow""")
    wd={1:'日',2:'月',3:'火',4:'水',5:'木',6:'金',7:'土'}
    out(f"{'曜日':<5}{'日数':>4}{'n':>6}{'平均s':>7}{'中央s':>7}{'2分超%':>8}")
    csv=[["dow","weekday","days","n","mean_delay_s","median_delay_s","pct_late2m"]]
    for r in rows:
        out(f"{wd[r['dow']]:<5}{r['days']:>4}{r['n']:>6}{r['mean_d']:>7}{r['med_d']:>7}{r['pct_late']:>8}")
        csv.append([r['dow'],wd[r['dow']],r['days'],r['n'],r['mean_d'],r['med_d'],r['pct_late']])
    write_csv("req4_delay_by_weekday.csv", csv[0], csv[1:])

    # (5) 住民の声＝定時性が悪い停留所（遅延ばらつき・早発・遅延を総合）
    out("")
    out("=== (5) 定時性が悪い停留所 上位15（住民の声の要因; 定刻率が低い順, n>=50）===")
    rows = q(f"""SELECT stop_id, ANY_VALUE(stop_name) nm, COUNT(*) n,
        ROUND(100*COUNTIF(delay_sec BETWEEN -60 AND 120)/COUNT(*),1) pct_ontime,
        ROUND(100*COUNTIF(delay_sec>120)/COUNT(*),1) pct_late,
        ROUND(100*COUNTIF(delay_sec< -60)/COUNT(*),1) pct_early,
        CAST(ROUND(STDDEV(delay_sec)) AS INT64) sd
      FROM {SE} GROUP BY stop_id HAVING n>=50 ORDER BY pct_ontime ASC LIMIT 15""")
    out(f"{'stop':<7}{'停留所名':<18}{'n':>5}{'定刻%':>7}{'遅延%':>7}{'早発%':>7}{'ばらつきs':>9}")
    csv=[["stop_id","stop_name","n","pct_ontime","pct_late","pct_early","stddev_s"]]
    for r in rows:
        out(f"{r['stop_id']:<7}{r['nm']:<18}{r['n']:>5}{r['pct_ontime']:>7}{r['pct_late']:>7}{r['pct_early']:>7}{r['sd']:>9}")
        csv.append([r['stop_id'],r['nm'],r['n'],r['pct_ontime'],r['pct_late'],r['pct_early'],r['sd']])
    write_csv("req5_punctuality_by_stop.csv", csv[0], csv[1:])

    # (6) 早発の発生状況
    out("")
    out("=== (6) 早発の発生状況 ===")
    r = q(f"""SELECT COUNT(*) n_all,
        COUNTIF(delay_sec< -60) n_early1,
        COUNTIF(delay_sec< -120) n_early2,
        COUNTIF(delay_sec< -60 AND is_origin) n_early_origin
      FROM {SE}""")[0]
    out(f"全到着イベント {r['n_all']} 中: 1分以上早発 {r['n_early1']}件({round(100*r['n_early1']/r['n_all'],1)}%), "
        f"2分以上早発 {r['n_early2']}件, うち始発の早発 {r['n_early_origin']}件")
    out("  早発が多い停留所 上位10:")
    rows = q(f"""SELECT stop_id, ANY_VALUE(stop_name) nm, COUNT(*) n,
        COUNTIF(delay_sec< -60) n_early,
        ROUND(100*COUNTIF(delay_sec< -60)/COUNT(*),1) pct_early
      FROM {SE} GROUP BY stop_id HAVING n>=50 ORDER BY pct_early DESC LIMIT 10""")
    for r in rows:
        out(f"    {r['stop_id']:<7}{r['nm']:<18}n={r['n']:>4} 早発{r['n_early']:>4}件 ({r['pct_early']}%)")
    # 早発イベント詳細（別資料CSV）
    rows = q(f"""SELECT CAST(date AS STRING) date, run_id, trip_id, route_id, stop_id, stop_name, stop_seq,
        sched_sec, actual_sec, delay_sec, is_origin, is_station
      FROM {SE} WHERE delay_sec < -60 ORDER BY delay_sec ASC""")
    csv=[["date","run_id","trip_id","route_id","stop_id","stop_name","stop_seq","sched_sec","actual_sec","delay_sec","is_origin","is_station"]]
    for r in rows:
        csv.append([r['date'],r['run_id'],r['trip_id'],r['route_id'],r['stop_id'],r['stop_name'],r['stop_seq'],r['sched_sec'],r['actual_sec'],r['delay_sec'],r['is_origin'],r['is_station']])
    write_csv("early_departures.csv", csv[0], csv[1:])
    out(f"  → 早発イベント {len(csv)-1}件を early_departures.csv に別資料出力")

    with open(os.path.join(OUT, "req_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    out("")
    out("written to " + OUT)


if __name__ == "__main__":
    main()
