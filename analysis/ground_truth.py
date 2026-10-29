"""
正解便推定（ground truth trip inference）— v2: 点単位スケジュール軌道マッチング

目的:
  実証運用中にリアルタイムで行っていた「便推定（trip lock）」の精度を、
  走り終わった全走行データ（後知恵）から作る「正解の便推定」と照合して検証する。

設計上の重要方針:
  - 正解生成は backend/main.py の便固定ロジック（状態機械・候補スコアリング・
    ジオフェンス出発窓・ロック判定）を一切流用しない（循環論法回避）。
  - 使うのは幾何（点→ポリライン投影）と GTFS 時刻表（trip_progress）だけ。

アルゴリズム（点単位・後知恵）:
  各 accepted GPS 点について:
    1. 全 shape ポリラインへ投影し、横ずれ（offset）と路線上距離（dist_along）を得る。
    2. offset が小さい（= その経路上にいる）shape を通る「当日運行便」を候補とする。
    3. 各候補便について、trip_progress から「その距離の予定通過時刻」を補間し、
       含意遅延 = 実時刻 - 予定時刻 を求める。
    4. 含意遅延が妥当な帯（早発〜大遅延の許容内）にある便のうち、
       (横ずれ, |含意遅延|) が最小の便を、その点の暫定正解便とする。
    5. どの便にも乗らない点（横ずれ大／遅延帯外）は「非運行（回送・待機）」。
  後処理:
    - 時系列で暫定割当を多数決スムージング（点のちらつき除去）。
    - 連続した同一便の区間を run としてまとめ、report 用に集計。

  背中合わせのループ便は「含意遅延の妥当性」で自然に弁別される
  （正しい便は遅延ほぼ0、別便として見ると数十分の早発/延着になり棄却される）。
"""

import os
import csv
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np

JST = timezone(timedelta(hours=9))
GTFS_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "gtfs")

# --- チューニングパラメータ（報告書に明記する） ---
ONROUTE_MAX_OFFSET_M = 55.0     # この横ずれ以内なら「その経路上にいる」とみなす
DELAY_MIN_SEC = -300.0          # 含意遅延の下限（5分早発まで許容）
DELAY_MAX_SEC = 2700.0          # 含意遅延の上限（45分延着まで許容）
RUN_MIN_POINTS = 20             # run（1走行）とみなす最小点数（約3分以上）
CONF_DELAY_MAX_SEC = 900.0      # 遅延中央値がこれを超える run は低信頼（精度分母から除外）
# 便の切り替わりは始発/終点でしか起きない、というドメイン制約で区間を切る
TERMINAL_ZONE_R = 90.0          # 始発/終点停留所とみなす半径
DWELL_MIN_SEC = 60.0            # 終点付近でこの秒数以上留まったら区間境界（滞留）
SEG_GAP_SEC = 300.0            # この時間以上のGPS欠損も区間境界

EARTH_RADIUS_M = 6_371_000


# ============================================================
# GTFS / precomputed ローダ
# ============================================================
def _load_csv(name):
    with open(os.path.join(GTFS_DIR, name), "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _parse_hhmmss(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def load_gtfs():
    stops = {}
    for r in _load_csv("stops.txt"):
        stops[r["stop_id"]] = {"name": r["stop_name"],
                               "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
    trips = {}
    for r in _load_csv("trips.txt"):
        trips[r["trip_id"]] = {"route_id": r["route_id"], "service_id": r["service_id"],
                               "shape_id": r["shape_id"], "direction_id": r.get("direction_id", ""),
                               "headsign": r.get("trip_headsign", "")}
    calendar = {r["service_id"]: r for r in _load_csv("calendar.txt")}
    calendar_dates = {}
    for r in _load_csv("calendar_dates.txt"):
        calendar_dates[(r["service_id"], r["date"])] = int(r["exception_type"])
    return {"stops": stops, "trips": trips, "calendar": calendar, "calendar_dates": calendar_dates}


def load_precomputed():
    with open(os.path.join(GTFS_DIR, "precomputed.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def haversine(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ============================================================
# カレンダー
# ============================================================
_WD = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def service_ids_for_date(gtfs, ymd):
    dt = datetime.strptime(ymd, "%Y%m%d")
    wd = _WD[dt.weekday()]
    active = set()
    for sid, row in gtfs["calendar"].items():
        if row.get(wd) == "1" and row["start_date"] <= ymd <= row["end_date"]:
            active.add(sid)
    for (sid, date), ex in gtfs["calendar_dates"].items():
        if date == ymd:
            if ex == 1:
                active.add(sid)
            elif ex == 2:
                active.discard(sid)
    return active


# ============================================================
# shape の numpy 化 + 高速投影
# ============================================================
class ShapeIndex:
    """1 shape のポリラインを numpy 化し、点群を一括投影する。"""
    def __init__(self, pts):
        lat0 = pts[0]["lat"]
        lon0 = pts[0]["lon"]
        p = math.pi / 180
        self.ref = (lat0, lon0)
        self.kx = p * EARTH_RADIUS_M * math.cos(lat0 * p)
        self.ky = p * EARTH_RADIUS_M
        xs = np.array([(q["lon"] - lon0) * self.kx for q in pts])
        ys = np.array([(q["lat"] - lat0) * self.ky for q in pts])
        self.ax = xs[:-1]; self.ay = ys[:-1]
        self.dx = xs[1:] - xs[:-1]
        self.dy = ys[1:] - ys[:-1]
        self.seg2 = self.dx**2 + self.dy**2
        self.seg2[self.seg2 == 0] = 1e-9
        self.seg_len = np.sqrt(self.dx**2 + self.dy**2)
        base = np.array([q["dist_traveled"] for q in pts[:-1]])
        self.base_dist = base

    def project_many(self, lats, lons):
        """戻り: (offset[N], dist_along[N])  最小横ずれのセグメントを採用。"""
        px = (lons - self.ref[1]) * self.kx        # (N,)
        py = (lats - self.ref[0]) * self.ky
        # (N, S)
        apx = px[:, None] - self.ax[None, :]
        apy = py[:, None] - self.ay[None, :]
        t = (apx * self.dx[None, :] + apy * self.dy[None, :]) / self.seg2[None, :]
        t = np.clip(t, 0.0, 1.0)
        projx = self.ax[None, :] + t * self.dx[None, :]
        projy = self.ay[None, :] + t * self.dy[None, :]
        off = np.hypot(px[:, None] - projx, py[:, None] - projy)   # (N,S)
        seg_idx = np.argmin(off, axis=1)                            # (N,)
        n = len(lats)
        rows = np.arange(n)
        best_off = off[rows, seg_idx]
        best_t = t[rows, seg_idx]
        dist_along = self.base_dist[seg_idx] + best_t * self.seg_len[seg_idx]
        return best_off, dist_along

    def project_sequential(self, lats, lons, lookback=2):
        """時系列順の点列を、セグメントを非後退で進めながら投影する。
        自己重複する経路（往復同一道路など）で、往路・復路の脚を距離で正しく
        振り分けるために使う。戻り: (offset[N], dist_along[N])。"""
        n = len(lats)
        px = (lons - self.ref[1]) * self.kx
        py = (lats - self.ref[0]) * self.ky
        nseg = len(self.ax)
        offs = np.empty(n)
        dists = np.empty(n)
        last = 0
        for i in range(n):
            lo = max(0, last - lookback)
            ax = self.ax[lo:]; ay = self.ay[lo:]
            dx = self.dx[lo:]; dy = self.dy[lo:]
            seg2 = self.seg2[lo:]; seglen = self.seg_len[lo:]
            base = self.base_dist[lo:]
            apx = px[i] - ax; apy = py[i] - ay
            t = np.clip((apx * dx + apy * dy) / seg2, 0.0, 1.0)
            projx = ax + t * dx; projy = ay + t * dy
            off = np.hypot(px[i] - projx, py[i] - projy)
            j = int(np.argmin(off))
            last = lo + j
            offs[i] = off[j]
            dists[i] = base[j] + t[j] * seglen[j]
        return offs, dists


# ============================================================
# 便メタ + スケジュール補間
# ============================================================
def build_trip_meta(gtfs, precomputed):
    meta = {}
    shapes = precomputed["shapes"]
    tp = precomputed["trip_progress"]
    start_abs = precomputed.get("trip_shape_start_abs", {})
    for tid, t in gtfs["trips"].items():
        if t["shape_id"] not in shapes or tid not in tp:
            continue
        prog = tp[tid]
        dists = np.array([e["distance_m"] for e in prog], dtype=float)
        times = np.array([e["time_sec"] for e in prog], dtype=float)
        meta[tid] = {
            "route_id": t["route_id"], "service_id": t["service_id"],
            "shape_id": t["shape_id"], "direction_id": t["direction_id"],
            "start_abs": float(start_abs.get(tid, 0.0)),
            "prog_dist": dists, "prog_time": times,
            "origin_stop_id": prog[0]["stop_id"],
            "terminal_stop_id": prog[-1]["stop_id"],
            "origin_dep_sec": float(times[0]),
            "terminal_arr_sec": float(times[-1]),
            "route_len": float(dists[-1]),
        }
    return meta


def terminal_stops_with_coords(meta, gtfs):
    """始発/終点の stop_id 集合を座標付きで返す。"""
    ids = set()
    for m in meta.values():
        ids.add(m["origin_stop_id"])
        ids.add(m["terminal_stop_id"])
    out = []
    for sid in ids:
        s = gtfs["stops"].get(sid)
        if s:
            out.append((sid, s["lat"], s["lon"]))
    return out


def sched_time_at(meta, trip_distance):
    """便メタの trip_progress を線形補間して予定通過時刻(sec)を返す。範囲外は端で外挿。"""
    d = meta["prog_dist"]; t = meta["prog_time"]
    if trip_distance <= d[0]:
        return t[0]
    if trip_distance >= d[-1]:
        return t[-1]
    return float(np.interp(trip_distance, d, t))


# ============================================================
# メイン: 正解生成
# ============================================================
def infer_ground_truth(points_by_day, gtfs, precomputed, diag_days=None):
    meta = build_trip_meta(gtfs, precomputed)
    shapes = precomputed["shapes"]
    shape_index = {sid: ShapeIndex(pts) for sid, pts in shapes.items()}
    # shape_id -> [trip_ids...]
    trips_on_shape = {}
    for tid, m in meta.items():
        trips_on_shape.setdefault(m["shape_id"], []).append(tid)
    terminal_pts = terminal_stops_with_coords(meta, gtfs)
    # 全停留所を numpy 化（最寄り停留所の高速算出用）
    stop_ids = list(gtfs["stops"].keys())
    stop_lat = np.array([gtfs["stops"][s]["lat"] for s in stop_ids])
    stop_lon = np.array([gtfs["stops"][s]["lon"] for s in stop_ids])

    gt = {}
    runs_summary = []
    run_counter = 0
    diag_days = diag_days or set()
    day_diag = {}

    for day in sorted(points_by_day.keys()):
        day_pts_all = points_by_day[day]
        for p in day_pts_all:
            gt.setdefault(p["event_id"], {"gt_trip_id": None, "gt_route_id": None,
                                          "gt_in_service": False, "run_id": None,
                                          "gt_delay_sec": None, "gt_confident": False,
                                          "gt_nearest_stop_id": None})
        acc = [p for p in day_pts_all if p.get("accepted")]
        acc.sort(key=lambda x: x["ts"])
        if not acc:
            continue

        ymd = day.replace("-", "")
        day_services = service_ids_for_date(gtfs, ymd)
        day_shapes = sorted({m["shape_id"] for m in meta.values() if m["service_id"] in day_services})

        lats = np.array([p["lat"] for p in acc])
        lons = np.array([p["lon"] for p in acc])
        # 当日の各点の time-of-day 秒
        tod = np.array([_tod_sec(p["ts"]) for p in acc])

        # 各点の最寄り停留所（近似平面距離で十分）
        dlat = (lats[:, None] - stop_lat[None, :]) * 111000.0
        dlon = (lons[:, None] - stop_lon[None, :]) * 111000.0 * math.cos(math.radians(35.77))
        nearest_idx = np.argmin(dlat**2 + dlon**2, axis=1)

        # 各 shape へ一括投影
        proj = {sid: shape_index[sid].project_many(lats, lons) for sid in day_shapes}

        assign = [None] * len(acc)      # 暫定 trip_id
        assign_delay = [None] * len(acc)
        for i in range(len(acc)):
            best = None  # (offset, abs_delay, trip_id, delay)
            for sid in day_shapes:
                off = proj[sid][0][i]
                if off > ONROUTE_MAX_OFFSET_M:
                    continue
                dist_along = proj[sid][1][i]
                for tid in trips_on_shape.get(sid, []):
                    m = meta[tid]
                    if m["service_id"] not in day_services:
                        continue
                    trip_dist = dist_along - m["start_abs"]
                    if trip_dist < -30 or trip_dist > m["route_len"] + 30:
                        continue
                    sched = sched_time_at(m, trip_dist)
                    delay = tod[i] - sched
                    if delay < DELAY_MIN_SEC or delay > DELAY_MAX_SEC:
                        continue
                    key = (off, abs(delay))
                    if best is None or key < (best[0], best[1]):
                        best = (off, abs(delay), tid, delay)
            if best is not None:
                assign[i] = best[2]
                assign_delay[i] = best[3]

        # --- 終点アンカー区間分割 + 区間内多数決 ---
        # 「便の切り替わりは始発/終点でしか起きない」制約で走行区間を切り、
        # 各区間の暫定割当を多数決して1便に固定する（滞留・重複区間のちらつき除去）。
        smoothed = _segment_and_vote(acc, assign, terminal_pts)

        # --- run 化して gt 付与 ---
        i = 0
        n = len(acc)
        while i < n:
            tid = smoothed[i]
            j = i
            while j < n and smoothed[j] == tid:
                j += 1
            if tid is not None and (j - i) >= RUN_MIN_POINTS:
                run_counter += 1
                rid = f"{day}#{run_counter}"
                route_id = meta[tid]["route_id"]
                delays = [assign_delay[k] for k in range(i, j) if assign_delay[k] is not None]
                med_delay = float(np.median(delays)) if delays else None
                # 低信頼判定: 遅延中央値が過大な run はスケジュールマッチの人工物の可能性が高い
                confident = med_delay is not None and abs(med_delay) <= CONF_DELAY_MAX_SEC
                for k in range(i, j):
                    ev = acc[k]["event_id"]
                    gt[ev] = {"gt_trip_id": tid, "gt_route_id": route_id,
                              "gt_in_service": True, "run_id": rid,
                              "gt_delay_sec": assign_delay[k],
                              "gt_confident": confident,
                              "gt_nearest_stop_id": stop_ids[nearest_idx[k]]}
                runs_summary.append({
                    "run_id": rid, "date": day, "trip_id": tid, "route_id": route_id,
                    "n_points": j - i,
                    "start_ts": acc[i]["ts"], "end_ts": acc[j - 1]["ts"],
                    "median_delay_sec": round(med_delay, 1) if med_delay is not None else None,
                    "confident": confident,
                })
            i = j

        if day in diag_days:
            day_diag[day] = {"n_acc": n, "n_assigned": sum(1 for a in smoothed if a),
                             "day_shapes": day_shapes}

    return gt, runs_summary, day_diag


def _tod_sec(ts):
    dt = datetime.fromtimestamp(ts, JST)
    return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6


def _near_terminal(lat, lon, terminal_pts):
    for _sid, tlat, tlon in terminal_pts:
        if haversine(lat, lon, tlat, tlon) <= TERMINAL_ZONE_R:
            return True
    return False


def _segment_and_vote(acc, assign, terminal_pts):
    """
    走行区間を「始発/終点での滞留」「大きな時間ギャップ」で切り、
    各区間内の暫定割当(assign)を多数決して1便に固定する。
    滞留ブロック内の点は非運行(None)。
    """
    n = len(acc)
    out = [None] * n

    # 各点が終点ゾーン内か
    inzone = [_near_terminal(p["lat"], p["lon"], terminal_pts) for p in acc]

    # 滞留ブロック検出: 終点ゾーン内が連続し時間幅>=DWELL_MIN_SEC
    is_dwell = [False] * n
    i = 0
    while i < n:
        if inzone[i]:
            j = i
            while j < n and inzone[j]:
                j += 1
            if acc[j - 1]["ts"] - acc[i]["ts"] >= DWELL_MIN_SEC:
                for k in range(i, j):
                    is_dwell[k] = True
            i = j
        else:
            i += 1

    # 区間 = 滞留でなく、直前との時間ギャップが小さい連続点
    seg_start = None
    for i in range(n):
        boundary = is_dwell[i] or (seg_start is not None and
                                   acc[i]["ts"] - acc[i - 1]["ts"] > SEG_GAP_SEC)
        if is_dwell[i]:
            if seg_start is not None:
                _vote_fill(acc, assign, out, seg_start, i)
                seg_start = None
            continue
        if boundary and seg_start is not None:
            _vote_fill(acc, assign, out, seg_start, i)
            seg_start = i
        elif seg_start is None:
            seg_start = i
    if seg_start is not None:
        _vote_fill(acc, assign, out, seg_start, n)
    return out


def _vote_fill(acc, assign, out, lo, hi):
    """[lo,hi) 区間の多数決 trip を out に埋める。None 多数なら埋めない。"""
    cnt = {}
    for k in range(lo, hi):
        v = assign[k]
        if v is not None:
            cnt[v] = cnt.get(v, 0) + 1
    if not cnt:
        return
    winner = max(cnt, key=cnt.get)
    # 区間の過半が on-route でなければ非運行扱い
    if cnt[winner] < 0.4 * (hi - lo):
        return
    for k in range(lo, hi):
        out[k] = winner


if __name__ == "__main__":
    g = load_gtfs(); pc = load_precomputed()
    m = build_trip_meta(g, pc)
    print("trips:", len(m), "shapes:", len({x['shape_id'] for x in m.values()}))
