"""
停留所到着イベント生成（正解便ベース）

各 run（正解でマッチした便）について、その便の時刻表上の各停留所を
バスが実際に通過した時刻を検出し、時刻表との差（到着遅延）を算出する。

方法（v3: 全域最小＋時刻表最近傍の谷）:
  - 便の各時刻表停留所（trip_progress: stop_id と 予定 time_sec）を運行順に処理する。
  - 各停留所について、探索カーソル以降・時刻窓内・許容内(150m)の点を「接近区間（谷）」に
    分割し、谷内の最接近（全域最小）±25mのプラトーの中で予定時刻に最も近い点を通過点とする。
    折り返し等で谷が複数あるときは、予定時刻に最も近い谷を採る。
    採用したらカーソルをその点へ進め、次の停留所は以降だけを探索する。
  - 遅延 = 実通過時刻(当日秒) - 予定時刻(当日秒)。

  ※ v2 は「最初の谷」を採っていたため、停留所が経路から離れ最接近が平坦に続く
    （例：進行方向に対し90度曲がった先に停留所がある）場合、プラトーの前縁を拾って
    実際の到着より早い時刻になり、瑞穂二本木(復)などで系統的な見かけの早着が出ていた。
    v3 はプラトー内を予定時刻最近傍で解消し、これを是正する。

  ※ 旧版は shape へ投影した路線上距離で最近傍点を選んでいたが、瑞穂町の各系統は
    「箱根ケ崎駅発・箱根ケ崎駅着」の循環で、駅前・役場前など往復で同一地点を通る。
    点独立の最小横ずれ投影（project_many）ではこの往復同一地点を弁別できず、復路
    （箱根ケ崎駅へ戻る区間）の停留所が往路側の距離に誤投影されて欠落していた。
    停留所座標そのものへの物理最接近＋カーソル前進なら、循環後半も正しく検出できる。

出力: BigQuery bus_gps_data.stop_events
  date, run_id, trip_id, route_id, stop_id, stop_seq, stop_name,
  sched_sec, actual_sec, delay_sec, is_origin, is_terminal, is_station, gap_dist_m
"""
import os
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np
from google.cloud import bigquery

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G

PROJECT = "mizuho-basuroke-f370e"
JST = timezone(timedelta(hours=9))
MATCH_TOL_M = 150.0    # 停留所座標への最接近がこの距離以内なら「通過」とみなす
WIN_EARLY_SEC = 600    # 予定時刻のこの秒数前から探索（早発側の余裕）
# 延着側の探索窓。往復で同一停留所を通る循環路線では、往路停留所の通過点が
# 欠損したとき、窓が広いと約44分後の復路の同名停留所の点を誤って拾い、
# 遅延が窓上限(旧2700秒=45分)付近に張り付く外れ値になっていた。
# 往復同一停留所の最短間隔（瑞穂二本木で約14分=840秒）より短い720秒に絞り、
# 誤マッチを防ぐ（実遅延は中央値数分なので取りこぼしは軽微）。
WIN_LATE_SEC = 720
# 全域最小の「プラトー」とみなす距離幅。最接近が平坦に続く（例：停留所が経路から
# 一定距離オフセットしている、90度コーナー先など）場合、最小距離±この幅の点を
# 「同程度に最接近」とみなし、その中で予定時刻に最も近い点を採用する（前縁の早取り防止）。
PLATEAU_EPS_M = 25.0

client = bigquery.Client(project=PROJECT)


def haversine(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def tod_sec(ts):
    dt = datetime.fromtimestamp(ts, JST)
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def fetch_points():
    sql = f"""
      SELECT event_id, ts_unix, CAST(jst_date AS STRING) jst_date,
             lat, lon, run_id, gt_trip_id, tm_delay_sec
      FROM `{PROJECT}.bus_gps_data.gps_eval`
      WHERE gt_in_service AND gt_confident AND run_id IS NOT NULL
      ORDER BY run_id, ts_unix
    """
    rows = list(client.query(sql).result())
    runs = defaultdict(list)
    for r in rows:
        runs[r["run_id"]].append({
            "ts": int(r["ts_unix"]), "date": r["jst_date"],
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "trip_id": r["gt_trip_id"],
            "tm_delay": None if r["tm_delay_sec"] is None else float(r["tm_delay_sec"]),
        })
    return runs


def main():
    gtfs = G.load_gtfs()
    pc = G.load_precomputed()
    meta = G.build_trip_meta(gtfs, pc)
    tp = pc["trip_progress"]
    stops = gtfs["stops"]

    runs = fetch_points()
    print(f"runs to process: {len(runs)}")

    events = []
    for run_id, pts in runs.items():
        if not pts:
            continue
        trip_id = pts[0]["trip_id"]
        m = meta.get(trip_id)
        prog = tp.get(trip_id)
        if not m or not prog:
            continue
        lats = [p["lat"] for p in pts]
        lons = [p["lon"] for p in pts]
        tods = [tod_sec(p["ts"]) for p in pts]
        tm_delays = [p["tm_delay"] for p in pts]         # 運用の遅延（点ごと）
        date = pts[0]["date"]
        npts = len(pts)
        n_stops = len(prog)

        # 停留所を運行順に処理する（v3: 全域最小＋時刻表最近傍の谷）。
        #   1) 窓 [sched-WIN_EARLY, sched+WIN_LATE] 内・許容150m以内の点を、距離が許容を
        #      超える区切りで「接近区間（谷）」に分割する。
        #   2) 各谷の代表時刻は、谷内の最接近（全域最小）±PLATEAU_EPS_M のプラトーの中で
        #      予定時刻に最も近い点とする（平坦な最接近の前縁を拾う早取りを防ぐ）。
        #   3) 谷が1つならその代表点。折り返し等で谷が複数あるときは、予定時刻に最も近い谷を採る。
        #   カーソルを採用点へ前進させ、循環の後の通過を前の通過へ誤マッチさせない。
        cursor = 0
        for si_idx, e in enumerate(prog):
            sched = int(e["time_sec"])
            s = stops.get(e["stop_id"], {})
            slat, slon = s.get("lat"), s.get("lon")
            if slat is None:
                continue
            # 窓内の点（距離付き）
            win = []
            k = cursor
            while k < npts and tods[k] <= sched + WIN_LATE_SEC:
                if tods[k] >= sched - WIN_EARLY_SEC:
                    win.append((k, haversine(slat, slon, lats[k], lons[k])))
                k += 1
            # 許容内の点を接近区間（谷）に分割
            approaches, cur = [], []
            for kk, dd in win:
                if dd <= MATCH_TOL_M:
                    cur.append((kk, dd))
                elif cur:
                    approaches.append(cur); cur = []
            if cur:
                approaches.append(cur)
            if not approaches:
                continue  # 許容内に点なし＝未検出
            # 各谷の代表点：全域最小±PLATEAU_EPS_M のプラトーの中で予定時刻に最も近い点
            def _rep(app):
                dmin = min(d for _, d in app)
                near = [kk for kk, d in app if d <= dmin + PLATEAU_EPS_M]
                return min(near, key=lambda kk: abs(tods[kk] - sched))
            reps = [_rep(app) for app in approaches]
            # 谷が1つ→その点／複数（折り返し等）→予定時刻に最も近い谷
            best_k = reps[0] if len(reps) == 1 else min(reps, key=lambda kk: abs(tods[kk] - sched))
            best_d = haversine(slat, slon, lats[best_k], lons[best_k])
            cursor = best_k  # 次stopはここ以降を探索
            actual = int(tods[best_k])
            events.append({
                "date": date, "run_id": run_id, "trip_id": trip_id,
                "route_id": m["route_id"], "stop_id": e["stop_id"],
                "stop_seq": int(e["stop_sequence"]), "stop_name": s.get("name", "?"),
                "stop_lat": slat, "stop_lon": slon,
                "sched_sec": sched, "actual_sec": actual,
                "delay_sec": actual - sched,
                "op_delay_sec": tm_delays[best_k],   # 停留所通過時点の運用の遅延
                "is_origin": si_idx == 0,
                "is_terminal": si_idx == n_stops - 1,
                "is_station": "駅" in s.get("name", ""),
                "gap_dist_m": round(float(best_d), 1),
            })

    print(f"stop events: {len(events)}")

    import pandas as pd
    df = pd.DataFrame(events)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    table_id = f"{PROJECT}.bus_gps_data.stop_events"
    job = client.load_table_from_dataframe(
        df, table_id,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
    job.result()
    print(f"uploaded -> {table_id}  ({len(df)} rows)")


if __name__ == "__main__":
    main()
