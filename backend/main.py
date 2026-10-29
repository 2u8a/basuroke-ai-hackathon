"""
Module B – GPS受信 / 5状態ステートマシン / 便固定 / 遅延追跡 / GTFS-RT生成 / Firestore保存

瑞穂町コミュニティバス バスロケーションシステム PoC （便固定モデル v3）

設計方針:
  - 毎GPS再推定ではなく、起動→車庫→始発→候補絞込→便固定→遅延追跡 の状態機械
  - 一度便を固定したら原則として便を変更しない
  - 平滑化は TRIP_LOCKED 中のみ
  - GTFS-RT は latest の確定状態だけを反映（再推定フォールバック廃止）

状態遷移:
  IDLE
    ↓ 最初の accepted GPS
  PRE_SERVICE_MOVE          車庫→駅などの回送中。便候補は作らない
    ↓ 始発停留所ジオフェンス内に入る
  ORIGIN_WAITING            始発で待機。出発時刻窓に入るまで便は確定させない
    ↓ 出発時刻窓に入る + 候補便≥1
  CANDIDATE_SEARCH          数回のGPSで候補スコアを累積し、便を確定する
    ↓ 確定条件成立 / 強制確定
  TRIP_LOCKED               便固定。以後は遅延だけ追跡
    ↓ 終点到着判定（即時） / 終点+15分超過 / 15分以上欠損
  IDLE
"""

# ============================================================
# imports
# ============================================================
import os
import csv
import json
import math
import time
import hashlib
import base64
import logging
import threading
from datetime import datetime, timedelta, timezone

from firebase_functions import https_fn
import firebase_admin
from firebase_admin import firestore as _firestore_mod
from google.transit import gtfs_realtime_pb2

# ============================================================
# constants
# ============================================================
JST = timezone(timedelta(hours=9))
VEHICLE_ID = "mizuho-bus-01"
GTFS_DIR = os.path.join(os.path.dirname(__file__), "data", "gtfs")

ALGORITHM_VERSION = "trip-lock-state-machine-v3.3-end-arrival"

# --- geo / area ---
EARTH_RADIUS_M = 6_371_000
LAT_MIN, LAT_MAX = 35.74, 35.80
LON_MIN, LON_MAX = 139.32, 139.38

# --- ジオフェンス（始発停留所判定） ---
ORIGIN_GEOFENCE_RADIUS_M = 120         # 駅前ロータリー余裕を見て120m
ORIGIN_LEAVE_HYSTERESIS_M = 60         # ジオフェンスから抜けたとみなす追加距離

# --- 出発時刻窓（始発の予定発車時刻に対して） ---
DEPARTURE_WINDOW_OPEN_SEC = 15 * 60    # 出発15分前から候補探索開始可
DEPARTURE_WINDOW_CLOSE_SEC = 10 * 60   # 出発10分後までは候補探索継続可

# --- 候補絞込・確定条件 ---
CANDIDATE_MIN_GPS_COUNT = 5            # 通常確定の最小GPS受信回数
CANDIDATE_FORCE_GPS_COUNT = 15         # 強制確定の上限
CANDIDATE_LOCK_GAP_M = 300.0           # 1位と2位の累積誤差差（m）
CANDIDATE_MAX_AVG_ERROR_M = 400.0      # 1位の平均距離誤差上限
MAX_SHAPE_OFFSET_M = 250.0             # shape横ずれ上限（候補棄却用）

# --- TRIP_LOCKED 中の追跡 ---
SMOOTH_ALPHA = 0.6                     # 直近GPSへの寄せ係数（0.6:0.4 = current:prev）
LOCKED_REVERSE_REJECT_M = 200.0        # ロック後の逆行棄却（保存はする/採用しない）
LOCKED_JUMP_REJECT_M = 1500.0          # ジャンプ棄却

# --- シーケンシャルトラッキング（往復重複ルートの誤スナップ対策） ---
SEARCH_LOOKBEHIND_M = 100.0            # 通過済み停留所からの戻り許容
SEARCH_LOOKAHEAD_STOPS = 2             # 何個先の停留所までを探索範囲にするか
SEARCH_LOOKAHEAD_BUFFER_M = 500.0      # 先頭側の遊び（停留所間が長い区間の救済）
STOP_PASS_THRESHOLD_M = 30.0           # 次停留所の距離をこの幅で超えたら「通過」とみなす

# --- 始発保留（早着配信防止） ---
ORIGIN_HOLD_RELEASE_DISTANCE_M = 50.0  # 始発から何m離れたら「実際に出発」とみなすか

# --- IDLE 復帰条件 ---
LOCKED_END_GRACE_SEC = 15 * 60         # 終着時刻+15分でIDLE復帰
# 終点到着とみなして TRIP_LOCKED から即 IDLE に戻す（shape距離・シーケンス）
END_STOP_DISTANCE_THRESHOLD_M = 100.0  # 終点の累積距離との差がこれ以下なら到着圏内
END_ARRIVAL_EARLY_WINDOW_SEC = 5 * 60  # 終着予定のこの秒数「前」から、到着即終了の時刻条件を満たす
NO_GPS_IDLE_SEC = 15 * 60              # 15分欠損でIDLE復帰
PRE_SERVICE_MAX_SEC = 4 * 3600         # 4時間以上 PRE_SERVICE_MOVE 継続したらリセット
ORIGIN_WAITING_MAX_SEC = 3 * 3600      # 3時間以上 ORIGIN_WAITING 継続したらリセット

# --- nearest stop ---
STOP_NEAR_GEO_THRESHOLD_M = 70

# --- 永続化 ---
EVENT_RETENTION_DAYS = 400
CANDIDATE_STORE_LIMIT = 8
ENABLE_GTFS_RT_AUDIT = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("busroq")

# ============================================================
# lazy init state
# ============================================================
_init_done = False
_init_lock = threading.Lock()

db = None
API_KEY = None

STOPS = {}
ROUTES = {}
TRIPS = {}
TRIP_STOP_TIMES = {}
CALENDAR = {}
CALENDAR_DATES = {}

SHAPES = {}
SHAPE_TOTAL_DISTANCE = {}
TRIP_PROGRESS = {}
STOP_TO_SHAPE_DIST = {}
TRIP_SHAPE_START_ABS = {}
TRIP_ROUTE_LENGTH = {}

# 始発（origin）まわり
TRIP_ORIGIN_STOP_ID = {}    # trip_id → 最初の stop_id
TRIP_ORIGIN_DEP_SEC = {}    # trip_id → 最初の departure_sec
ORIGIN_STOPS = {}           # stop_id → {lat, lon, name}  (origin 集合)

PRECOMPUTED_VERSION = None


# ============================================================
# init helpers
# ============================================================
def _load_csv(filename):
    path = os.path.join(GTFS_DIR, filename)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _parse_gtfs_time(hhmmss):
    if not hhmmss:
        return 0
    hh, mm, ss = hhmmss.strip().split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def _now_jst():
    return datetime.now(JST)


def _seconds_since_midnight(dt):
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _do_init():
    global _init_done, db, API_KEY
    global STOPS, ROUTES, TRIPS, TRIP_STOP_TIMES, CALENDAR, CALENDAR_DATES
    global SHAPES, SHAPE_TOTAL_DISTANCE, TRIP_PROGRESS
    global STOP_TO_SHAPE_DIST, TRIP_SHAPE_START_ABS, TRIP_ROUTE_LENGTH
    global TRIP_ORIGIN_STOP_ID, TRIP_ORIGIN_DEP_SEC, ORIGIN_STOPS
    global PRECOMPUTED_VERSION

    if _init_done:
        return

    firebase_admin.initialize_app()
    db = _firestore_mod.client()
    API_KEY = os.environ.get("API_KEY")

    # --- GTFS CSV ---
    STOPS.update({
        r["stop_id"]: {
            "name": r.get("stop_name", r["stop_id"]),
            "lat": float(r["stop_lat"]),
            "lon": float(r["stop_lon"]),
        }
        for r in _load_csv("stops.txt")
    })

    ROUTES.update({r["route_id"]: r for r in _load_csv("routes.txt")})
    TRIPS.update({r["trip_id"]: r for r in _load_csv("trips.txt")})

    for r in _load_csv("stop_times.txt"):
        tid = r["trip_id"]
        TRIP_STOP_TIMES.setdefault(tid, []).append({
            "stop_id": r["stop_id"],
            "arrival_sec": _parse_gtfs_time(r["arrival_time"]),
            "departure_sec": _parse_gtfs_time(r["departure_time"]),
            "stop_sequence": int(r["stop_sequence"]),
        })
    for tid in TRIP_STOP_TIMES:
        TRIP_STOP_TIMES[tid].sort(key=lambda x: x["stop_sequence"])

    CALENDAR.update({r["service_id"]: r for r in _load_csv("calendar.txt")})
    for r in _load_csv("calendar_dates.txt"):
        CALENDAR_DATES[(r["service_id"], r["date"])] = int(r["exception_type"])

    # --- precomputed.json ---
    pc_path = os.path.join(GTFS_DIR, "precomputed.json")
    PRECOMPUTED_VERSION = _file_sha1(pc_path)
    with open(pc_path, "r", encoding="utf-8") as f:
        pc = json.load(f)

    SHAPES.update({
        sid: [
            {
                "lat": float(p["lat"]),
                "lon": float(p["lon"]),
                "dist_traveled": float(p["dist_traveled"]),
            }
            for p in pts
        ]
        for sid, pts in pc["shapes"].items()
    })
    SHAPE_TOTAL_DISTANCE.update({k: float(v) for k, v in pc["shape_total_distance"].items()})
    TRIP_PROGRESS.update(pc["trip_progress"])

    for key, val in pc["stop_to_shape_dist"].items():
        trip_id, seq_str = key.split("|")
        STOP_TO_SHAPE_DIST[(trip_id, int(seq_str))] = float(val)

    TRIP_SHAPE_START_ABS.update({k: float(v) for k, v in pc["trip_shape_start_abs"].items()})
    TRIP_ROUTE_LENGTH.update({k: float(v) for k, v in pc["trip_route_length"].items()})

    # --- origin マッピング作成 ---
    origin_stop_ids = set()
    for trip_id, stops in TRIP_STOP_TIMES.items():
        if not stops:
            continue
        first = stops[0]
        TRIP_ORIGIN_STOP_ID[trip_id] = first["stop_id"]
        TRIP_ORIGIN_DEP_SEC[trip_id] = int(first["departure_sec"])
        origin_stop_ids.add(first["stop_id"])

    for sid in origin_stop_ids:
        s = STOPS.get(sid)
        if s:
            ORIGIN_STOPS[sid] = {"lat": s["lat"], "lon": s["lon"], "name": s["name"]}

    _init_done = True
    logger.info(
        "Init complete: stops=%d routes=%d trips=%d shapes=%d origin_stops=%d precomputed=%s",
        len(STOPS), len(ROUTES), len(TRIPS), len(SHAPES), len(ORIGIN_STOPS),
        PRECOMPUTED_VERSION[:10],
    )


def _ensure_init():
    global _init_done
    if _init_done:
        return
    with _init_lock:
        if not _init_done:
            _do_init()


# ============================================================
# generic utils
# ============================================================
def _route_name(route_id):
    r = ROUTES.get(route_id, {})
    return r.get("route_long_name") or r.get("route_short_name") or route_id


def _parse_payload_timestamp(data):
    ts = data.get("timestamp")
    if not ts:
        return _now_jst()
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return _now_jst()


def _cors_headers(content_type):
    return {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-KEY",
    }


def _expire_at(days=EVENT_RETENTION_DAYS):
    return _now_jst() + timedelta(days=days)


def _make_event_id(vehicle_id, ts_unix, lat, lon):
    raw = f"{vehicle_id}|{ts_unix}|{lat}|{lon}|{time.time_ns()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _safe_firestore_value(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 3)
    if isinstance(v, dict):
        return {str(k): _safe_firestore_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_safe_firestore_value(x) for x in v]
    return v


# ============================================================
# geometry
# ============================================================
def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _latlon_to_xy(lat, lon, ref_lat, ref_lon):
    x = math.radians(lon - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return x, y


def _project_point_to_segment(lat, lon, a, b):
    ref_lat = (a["lat"] + b["lat"]) / 2.0
    ref_lon = (a["lon"] + b["lon"]) / 2.0
    px, py = _latlon_to_xy(lat, lon, ref_lat, ref_lon)
    ax, ay = _latlon_to_xy(a["lat"], a["lon"], ref_lat, ref_lon)
    bx, by = _latlon_to_xy(b["lat"], b["lon"], ref_lat, ref_lon)

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))

    qx, qy = ax + t * dx, ay + t * dy
    return {
        "distance_m": a["dist_traveled"] + t * (b["dist_traveled"] - a["dist_traveled"]),
        "offset_m": math.hypot(px - qx, py - qy),
    }


def project_to_shape(shape_id, lat, lon, dist_min=None, dist_max=None):
    """
    GPS点を shape の最近接点に投影する。

    dist_min / dist_max を与えると、shape上の累積距離がその範囲に重なるセグメントだけを
    探索対象にする。往復重複shapeで復路走行中に往路セグメントへ誤スナップされるのを防ぐための重要なガード。
    """
    pts = SHAPES.get(shape_id)
    if not pts or len(pts) < 2:
        return None

    best = None
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if dist_min is not None or dist_max is not None:
            seg_lo = min(a["dist_traveled"], b["dist_traveled"])
            seg_hi = max(a["dist_traveled"], b["dist_traveled"])
            if dist_max is not None and seg_lo > dist_max:
                continue
            if dist_min is not None and seg_hi < dist_min:
                continue

        proj = _project_point_to_segment(lat, lon, a, b)
        cand = {**proj, "shape_id": shape_id, "segment_index": i}
        if best is None or cand["offset_m"] < best["offset_m"] - 1.0:
            best = cand
        elif abs(cand["offset_m"] - best["offset_m"]) <= 1.0:
            if cand["distance_m"] < best["distance_m"]:
                best = cand
    return best


def _stop_distance_on_trip(trip_id, seq):
    """trip上のある stop_sequence に対応する距離を返す。None なら未定義。"""
    return STOP_TO_SHAPE_DIST.get((trip_id, seq))


def _trip_last_sequence(trip_id):
    sts = TRIP_STOP_TIMES.get(trip_id, [])
    if not sts:
        return None
    return sts[-1]["stop_sequence"]


def _trip_first_stop_sequence(trip_id):
    sts = TRIP_STOP_TIMES.get(trip_id, [])
    if not sts:
        return None
    return int(sts[0]["stop_sequence"])


def _trip_index_for_sequence(trip_id, current_seq):
    """
    TRIP_STOP_TIMES[trip_id]（stop_sequence 昇順）上で、current_seq に対応するインデックスを返す。
    完全一致がなければ、stop_sequence <= current_seq となる最右インデックス（通過済みとみなせる停留所）。
    current_seq より手前に該当がなければ 0。
    """
    sts = TRIP_STOP_TIMES.get(trip_id, [])
    if not sts:
        return None
    if current_seq is None:
        return 0
    cur = int(current_seq)
    lo, hi = 0, len(sts) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        s = int(sts[mid]["stop_sequence"])
        if s <= cur:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best < 0:
        return 0
    return best


def resolve_search_window(trip_id, current_seq,
                          look_ahead_stops=SEARCH_LOOKAHEAD_STOPS,
                          look_behind_m=SEARCH_LOOKBEHIND_M,
                          look_ahead_m=SEARCH_LOOKAHEAD_BUFFER_M):
    """
    現在の current_seq を起点に、project_to_shape へ渡す探索範囲 (dist_min, dist_max) を計算する。
    未定義なら (None, None) を返し、全体探索にフォールバックさせる。
    次停留所・先読みは TRIP_STOP_TIMES の配列インデックスで進め、GTFS の stop_sequence の欠番に依存しない。
    """
    if not trip_id or current_seq is None:
        return None, None
    sts = TRIP_STOP_TIMES.get(trip_id, [])
    if not sts:
        return None, None

    idx = _trip_index_for_sequence(trip_id, current_seq)
    if idx is None:
        return None, None
    ahead_idx = min(idx + look_ahead_stops, len(sts) - 1)

    seq_at_idx = int(sts[idx]["stop_sequence"])
    ahead_seq = int(sts[ahead_idx]["stop_sequence"])

    cur_d = _stop_distance_on_trip(trip_id, seq_at_idx)
    ahead_d = _stop_distance_on_trip(trip_id, ahead_seq)

    if cur_d is None and ahead_d is None:
        return None, None

    lo = (cur_d if cur_d is not None else ahead_d) - look_behind_m
    hi = (ahead_d if ahead_d is not None else cur_d) + look_ahead_m
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _project_to_trip_distance(trip_id, lat, lon, anchor_distance_m=None,
                              search_window=None):
    """
    tripの shape に GPS を投影し、trip起点からの距離 distance_m を返す。
    search_window=(dist_min, dist_max) を与えると、その範囲だけを探索する。
    """
    shape_id = TRIPS[trip_id]["shape_id"]
    if search_window is None:
        proj = project_to_shape(shape_id, lat, lon)
    else:
        # search_window は trip起点基準。project_to_shape へは shape 絶対距離に換算する。
        base_abs = TRIP_SHAPE_START_ABS.get(trip_id, 0.0)
        lo_rel, hi_rel = search_window
        dist_min = (base_abs + lo_rel) if lo_rel is not None else None
        dist_max = (base_abs + hi_rel) if hi_rel is not None else None
        proj = project_to_shape(shape_id, lat, lon, dist_min=dist_min, dist_max=dist_max)
        if proj is None:
            # 範囲外フォールバック（ジオフェンス上で拾えなかった場合の保険）
            proj = project_to_shape(shape_id, lat, lon)
    if proj is None:
        return None

    abs_d = proj["distance_m"]
    base_abs = TRIP_SHAPE_START_ABS.get(trip_id, 0.0)
    total_shape = SHAPE_TOTAL_DISTANCE.get(shape_id, 0.0)

    raw_rel = abs_d - base_abs
    candidates = [c for c in [raw_rel, raw_rel + total_shape, raw_rel - total_shape] if c >= -100.0]
    if not candidates:
        candidates = [0.0]

    if anchor_distance_m is None:
        distance_m = min((max(0.0, c) for c in candidates), key=abs)
    else:
        distance_m = min((max(0.0, c) for c in candidates), key=lambda x: abs(x - anchor_distance_m))

    return {
        **proj,
        "abs_distance_m": round(abs_d, 1),
        "distance_m": round(distance_m, 1),
    }


# ============================================================
# trip progress interpolation
# ============================================================
def time_to_distance(trip_id, t_sec):
    prog = TRIP_PROGRESS.get(trip_id, [])
    if not prog:
        return 0.0
    if t_sec <= prog[0]["time_sec"]:
        return float(prog[0]["distance_m"])
    if t_sec >= prog[-1]["time_sec"]:
        return float(prog[-1]["distance_m"])
    for i in range(len(prog) - 1):
        p1, p2 = prog[i], prog[i + 1]
        if p1["time_sec"] <= t_sec <= p2["time_sec"]:
            dt = p2["time_sec"] - p1["time_sec"]
            if dt <= 0:
                return float(p1["distance_m"])
            ratio = (t_sec - p1["time_sec"]) / dt
            return float(p1["distance_m"]) + ratio * (float(p2["distance_m"]) - float(p1["distance_m"]))
    return float(prog[-1]["distance_m"])


def distance_to_time(trip_id, d_m):
    prog = TRIP_PROGRESS.get(trip_id, [])
    if not prog:
        return 0
    if d_m <= prog[0]["distance_m"]:
        return int(prog[0]["time_sec"])
    if d_m >= prog[-1]["distance_m"]:
        return int(prog[-1]["time_sec"])
    for i in range(len(prog) - 1):
        p1, p2 = prog[i], prog[i + 1]
        d1 = float(p1["distance_m"])
        d2 = float(p2["distance_m"])
        if d1 <= d_m <= d2:
            dd = d2 - d1
            if dd <= 0:
                return int(p1["time_sec"])
            ratio = (d_m - d1) / dd
            return int(round(p1["time_sec"] + ratio * (p2["time_sec"] - p1["time_sec"])))
    return int(prog[-1]["time_sec"])


# ============================================================
# service calendar
# ============================================================
def resolve_active_service_ids(now_dt):
    date_str = now_dt.strftime("%Y%m%d")
    weekday_idx = now_dt.weekday()
    weekday_key = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][weekday_idx]

    active = set()
    for sid, row in CALENDAR.items():
        start_date = row.get("start_date", "00000000")
        end_date = row.get("end_date", "99999999")
        if not (start_date <= date_str <= end_date):
            continue
        if str(row.get(weekday_key, "0")) == "1":
            active.add(sid)

    for (sid, d), exc_type in CALENDAR_DATES.items():
        if d != date_str:
            continue
        if exc_type == 1:
            active.add(sid)
        elif exc_type == 2:
            active.discard(sid)

    return sorted(active)


# ============================================================
# origin geofence
# ============================================================
def detect_origin_zone(lat, lon):
    """
    現在のGPSが、いずれかの始発停留所ジオフェンスに入っているか判定する。
    入っているなら (stop_id, distance_m) を返す。複数該当時は最も近いものを返す。
    """
    best = None
    for sid, info in ORIGIN_STOPS.items():
        d = _haversine(lat, lon, info["lat"], info["lon"])
        if d <= ORIGIN_GEOFENCE_RADIUS_M:
            if best is None or d < best[1]:
                best = (sid, d)
    return best  # (stop_id, distance_m) or None


def is_outside_origin_zone(lat, lon, origin_stop_id):
    """origin_stop_id のジオフェンスから完全に抜けたか（ヒステリシス込み）"""
    if not origin_stop_id:
        return True
    info = ORIGIN_STOPS.get(origin_stop_id)
    if not info:
        return True
    d = _haversine(lat, lon, info["lat"], info["lon"])
    return d > (ORIGIN_GEOFENCE_RADIUS_M + ORIGIN_LEAVE_HYSTERESIS_M)


# ============================================================
# departure candidate finder
# ============================================================
def find_departure_candidates(origin_stop_id, now_dt, service_ids):
    """
    指定 origin_stop_id を始発とし、当日 service_id に属し、
    出発時刻が now_sec ∈ [-DEPARTURE_WINDOW_OPEN, +DEPARTURE_WINDOW_CLOSE] にある trip を返す。
    """
    if not origin_stop_id:
        return []
    now_sec = _seconds_since_midnight(now_dt)
    rows = []
    for trip_id, trip in TRIPS.items():
        if trip.get("service_id") not in service_ids:
            continue
        if TRIP_ORIGIN_STOP_ID.get(trip_id) != origin_stop_id:
            continue
        dep_sec = TRIP_ORIGIN_DEP_SEC.get(trip_id)
        if dep_sec is None:
            continue
        if (dep_sec - DEPARTURE_WINDOW_OPEN_SEC) <= now_sec <= (dep_sec + DEPARTURE_WINDOW_CLOSE_SEC):
            rows.append({
                "trip_id": trip_id,
                "route_id": trip.get("route_id"),
                "shape_id": trip.get("shape_id"),
                "service_id": trip.get("service_id"),
                "scheduled_departure_sec": dep_sec,
            })
    rows.sort(key=lambda x: (x["scheduled_departure_sec"], x["trip_id"]))
    return rows


# ============================================================
# nearest stop
# ============================================================
def _nearest_progress_stop(trip_id, current_distance_m):
    prog = TRIP_PROGRESS.get(trip_id, [])
    if not prog:
        return None, None, None, None

    best = None
    for p in prog:
        d = float(p["distance_m"])
        gap = abs(d - current_distance_m)
        if best is None or gap < best["gap"]:
            best = {
                "gap": gap,
                "distance_m": d,
                "stop_id": p.get("stop_id"),
                "stop_sequence": p.get("stop_sequence"),
            }
    if best is None:
        return None, None, None, None

    stop_id = best["stop_id"]
    stop_name = STOPS.get(stop_id, {}).get("name", stop_id)
    return stop_id, best["stop_sequence"], stop_name, best["distance_m"]


# ============================================================
# candidate scoring (CANDIDATE_SEARCH 内)
# ============================================================
def evaluate_candidate_once(trip_id, lat, lon, now_dt):
    """複数便選択中の評価。始発周辺に探索を限定することで、
    往復重複shapeの誤スナップを防ぐ。"""
    first_seq = _trip_first_stop_sequence(trip_id)
    if first_seq is None:
        return None
    sw = resolve_search_window(trip_id, first_seq)
    proj = _project_to_trip_distance(trip_id, lat, lon, search_window=sw)
    if proj is None:
        return None

    if proj["offset_m"] > MAX_SHAPE_OFFSET_M:
        return {
            "valid": False,
            "reason": "offset_too_large",
            "offset_m": proj["offset_m"],
        }

    now_sec = _seconds_since_midnight(now_dt)
    expected_distance_m = float(time_to_distance(trip_id, now_sec))
    current_distance_m = float(proj["distance_m"])
    distance_error_m = abs(current_distance_m - expected_distance_m)

    return {
        "valid": True,
        "current_distance_m": current_distance_m,
        "expected_distance_m": expected_distance_m,
        "distance_error_m": distance_error_m,
        "offset_m": proj["offset_m"],
    }


def update_candidate_scores(prev_scores, candidates, lat, lon, now_dt):
    """
    prev_scores: { trip_id: { score, error_count, gps_count } }
    candidates: find_departure_candidates の結果
    """
    new_scores = {}
    for c in candidates:
        tid = c["trip_id"]
        prev = prev_scores.get(tid) or {"score": 0.0, "error_count": 0, "gps_count": 0}
        ev = evaluate_candidate_once(tid, lat, lon, now_dt)
        if ev is None or not ev.get("valid"):
            new_scores[tid] = {
                "trip_id": tid,
                "route_id": c.get("route_id"),
                "shape_id": c.get("shape_id"),
                "scheduled_departure_sec": c.get("scheduled_departure_sec"),
                "score": prev["score"] + 9999.0,        # 投影失敗は重ペナルティ
                "error_count": prev["error_count"] + 1,
                "gps_count": prev["gps_count"] + 1,
                "last_distance_error_m": None,
                "last_offset_m": ev.get("offset_m") if ev else None,
                "last_reason": ev.get("reason") if ev else "projection_failed",
            }
            continue

        new_scores[tid] = {
            "trip_id": tid,
            "route_id": c.get("route_id"),
            "shape_id": c.get("shape_id"),
            "scheduled_departure_sec": c.get("scheduled_departure_sec"),
            "score": prev["score"] + ev["distance_error_m"],
            "error_count": prev["error_count"],
            "gps_count": prev["gps_count"] + 1,
            "last_distance_error_m": ev["distance_error_m"],
            "last_offset_m": ev["offset_m"],
            "last_reason": "ok",
        }
    return new_scores


def decide_trip_lock(scores, gps_count):
    """確定すべきか判定する。

    return: (locked_trip_id or None, reason_str)
    """
    if not scores:
        return None, "no_candidates"

    ranked = sorted(scores.values(), key=lambda x: x["score"])

    if gps_count < CANDIDATE_MIN_GPS_COUNT:
        return None, f"need_more_gps({gps_count}/{CANDIDATE_MIN_GPS_COUNT})"

    best = ranked[0]
    second_score = ranked[1]["score"] if len(ranked) >= 2 else None
    avg_err = best["score"] / max(1, best["gps_count"])

    if avg_err > CANDIDATE_MAX_AVG_ERROR_M:
        if gps_count >= CANDIDATE_FORCE_GPS_COUNT:
            return best["trip_id"], "forced_lock_low_quality"
        return None, f"avg_error_too_large({avg_err:.0f}m)"

    if second_score is None:
        return best["trip_id"], "lock_only_candidate"

    gap = second_score - best["score"]
    if gap >= CANDIDATE_LOCK_GAP_M:
        return best["trip_id"], "normal_lock"

    if gps_count >= CANDIDATE_FORCE_GPS_COUNT:
        return best["trip_id"], "forced_lock_close_race"

    return None, f"need_more_gap(gap={gap:.0f}m)"


# ============================================================
# locked tracking
# ============================================================
def update_passed_sequence(trip_id, current_seq, current_distance_m):
    """
    現在の current_seq から、次以降の停留所の距離を越えていたら seq を進める。
    TRIP_STOP_TIMES の配列インデックスで「次の停留所」を辿り、返却値は常に GTFS の stop_sequence。
    """
    sts = TRIP_STOP_TIMES.get(trip_id, [])
    if not sts:
        return int(current_seq) if current_seq is not None else 1
    if current_seq is None:
        return int(sts[0]["stop_sequence"])

    idx = _trip_index_for_sequence(trip_id, current_seq)
    if idx is None:
        return int(sts[0]["stop_sequence"])

    while idx < len(sts) - 1:
        next_seq = int(sts[idx + 1]["stop_sequence"])
        next_d = _stop_distance_on_trip(trip_id, next_seq)
        if next_d is None:
            break
        if current_distance_m + STOP_PASS_THRESHOLD_M >= next_d:
            idx += 1
        else:
            break
    return int(sts[idx]["stop_sequence"])


def track_locked_trip(trip_id, lat, lon, now_dt, prev_distance_m=None,
                     lock_progress=None):
    """
    TRIP_LOCKED 中の遅延追跡。
    lock_progress = {
        current_stop_sequence: int,
        max_passed_stop_sequence: int,
        last_distance_m: float,
    }
    シーケンスベースで shape 探索範囲を限定し、往復重複shapeの誤スナップを防ぐ。
    始発保留中は 遅延=0、進捗=0 とし、早着配信を防ぐ。
    戻り値に lock_progress_next を含める。
    """
    progress = dict(lock_progress or {})
    first_seq = _trip_first_stop_sequence(trip_id)
    _first = int(first_seq) if first_seq is not None else 1
    raw_cur = progress.get("current_stop_sequence")
    cur_seq = int(raw_cur) if raw_cur is not None else _first
    raw_max = progress.get("max_passed_stop_sequence")
    max_passed_seq = int(raw_max) if raw_max is not None else cur_seq

    # 探索範囲（trip起点基準）を計算
    sw = resolve_search_window(trip_id, cur_seq)
    proj = _project_to_trip_distance(
        trip_id, lat, lon,
        anchor_distance_m=prev_distance_m,
        search_window=sw,
    )
    if proj is None:
        return None

    current_distance_m = float(proj["distance_m"])

    # 逆行・ジャンプ検知
    delta_from_prev_m = None
    rejected_motion = None
    if prev_distance_m is not None:
        delta_from_prev_m = round(current_distance_m - float(prev_distance_m), 1)
        if delta_from_prev_m < -LOCKED_REVERSE_REJECT_M:
            rejected_motion = f"reverse_too_large({delta_from_prev_m:.0f}m)"
        elif abs(delta_from_prev_m) > LOCKED_JUMP_REJECT_M:
            rejected_motion = f"jump_too_large({delta_from_prev_m:.0f}m)"

    # 逆行を検知した場合は現在距離を保護する（seq は下げない）
    if rejected_motion and prev_distance_m is not None:
        current_distance_m = max(current_distance_m, float(prev_distance_m))

    # シーケンス更新
    new_seq = update_passed_sequence(trip_id, cur_seq, current_distance_m)
    if new_seq < max_passed_seq:
        new_seq = max_passed_seq  # 一旦通過したら逆行させない
    new_max_passed = max(max_passed_seq, new_seq)

    # 始発保留判定（課題1対策）
    now_sec = _seconds_since_midnight(now_dt)
    sched_dep_sec = TRIP_ORIGIN_DEP_SEC.get(trip_id)
    holding_at_origin = False
    if sched_dep_sec is not None and now_sec < int(sched_dep_sec):
        # まだ出発予定時刻前
        if current_distance_m < ORIGIN_HOLD_RELEASE_DISTANCE_M and first_seq is not None and new_seq <= int(first_seq):
            holding_at_origin = True

    if holding_at_origin:
        expected_distance_m = 0.0
        expected_time_sec = int(sched_dep_sec)
        delay_sec = 0
        distance_error_m = current_distance_m
        vehicle_stop_status = "STOPPED_AT"
    else:
        expected_distance_m = float(time_to_distance(trip_id, now_sec))
        distance_error_m = abs(current_distance_m - expected_distance_m)
        expected_time_sec = distance_to_time(trip_id, current_distance_m)
        delay_sec = int(now_sec - expected_time_sec)
        vehicle_stop_status = "IN_TRANSIT_TO"

    nearest_stop_id, nearest_stop_seq, nearest_stop_name, nearest_stop_dist = _nearest_progress_stop(
        trip_id, current_distance_m
    )

    if (not holding_at_origin
        and nearest_stop_dist is not None
        and abs(nearest_stop_dist - current_distance_m) <= STOP_NEAR_GEO_THRESHOLD_M):
        vehicle_stop_status = "STOPPED_AT"

    trip = TRIPS.get(trip_id, {})
    lock_progress_next = {
        "current_stop_sequence": int(new_seq),
        "max_passed_stop_sequence": int(new_max_passed),
        "last_distance_m": round(current_distance_m, 1),
        "last_updated_at": int(now_dt.timestamp()),
        "holding_at_origin": bool(holding_at_origin),
    }

    stops_progress = []
    delay_for_eta = 0 if holding_at_origin else int(delay_sec)
    for st in TRIP_STOP_TIMES.get(trip_id, []):
        seq = int(st["stop_sequence"])
        sid = st["stop_id"]
        scheduled_arrival_sec = int(st["arrival_sec"])
        estimated_arrival_sec = int(scheduled_arrival_sec + delay_for_eta)
        if seq < int(new_seq):
            sp_status = "passed"
        elif seq == int(new_seq):
            sp_status = "current"
        else:
            sp_status = "future"
        stop_name = (STOPS.get(sid) or {}).get("name") or sid
        stops_progress.append(
            {
                "stop_sequence": seq,
                "stop_id": sid,
                "stop_name": stop_name,
                "scheduled_arrival_sec": scheduled_arrival_sec,
                "estimated_arrival_sec": estimated_arrival_sec,
                "status": sp_status,
            }
        )

    return {
        "trip_id": trip_id,
        "route_id": trip.get("route_id"),
        "route_name": _route_name(trip.get("route_id")),
        "shape_id": trip.get("shape_id"),
        "current_distance_m": round(current_distance_m, 1),
        "expected_distance_m": round(expected_distance_m, 1),
        "distance_error_m": round(distance_error_m, 1),
        "projection_offset_m": round(proj["offset_m"], 1),
        "abs_distance_m": round(proj["abs_distance_m"], 1),
        "expected_time_sec": int(expected_time_sec),
        "delay_sec": int(delay_sec),
        "nearest_stop_id": nearest_stop_id,
        "nearest_stop_name": nearest_stop_name,
        # 従来名称互換: nearest だったものを current_stop_sequence として出していたが、
        # 今回からは 進捗シーケンス をセットする。
        "current_stop_sequence": int(new_seq),
        "max_passed_stop_sequence": int(new_max_passed),
        "holding_at_origin": bool(holding_at_origin),
        "vehicle_stop_status": vehicle_stop_status,
        "delta_from_prev_m": delta_from_prev_m,
        "rejected_motion": rejected_motion,
        "lock_progress_next": lock_progress_next,
        "stops_progress": stops_progress,
    }


def _is_trip_finished(
    trip_id,
    now_dt,
    current_distance_m=None,
    nearest_stop_id=None,
    current_stop_sequence=None,
    max_passed_stop_sequence=None,
):
    """
    便終了判定。

    戻り値: (finished, reason)
      reason は _reset_to_idle の lock_reason に渡す文字列。finished が False のとき None。

    True となる条件:
      1. 従来の時間タイムアウト
         now_sec > 終点到着予定時刻 + LOCKED_END_GRACE_SEC → reason "trip_finished"
      2. 終点到着の即時終了（猶予15分を待たない）
         - 現在時刻が 終着予定の END_ARRIVAL_EARLY_WINDOW_SEC 前〜
         - かつ 次のいずれか:
           * max_passed_stop_sequence または current_stop_sequence が終点の stop_sequence に達している
           * current_distance_m が終点の累積距離との差が END_STOP_DISTANCE_THRESHOLD_M 以内
         → reason "arrived_at_destination"

    nearest_stop_id は後方互換のため引数として受け取る（将来の条件拡張用）。
    """
    prog = TRIP_PROGRESS.get(trip_id, [])
    if not prog:
        return False, None

    end_point = prog[-1]
    end_time_sec = int(end_point["time_sec"])
    end_distance_m = float(end_point["distance_m"])
    end_seq = end_point.get("stop_sequence")
    if end_seq is None:
        end_seq = _trip_last_sequence(trip_id)

    now_sec = _seconds_since_midnight(now_dt)
    if now_sec > end_time_sec + LOCKED_END_GRACE_SEC:
        return True, "trip_finished"

    # 終着予定の少し手前から「到着」とみなせれば即 IDLE（終点で長く LOCKED しない）
    if now_sec >= end_time_sec - END_ARRIVAL_EARLY_WINDOW_SEC:
        seq_at_end = False
        if end_seq is not None:
            if max_passed_stop_sequence is not None and int(max_passed_stop_sequence) >= int(end_seq):
                seq_at_end = True
            elif current_stop_sequence is not None and int(current_stop_sequence) >= int(end_seq):
                seq_at_end = True

        dist_at_end = False
        if current_distance_m is not None:
            remaining_m = abs(float(current_distance_m) - end_distance_m)
            if remaining_m <= END_STOP_DISTANCE_THRESHOLD_M:
                dist_at_end = True

        if seq_at_end or dist_at_end:
            return True, "arrived_at_destination"

    return False, None


# ============================================================
# smoothing (TRIP_LOCKED only)
# ============================================================
def smooth_locked_position(lat, lon, prev_state):
    if not prev_state:
        return lat, lon
    plat = prev_state.get("lat")
    plon = prev_state.get("lon")
    if plat is None or plon is None:
        return lat, lon
    a = SMOOTH_ALPHA
    return a * lat + (1 - a) * float(plat), a * lon + (1 - a) * float(plon)


# ============================================================
# state machine
# ============================================================
DEFAULT_LOCK = {
    "lock_state": "IDLE",
    "locked_trip_id": None,
    "candidate_trips": [],
    "candidate_scores": {},
    "candidate_gps_count": 0,
    "lock_confirmed_at": None,
    "lock_reason": None,
    "acc_on_at": None,
    "origin_stop_id": None,
    "origin_zone_entered_at": None,
    "departure_window_opened_at": None,
    "last_accepted_at": None,
    # シーケンシャルトラッキング（課題2対応）
    "locked_trip_progress": None,
}


def load_lock_state(prev_state):
    if not prev_state:
        return dict(DEFAULT_LOCK)
    raw = prev_state.get("lock") or {}
    out = dict(DEFAULT_LOCK)
    for k in DEFAULT_LOCK:
        if k in raw:
            out[k] = raw[k]
    # 互換: 旧 trip フィールドから locked_trip_id を復旧（移行期）
    if not out.get("locked_trip_id") and out.get("lock_state") == "TRIP_LOCKED":
        t = prev_state.get("trip") or {}
        if t.get("trip_id"):
            out["locked_trip_id"] = t["trip_id"]
    return out


def _reset_to_idle(reason):
    s = dict(DEFAULT_LOCK)
    s["lock_reason"] = reason
    return s


def advance_state(prev_state, lat, lon, now_dt, accepted, accuracy):
    """
    ステートマシン本体。
    return: (new_lock_state_dict, trip_match_or_None, debug_dict)
    """
    lock = load_lock_state(prev_state)
    now_unix = int(now_dt.timestamp())
    debug = {"prev_state": lock["lock_state"], "transitions": []}

    # ----- 30分欠損があれば IDLE 復帰 -----
    last_acc = lock.get("last_accepted_at")
    if last_acc is not None and (now_unix - int(last_acc)) > NO_GPS_IDLE_SEC:
        lock = _reset_to_idle("no_gps_timeout")
        debug["transitions"].append("→IDLE(no_gps_timeout)")
        return lock, None, debug

    # ----- 失敗GPSはステートを進めない（ログだけ残す） -----
    if not accepted:
        return lock, None, {**debug, "skipped_for_not_accepted": True}

    # ----- IDLE → PRE_SERVICE_MOVE -----
    if lock["lock_state"] == "IDLE":
        lock["lock_state"] = "PRE_SERVICE_MOVE"
        lock["acc_on_at"] = now_unix
        debug["transitions"].append("IDLE→PRE_SERVICE_MOVE")

    # ----- PRE_SERVICE_MOVE タイムアウト -----
    if lock["lock_state"] == "PRE_SERVICE_MOVE":
        acc_on = lock.get("acc_on_at") or now_unix
        if (now_unix - int(acc_on)) > PRE_SERVICE_MAX_SEC:
            lock = _reset_to_idle("pre_service_timeout")
            lock["lock_state"] = "PRE_SERVICE_MOVE"
            lock["acc_on_at"] = now_unix
            debug["transitions"].append("PRE_SERVICE_MOVE_RESET(timeout)")

    # ----- PRE_SERVICE_MOVE → ORIGIN_WAITING -----
    if lock["lock_state"] == "PRE_SERVICE_MOVE":
        z = detect_origin_zone(lat, lon)
        if z is not None:
            origin_stop_id, _ = z
            lock["lock_state"] = "ORIGIN_WAITING"
            lock["origin_stop_id"] = origin_stop_id
            lock["origin_zone_entered_at"] = now_unix
            debug["transitions"].append(f"PRE_SERVICE_MOVE→ORIGIN_WAITING({origin_stop_id})")

    # ----- ORIGIN_WAITING タイムアウト / ゾーン離脱 -----
    if lock["lock_state"] == "ORIGIN_WAITING":
        entered = lock.get("origin_zone_entered_at") or now_unix
        if (now_unix - int(entered)) > ORIGIN_WAITING_MAX_SEC:
            lock = _reset_to_idle("origin_waiting_timeout")
            debug["transitions"].append("ORIGIN_WAITING→IDLE(timeout)")

    if lock["lock_state"] == "ORIGIN_WAITING":
        if is_outside_origin_zone(lat, lon, lock.get("origin_stop_id")):
            # 一旦ゾーンから離れたら、別の origin に再入する可能性あり → PRE_SERVICE_MOVE に戻す
            lock["lock_state"] = "PRE_SERVICE_MOVE"
            lock["origin_stop_id"] = None
            lock["origin_zone_entered_at"] = None
            debug["transitions"].append("ORIGIN_WAITING→PRE_SERVICE_MOVE(left_zone)")

    # ----- ORIGIN_WAITING → CANDIDATE_SEARCH -----
    if lock["lock_state"] == "ORIGIN_WAITING":
        service_ids = set(resolve_active_service_ids(now_dt))
        candidates = find_departure_candidates(lock["origin_stop_id"], now_dt, service_ids)
        if candidates:
            lock["lock_state"] = "CANDIDATE_SEARCH"
            lock["candidate_trips"] = [c["trip_id"] for c in candidates]
            lock["candidate_scores"] = {}
            lock["candidate_gps_count"] = 0
            lock["departure_window_opened_at"] = now_unix
            debug["transitions"].append(
                f"ORIGIN_WAITING→CANDIDATE_SEARCH(n={len(candidates)})"
            )
            debug["candidates_found"] = [c["trip_id"] for c in candidates]

    # ----- CANDIDATE_SEARCH 中処理 -----
    trip_match = None
    if lock["lock_state"] == "CANDIDATE_SEARCH":
        service_ids = set(resolve_active_service_ids(now_dt))
        # 候補集合を最新の時刻窓で更新（窓を抜けた便は除外）
        live_candidates = find_departure_candidates(lock["origin_stop_id"], now_dt, service_ids)
        live_ids = {c["trip_id"] for c in live_candidates}
        # 既に追跡中の trip_id は維持しつつ、live に消えたものは候補から外す
        prev_scores = {
            tid: s for tid, s in (lock.get("candidate_scores") or {}).items()
            if tid in live_ids
        }
        # 新規 live 候補は prev 0 から開始
        scores = update_candidate_scores(prev_scores, live_candidates, lat, lon, now_dt)
        gps_count = int(lock.get("candidate_gps_count", 0)) + 1

        lock["candidate_scores"] = scores
        lock["candidate_trips"] = list(scores.keys())
        lock["candidate_gps_count"] = gps_count

        decided_trip_id, reason = decide_trip_lock(scores, gps_count)
        debug["candidate_lock_reason"] = reason

        if decided_trip_id:
            lock["lock_state"] = "TRIP_LOCKED"
            lock["locked_trip_id"] = decided_trip_id
            lock["lock_confirmed_at"] = now_unix
            lock["lock_reason"] = reason
            # シーケンス追跡の初期化（課題2対応）— GTFS 始発の実 stop_sequence を使用
            _init_seq = _trip_first_stop_sequence(decided_trip_id)
            _init_seq = int(_init_seq) if _init_seq is not None else 1
            lock["locked_trip_progress"] = {
                "current_stop_sequence": _init_seq,
                "max_passed_stop_sequence": _init_seq,
                "last_distance_m": 0.0,
                "last_updated_at": now_unix,
                "holding_at_origin": True,
            }
            debug["transitions"].append(f"CANDIDATE_SEARCH→TRIP_LOCKED({decided_trip_id};{reason})")

    # ----- TRIP_LOCKED 中処理 -----
    if lock["lock_state"] == "TRIP_LOCKED":
        trip_id = lock.get("locked_trip_id")
        if not trip_id:
            lock = _reset_to_idle("locked_without_trip_id")
            debug["transitions"].append("TRIP_LOCKED→IDLE(missing_trip_id)")
        else:
            # 平滑化
            slat, slon = smooth_locked_position(lat, lon, prev_state)
            prev_trip = (prev_state or {}).get("trip") or {}
            prev_dist = prev_trip.get("current_distance_m")
            trip_match = track_locked_trip(
                trip_id, slat, slon, now_dt,
                prev_distance_m=prev_dist,
                lock_progress=lock.get("locked_trip_progress"),
            )

            # シーケンス進捗を lock に反映
            if trip_match and trip_match.get("lock_progress_next"):
                lock["locked_trip_progress"] = trip_match["lock_progress_next"]

            lp = lock.get("locked_trip_progress") or {}
            if trip_match:
                cur_seq = trip_match.get("current_stop_sequence")
                max_seq = trip_match.get("max_passed_stop_sequence")
                dist_m = trip_match.get("current_distance_m")
                near_sid = trip_match.get("nearest_stop_id")
            else:
                cur_seq = lp.get("current_stop_sequence")
                max_seq = lp.get("max_passed_stop_sequence")
                dist_m = lp.get("last_distance_m")
                near_sid = None

            finished, finish_reason = _is_trip_finished(
                trip_id,
                now_dt,
                current_distance_m=dist_m,
                nearest_stop_id=near_sid,
                current_stop_sequence=cur_seq,
                max_passed_stop_sequence=max_seq,
            )
            if finished:
                lock = _reset_to_idle(finish_reason or "trip_finished")
                debug["transitions"].append(
                    f"TRIP_LOCKED→IDLE({finish_reason or 'trip_finished'} {trip_id})"
                )
                trip_match = None

    # ----- last_accepted_at 更新 -----
    lock["last_accepted_at"] = now_unix

    return lock, trip_match, debug


# ============================================================
# validation
# ============================================================
def _validate_api_key(req):
    if not API_KEY:
        return True
    return req.headers.get("X-API-KEY") == API_KEY


def _validate_gps(data):
    lat = data.get("lat")
    lon = data.get("lon")

    if lat is None or lon is None:
        return False, "missing_lat_lon"

    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return False, "invalid_lat_lon_type"

    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return False, "out_of_service_area"

    acc = data.get("accuracy")
    if acc is not None:
        try:
            acc = float(acc)
            if acc > 100:
                return False, "accuracy_too_low"
        except Exception:
            pass

    return True, None


# ============================================================
# persistence
# ============================================================
def _thin_trip(tm):
    if not tm:
        return None
    keys = [
        "trip_id", "route_id", "route_name", "shape_id",
        "current_distance_m", "expected_distance_m", "distance_error_m",
        "projection_offset_m", "abs_distance_m",
        "expected_time_sec", "delay_sec",
        "nearest_stop_id", "nearest_stop_name",
        "current_stop_sequence", "max_passed_stop_sequence",
        "holding_at_origin",
        "vehicle_stop_status",
        "delta_from_prev_m", "rejected_motion",
        "stops_progress",
    ]
    return {k: _safe_firestore_value(tm.get(k)) for k in keys if k in tm}


def _thin_lock(lock):
    if not lock:
        return None
    out = {}
    for k in DEFAULT_LOCK:
        out[k] = _safe_firestore_value(lock.get(k))
    return out


def _persist_gtfs_rt_audit(vehicle_id, state, trip_match, pb):
    if not ENABLE_GTFS_RT_AUDIT:
        return
    ts_unix = int(state.get("timestamp_unix", int(time.time())))
    snapshot_id = f"{ts_unix}_{vehicle_id}"
    doc = {
        "vehicle_id": vehicle_id,
        "timestamp_unix": ts_unix,
        "server_created_at": _firestore_mod.SERVER_TIMESTAMP,
        "expire_at": _expire_at(),
        "feed_size_bytes": len(pb),
        "trip_id": trip_match.get("trip_id") if trip_match else None,
        "route_id": trip_match.get("route_id") if trip_match else None,
        "lat": state.get("lat"),
        "lon": state.get("lon"),
        "has_trip_update": bool(trip_match and trip_match.get("trip_id")),
        "feed_b64": base64.b64encode(pb).decode("ascii"),
    }
    db.document(f"vehicles/{vehicle_id}/gtfs_rt_audit/{snapshot_id}").set(_safe_firestore_value(doc))


def _persist_observation(
    *,
    vehicle_id,
    payload,
    observed_dt,
    accepted,
    reject_reason,
    prev_state,
    new_lock,
    trip_match,
    debug,
):
    ts_unix = int(observed_dt.timestamp())
    lat = payload.get("lat")
    lon = payload.get("lon")
    event_id = _make_event_id(vehicle_id, ts_unix, lat, lon)

    gps_block = {
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "accuracy": _safe_firestore_value(payload.get("accuracy")),
        "speed": _safe_firestore_value(payload.get("speed")),
        "heading": _safe_firestore_value(payload.get("heading")),
    }

    thin_trip = _thin_trip(trip_match)
    thin_lock = _thin_lock(new_lock)

    event_ref = db.document(f"vehicles/{vehicle_id}/events/{event_id}")
    latest_ref = db.document(f"vehicles/{vehicle_id}/state/latest")

    batch = db.batch()

    # gps_logs（生ログ・互換）
    log_doc = {
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "timestamp": payload.get("timestamp"),
        "timestamp_unix": ts_unix,
        "server_received_at": observed_dt.isoformat(),
        "created_at": _firestore_mod.SERVER_TIMESTAMP,
        "expire_at": _expire_at(),
        "accepted": accepted,
        "reject_reason": reject_reason,
        "lat": gps_block["lat"],
        "lon": gps_block["lon"],
        "accuracy": gps_block["accuracy"],
        "speed": gps_block["speed"],
        "heading": gps_block["heading"],
        "raw": _safe_firestore_value(payload),
        "trip_match": _safe_firestore_value(thin_trip),
        "lock": _safe_firestore_value(thin_lock),
        "lock_debug": _safe_firestore_value(debug),
    }
    batch.set(db.collection("gps_logs").document(event_id), _safe_firestore_value(log_doc))

    # detail event
    event_doc = {
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "observed_at": payload.get("timestamp"),
        "observed_unix": ts_unix,
        "server_received_at": observed_dt.isoformat(),
        "created_at": _firestore_mod.SERVER_TIMESTAMP,
        "expire_at": _expire_at(),
        "accepted": accepted,
        "reject_reason": reject_reason,
        "gps": gps_block,
        "raw": _safe_firestore_value(payload),
        "trip_match": _safe_firestore_value(thin_trip),
        "lock": _safe_firestore_value(thin_lock),
        "lock_debug": _safe_firestore_value(debug),
        "system": {
            "algorithm_version": ALGORITHM_VERSION,
            "precomputed_version": PRECOMPUTED_VERSION,
            "thresholds": {
                "ORIGIN_GEOFENCE_RADIUS_M": ORIGIN_GEOFENCE_RADIUS_M,
                "DEPARTURE_WINDOW_OPEN_SEC": DEPARTURE_WINDOW_OPEN_SEC,
                "DEPARTURE_WINDOW_CLOSE_SEC": DEPARTURE_WINDOW_CLOSE_SEC,
                "CANDIDATE_MIN_GPS_COUNT": CANDIDATE_MIN_GPS_COUNT,
                "CANDIDATE_FORCE_GPS_COUNT": CANDIDATE_FORCE_GPS_COUNT,
                "CANDIDATE_LOCK_GAP_M": CANDIDATE_LOCK_GAP_M,
                "CANDIDATE_MAX_AVG_ERROR_M": CANDIDATE_MAX_AVG_ERROR_M,
                "MAX_SHAPE_OFFSET_M": MAX_SHAPE_OFFSET_M,
                "NO_GPS_IDLE_SEC": NO_GPS_IDLE_SEC,
            },
        },
    }
    batch.set(event_ref, _safe_firestore_value(event_doc))

    # candidate scores 詳細
    scores = (new_lock or {}).get("candidate_scores") or {}
    for idx, (tid, s) in enumerate(
        sorted(scores.items(), key=lambda kv: kv[1].get("score", 1e18))[:CANDIDATE_STORE_LIMIT],
        start=1,
    ):
        candidate_id = f"{idx:02d}_{tid}"
        candidate_doc = {
            "rank": idx,
            "event_id": event_id,
            "vehicle_id": vehicle_id,
            "observed_unix": ts_unix,
            "created_at": _firestore_mod.SERVER_TIMESTAMP,
            "expire_at": _expire_at(),
            **_safe_firestore_value(s),
        }
        batch.set(event_ref.collection("candidates").document(candidate_id), candidate_doc)

    # latest（out-of-order 防止: 古いタイムスタンプは latest を更新しない）
    prev_ts = (prev_state or {}).get("timestamp_unix") or 0
    skip_latest_update = False
    if accepted and ts_unix < int(prev_ts):
        skip_latest_update = True

    if not skip_latest_update:
        latest_doc = {
            "vehicle_id": vehicle_id,
            "last_event_id": event_id,
            "lat": gps_block["lat"],
            "lon": gps_block["lon"],
            "accuracy": gps_block["accuracy"],
            "speed": gps_block["speed"],
            "heading": gps_block["heading"],
            "timestamp": payload.get("timestamp"),
            "timestamp_unix": ts_unix,
            "server_received_at": observed_dt.isoformat(),
            "updated_at": _firestore_mod.SERVER_TIMESTAMP,
            "accepted": accepted,
            "reject_reason": reject_reason,
            "trip": _safe_firestore_value(thin_trip),
            "lock": _safe_firestore_value(thin_lock),
            "algorithm_version": ALGORITHM_VERSION,
        }
        batch.set(latest_ref, _safe_firestore_value(latest_doc), merge=True)

    batch.commit()
    return event_id, skip_latest_update


# ============================================================
# GTFS-RT builder
# ============================================================
def build_stop_time_updates(trip_id, delay_sec, current_distance_m=None,
                            current_stop_sequence=None, holding_at_origin=False):
    """
    始発保留中は delay_sec を 0 に強制し、マイナス遅延（早着）を配信しない。
    進捗済みそれより手前の停留所は current_stop_sequence も考慮して除外。
    """
    effective_delay = 0 if holding_at_origin else int(delay_sec)
    out = []
    for st in TRIP_STOP_TIMES.get(trip_id, []):
        seq = st["stop_sequence"]
        stop_shape_d = STOP_TO_SHAPE_DIST.get((trip_id, seq))

        # 距離ベースでの通過済み除外
        if current_distance_m is not None and stop_shape_d is not None:
            if stop_shape_d < current_distance_m - 30:
                continue

        # シーケンスベースでの通過済み除外
        if current_stop_sequence is not None and seq < int(current_stop_sequence):
            continue

        stu = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate()
        stu.stop_sequence = seq
        stu.stop_id = st["stop_id"]
        stu.arrival.delay = effective_delay
        stu.departure.delay = effective_delay
        out.append(stu)
    return out


def build_gtfs_rt_feed(lat, lon, timestamp_unix, trip_match, vehicle_id=VEHICLE_ID):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = int(timestamp_unix)

    ent = feed.entity.add()
    ent.id = f"vp_{vehicle_id}"
    ent.vehicle.vehicle.id = vehicle_id
    ent.vehicle.position.latitude = float(lat)
    ent.vehicle.position.longitude = float(lon)
    ent.vehicle.timestamp = int(timestamp_unix)

    if trip_match and trip_match.get("trip_id"):
        ent.vehicle.trip.trip_id = trip_match["trip_id"]
        ent.vehicle.trip.route_id = trip_match["route_id"]
        if trip_match.get("current_stop_sequence") is not None:
            ent.vehicle.current_stop_sequence = int(trip_match["current_stop_sequence"])
        ent.vehicle.current_status = (
            gtfs_realtime_pb2.VehiclePosition.STOPPED_AT
            if trip_match.get("vehicle_stop_status") == "STOPPED_AT"
            else gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO
        )

        stus = build_stop_time_updates(
            trip_match["trip_id"],
            int(trip_match.get("delay_sec", 0)),
            current_distance_m=trip_match.get("current_distance_m"),
            current_stop_sequence=trip_match.get("current_stop_sequence"),
            holding_at_origin=bool(trip_match.get("holding_at_origin")),
        )
        if stus:
            ent2 = feed.entity.add()
            ent2.id = f"tu_{vehicle_id}"
            tu = ent2.trip_update
            tu.trip.trip_id = trip_match["trip_id"]
            tu.trip.route_id = trip_match["route_id"]
            tu.vehicle.id = vehicle_id
            tu.timestamp = int(timestamp_unix)
            tu.stop_time_update.extend(stus)

    return feed.SerializeToString()


# ============================================================
# HTTP functions
# ============================================================
@https_fn.on_request()
def gps(req):
    _ensure_init()

    if req.method == "OPTIONS":
        return ("", 204, _cors_headers("application/json"))

    if req.method != "POST":
        return ("Method Not Allowed", 405, _cors_headers("text/plain"))

    if not _validate_api_key(req):
        return ({"ok": False, "error": "unauthorized"}, 401, _cors_headers("application/json"))

    try:
        data = req.get_json(force=True)
        if not isinstance(data, dict):
            raise ValueError("json body is not object")
    except Exception:
        return ({"ok": False, "error": "invalid_json"}, 400, _cors_headers("application/json"))

    observed_dt = _parse_payload_timestamp(data)
    accepted, reject_reason = _validate_gps(data)

    vehicle_id = data.get("vehicle_id", VEHICLE_ID)
    latest_ref = db.document(f"vehicles/{vehicle_id}/state/latest")
    snap = latest_ref.get()
    prev_state = snap.to_dict() if snap.exists else None

    new_lock = load_lock_state(prev_state)
    trip_match = None
    debug = {"prev_state": new_lock["lock_state"], "transitions": []}

    try:
        lat_val = data.get("lat")
        lon_val = data.get("lon")
        lat_f = float(lat_val) if lat_val is not None else 0.0
        lon_f = float(lon_val) if lon_val is not None else 0.0
        new_lock, trip_match, debug = advance_state(
            prev_state,
            lat_f,
            lon_f,
            observed_dt,
            accepted=accepted,
            accuracy=data.get("accuracy"),
        )
    except Exception as e:
        logger.error("gps state machine error: %s", e, exc_info=True)
        debug = {"error": f"state_machine_exception:{e}"}

    event_id, skipped_latest = _persist_observation(
        vehicle_id=vehicle_id,
        payload=data,
        observed_dt=observed_dt,
        accepted=accepted,
        reject_reason=reject_reason,
        prev_state=prev_state,
        new_lock=new_lock,
        trip_match=trip_match,
        debug=debug,
    )

    resp = {
        "ok": True,
        "accepted": accepted,
        "lock_state": new_lock["lock_state"],
        "origin_stop_id": new_lock.get("origin_stop_id"),
        "candidate_count": len(new_lock.get("candidate_trips") or []),
        "candidate_gps_count": new_lock.get("candidate_gps_count", 0),
        "locked_trip_id": new_lock.get("locked_trip_id"),
        "lock_reason": new_lock.get("lock_reason"),
        "skipped_latest_update": skipped_latest,
        "event_id": event_id,
    }

    if not accepted:
        resp["reject_reason"] = reject_reason

    if trip_match and trip_match.get("trip_id"):
        delay_sec = int(trip_match.get("delay_sec", 0) or 0)
        resp["trip"] = {
            "trip_id": trip_match["trip_id"],
            "route_id": trip_match["route_id"],
            "route_name": trip_match.get("route_name"),
            "nearest_stop": trip_match.get("nearest_stop_name"),
            "status": trip_match.get("vehicle_stop_status"),
            "current_distance_m": trip_match.get("current_distance_m"),
            "expected_distance_m": trip_match.get("expected_distance_m"),
            "distance_error_m": trip_match.get("distance_error_m"),
            "offset_m": trip_match.get("projection_offset_m"),
            "delay_sec": delay_sec,
            "delay_min": round(delay_sec / 60.0, 1),
            "current_stop_sequence": trip_match.get("current_stop_sequence"),
            "max_passed_stop_sequence": trip_match.get("max_passed_stop_sequence"),
            "holding_at_origin": trip_match.get("holding_at_origin"),
            "rejected_motion": trip_match.get("rejected_motion"),
        }

    return (resp, 200, _cors_headers("application/json"))


@https_fn.on_request()
def gtfs_rt(req):
    _ensure_init()

    if req.method == "OPTIONS":
        return ("", 204, _cors_headers("application/x-protobuf"))

    vehicle_id = VEHICLE_ID
    doc = db.document(f"vehicles/{vehicle_id}/state/latest").get()

    if not doc.exists:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
        feed.header.timestamp = int(time.time())
        pb = feed.SerializeToString()
        return (pb, 200, _cors_headers("application/x-protobuf"))

    state = doc.to_dict()

    # 再推定はしない。確定した trip だけを反映する。
    trip_match = None
    lock = state.get("lock") or {}
    if lock.get("lock_state") == "TRIP_LOCKED":
        trip_match = state.get("trip")  # latest に保存されている確定済み trip

    pb = build_gtfs_rt_feed(
        float(state.get("lat", 0.0)),
        float(state.get("lon", 0.0)),
        int(state.get("timestamp_unix", int(time.time()))),
        trip_match,
        vehicle_id,
    )

    _persist_gtfs_rt_audit(vehicle_id, state, trip_match, pb)
    return (pb, 200, _cors_headers("application/x-protobuf"))
