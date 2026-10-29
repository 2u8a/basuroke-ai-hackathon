"""
precompute.py — デプロイ前に1回実行するスクリプト

shapes / stops / stop_times から以下を計算し JSON へ書き出す。
  - SHAPES (累積距離付き)
  - TRIP_PROGRESS (仮想バスモデル)
  - STOP_TO_SHAPE_DIST
  - TRIP_SHAPE_START_ABS / TRIP_ROUTE_LENGTH
  - SHAPE_TOTAL_DISTANCE

出力: data/gtfs/precomputed.json

これにより main.py のコールドスタートで shape投影ループが不要になる。
"""

import csv
import json
import math
import os
import time

GTFS_DIR = os.path.join(os.path.dirname(__file__), "data", "gtfs")
EARTH_RADIUS_M = 6_371_000


def load_csv(filename):
    path = os.path.join(GTFS_DIR, filename)
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            cleaned = {}
            for k, v in row.items():
                if k is None:
                    continue
                key = k.strip()
                cleaned[key] = v.strip() if isinstance(v, str) else v
            rows.append(cleaned)
        return rows


def parse_gtfs_time(t):
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def haversine(lat1, lon1, lat2, lon2):
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def latlon_to_xy(lat, lon, ref_lat, ref_lon):
    x = math.radians(lon - ref_lon) * math.cos(math.radians(ref_lat)) * EARTH_RADIUS_M
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return x, y


def project_point_to_segment(lat, lon, a, b):
    ref_lat = (a["lat"] + b["lat"]) / 2.0
    ref_lon = (a["lon"] + b["lon"]) / 2.0
    px, py = latlon_to_xy(lat, lon, ref_lat, ref_lon)
    ax, ay = latlon_to_xy(a["lat"], a["lon"], ref_lat, ref_lon)
    bx, by = latlon_to_xy(b["lat"], b["lon"], ref_lat, ref_lon)
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
    qx = ax + t * dx
    qy = ay + t * dy
    offset_m = math.hypot(px - qx, py - qy)
    distance_m = a["dist_traveled"] + t * (b["dist_traveled"] - a["dist_traveled"])
    return {"distance_m": distance_m, "offset_m": offset_m, "t": t}


def project_to_shape(pts, lat, lon):
    best = None
    for i in range(len(pts) - 1):
        proj = project_point_to_segment(lat, lon, pts[i], pts[i + 1])
        if best is None or proj["offset_m"] < best["offset_m"]:
            best = proj
    return best


def main():
    t0 = time.time()

    # Load GTFS
    stops_raw = load_csv("stops.txt")
    stops = {
        r["stop_id"]: {"name": r["stop_name"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
        for r in stops_raw
    }

    trips_raw = load_csv("trips.txt")
    trips = {r["trip_id"]: r for r in trips_raw}

    stop_times_raw = load_csv("stop_times.txt")
    trip_stop_times = {}
    for r in stop_times_raw:
        tid = r["trip_id"]
        trip_stop_times.setdefault(tid, []).append({
            "stop_id": r["stop_id"],
            "arrival_sec": parse_gtfs_time(r["arrival_time"]),
            "departure_sec": parse_gtfs_time(r["departure_time"]),
            "stop_sequence": int(r["stop_sequence"]),
        })
    for tid in trip_stop_times:
        trip_stop_times[tid].sort(key=lambda x: x["stop_sequence"])

    # Shapes with cumulative distance
    shapes_raw = load_csv("shapes.txt")
    shapes = {}
    for r in shapes_raw:
        sid = r["shape_id"]
        shapes.setdefault(sid, []).append({
            "lat": float(r["shape_pt_lat"]),
            "lon": float(r["shape_pt_lon"]),
            "seq": int(r["shape_pt_sequence"]),
        })
    for sid, pts in shapes.items():
        pts.sort(key=lambda x: x["seq"])
        cum = 0.0
        pts[0]["dist_traveled"] = 0.0
        for i in range(1, len(pts)):
            cum += haversine(pts[i-1]["lat"], pts[i-1]["lon"], pts[i]["lat"], pts[i]["lon"])
            pts[i]["dist_traveled"] = cum

    shape_total_distance = {sid: pts[-1]["dist_traveled"] for sid, pts in shapes.items()}

    # Shapes for JSON (just coords + dist)
    shapes_out = {}
    for sid, pts in shapes.items():
        shapes_out[sid] = [
            {"lat": p["lat"], "lon": p["lon"], "dist_traveled": round(p["dist_traveled"], 2)}
            for p in pts
        ]

    # Stop → shape distance mapping + trip progress
    stop_to_shape_dist = {}  # "trip_id|stop_seq" -> distance
    trip_progress = {}
    trip_shape_start_abs = {}
    trip_route_length = {}

    for trip_id, sts in trip_stop_times.items():
        shape_id = trips[trip_id]["shape_id"]
        pts = shapes.get(shape_id, [])
        total_shape = shape_total_distance.get(shape_id, 0.0)

        if not sts or len(pts) < 2:
            trip_progress[trip_id] = []
            trip_shape_start_abs[trip_id] = 0.0
            trip_route_length[trip_id] = 0.0
            continue

        first_stop = stops.get(sts[0]["stop_id"])
        first_proj = project_to_shape(pts, first_stop["lat"], first_stop["lon"]) if first_stop else None
        base_abs = first_proj["distance_m"] if first_proj else 0.0
        trip_shape_start_abs[trip_id] = base_abs

        progress = []
        prev_rel = 0.0
        for idx, st in enumerate(sts):
            stop = stops.get(st["stop_id"])
            if not stop:
                continue
            proj = project_to_shape(pts, stop["lat"], stop["lon"])
            if proj is None:
                rel = prev_rel
                offset_m = None
            else:
                abs_d = proj["distance_m"]
                if idx == 0:
                    rel = 0.0
                else:
                    rel = abs_d - base_abs
                    while rel < (prev_rel - 20.0):
                        rel += total_shape
                    if rel < prev_rel:
                        rel = prev_rel
                offset_m = proj["offset_m"]

            prev_rel = rel
            key = f"{trip_id}|{st['stop_sequence']}"
            stop_to_shape_dist[key] = round(rel, 2)
            progress.append({
                "time_sec": st["arrival_sec"],
                "distance_m": round(rel, 2),
                "stop_id": st["stop_id"],
                "stop_sequence": st["stop_sequence"],
            })

        trip_progress[trip_id] = progress
        trip_route_length[trip_id] = round(progress[-1]["distance_m"], 2) if progress else 0.0

    # Write JSON
    output = {
        "shapes": shapes_out,
        "shape_total_distance": {k: round(v, 2) for k, v in shape_total_distance.items()},
        "trip_progress": trip_progress,
        "stop_to_shape_dist": stop_to_shape_dist,
        "trip_shape_start_abs": {k: round(v, 2) for k, v in trip_shape_start_abs.items()},
        "trip_route_length": trip_route_length,
    }

    out_path = os.path.join(GTFS_DIR, "precomputed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    elapsed = time.time() - t0
    size_kb = os.path.getsize(out_path) / 1024

    print(f"✅ precomputed.json 生成完了")
    print(f"   shapes:            {len(shapes_out)}")
    print(f"   trips:             {len(trip_progress)}")
    print(f"   stop_to_shape_dist:{len(stop_to_shape_dist)}")
    print(f"   ファイルサイズ:     {size_kb:.1f} KB")
    print(f"   処理時間:           {elapsed:.2f} 秒")


if __name__ == "__main__":
    main()
