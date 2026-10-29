"""
WIN_LATE_SEC を 2700→720 に狭めた副作用の検証（DBは書き換えない）。

各 run について「窓2700」と「窓720」の両方で停留所到着を検出し、
2700では検出できたが720では失ったイベントを列挙。それぞれが
  (i) 復路の同名停留所の点を誤って拾っていた誤マッチ（=消えて正しい）か
  (ii) 本物の遅延（=取りこぼしで副作用）か
を、同一 actual_sec が同 run の別 stop_seq でも使われているか等で判定する。
"""
import os, sys, math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as G
from google.cloud import bigquery

PROJECT = "mizuho-basuroke-f370e"
JST = timezone(timedelta(hours=9))
MATCH_TOL_M = 150.0
WIN_EARLY = 600
client = bigquery.Client(project=PROJECT)


def hav(a, b, c, d):
    p = math.pi/180
    return 2*6_371_000*math.asin(math.sqrt(
        math.sin((c-a)*p/2)**2 + math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2))


def tod(ts):
    x = datetime.fromtimestamp(ts, JST)
    return x.hour*3600 + x.minute*60 + x.second


def fetch():
    sql = f"""SELECT ts_unix, run_id, gt_trip_id, lat, lon
              FROM `{PROJECT}.bus_gps_data.gps_eval`
              WHERE gt_in_service AND gt_confident AND run_id IS NOT NULL
              ORDER BY run_id, ts_unix"""
    runs = defaultdict(list)
    for r in client.query(sql).result():
        runs[r["run_id"]].append((int(r["ts_unix"]), r["gt_trip_id"],
                                  float(r["lat"]), float(r["lon"])))
    return runs


def detect(pts, prog, stops, win_late):
    """stop_seq -> (actual_sec, delay_sec, gap_m)。stop_events.py と同一ロジック。"""
    tods = [tod(t) for t, _, _, _ in pts]
    lat = [la for _, _, la, _ in pts]
    lon = [lo for _, _, _, lo in pts]
    out = {}
    cursor = 0
    for si_idx, e in enumerate(prog):
        s = stops.get(e["stop_id"], {})
        slat, slon = s.get("lat"), s.get("lon")
        if slat is None:
            continue
        sched = int(e["time_sec"])
        bk, bd = -1, float("inf")
        k = cursor
        while k < len(pts) and tods[k] <= sched + win_late:
            if tods[k] >= sched - WIN_EARLY:
                dd = hav(slat, slon, lat[k], lon[k])
                if dd < bd:
                    bd, bk = dd, k
                elif bd <= MATCH_TOL_M and dd > bd*1.5:
                    break
            k += 1
        if bk < 0 or bd > MATCH_TOL_M:
            continue
        cursor = bk
        out[int(e["stop_sequence"])] = (int(tods[bk]), int(tods[bk]) - sched, bd, si_idx)
    return out


def main():
    g = G.load_gtfs(); pc = G.load_precomputed()
    tp = pc["trip_progress"]; stops = g["stops"]
    runs = fetch()

    lost = []          # 2700で検出・720で欠落したイベント
    for run_id, pts in runs.items():
        if not pts:
            continue
        trip_id = pts[0][1]
        prog = tp.get(trip_id)
        if not prog:
            continue
        wide = detect(pts, prog, stops, 2700)
        narrow = detect(pts, prog, stops, 720)
        # narrow の actual 集合（復路一致判定用）
        for seq, (act, delay, gap, si_idx) in wide.items():
            if seq in narrow:
                continue
            # このactが wide の他 seq でも使われている＝復路等の重複マッチ
            dup = sum(1 for s2, v in wide.items() if s2 != seq and v[0] == act)
            lost.append((run_id, seq, delay, gap, dup))

    print("=== 窓2700→720 で失ったイベント ===")
    print("失ったイベント総数 =", len(lost))
    if lost:
        dl = np.array([x[2] for x in lost], float)
        dup_cnt = sum(1 for x in lost if x[4] > 0)
        print("  うち actual_sec が同runの別停留所と重複(=復路等の誤マッチ) : %d (%.0f%%)"
              % (dup_cnt, 100*dup_cnt/len(lost)))
        print("  失ったイベントの遅延分布: median=%.1fmin p90=%.1fmin max=%.1fmin"
              % (np.median(dl)/60, np.percentile(dl, 90)/60, dl.max()/60))
        # 重複でない=本物候補 の内訳
        genuine = [x for x in lost if x[4] == 0]
        print("  重複なし(本物の遅延の可能性) : %d 件" % len(genuine))
        for run_id, seq, delay, gap, dup in sorted(genuine, key=lambda z: -z[2])[:12]:
            print("    %-16s seq=%d delay=%.1fmin gap=%.0fm" % (run_id, seq, delay/60, gap))


if __name__ == "__main__":
    main()
