-- 分析用フラット化ビュー
-- changelog の data(JSON文字列) からスカラー項目を抽出し、JST日時を付与する。
-- 5/28-5/30 は「元の拡張機能エクスポート」と「今回の全期間バックフィル」で
-- document_id が重複するため、document_id 単位で1行に重複排除する。

CREATE OR REPLACE VIEW `mizuho-basuroke-f370e.bus_gps_data.gps_flat` AS
WITH deduped AS (
  SELECT
    document_id,
    data,
    ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY timestamp DESC) AS rn
  FROM `mizuho-basuroke-f370e.bus_gps_data.gps_logs_raw_changelog`
  WHERE operation != 'DELETE'
    AND data IS NOT NULL
)
SELECT
  document_id AS event_id,
  CAST(JSON_VALUE(data, '$.timestamp_unix') AS INT64) AS ts_unix,
  TIMESTAMP_SECONDS(CAST(JSON_VALUE(data, '$.timestamp_unix') AS INT64)) AS ts_utc,
  DATETIME(TIMESTAMP_SECONDS(CAST(JSON_VALUE(data, '$.timestamp_unix') AS INT64)), 'Asia/Tokyo') AS jst_datetime,
  DATE(TIMESTAMP_SECONDS(CAST(JSON_VALUE(data, '$.timestamp_unix') AS INT64)), 'Asia/Tokyo') AS jst_date,
  EXTRACT(HOUR FROM DATETIME(TIMESTAMP_SECONDS(CAST(JSON_VALUE(data, '$.timestamp_unix') AS INT64)), 'Asia/Tokyo')) AS jst_hour,
  -- 生GPS
  CAST(JSON_VALUE(data, '$.lat') AS FLOAT64) AS lat,
  CAST(JSON_VALUE(data, '$.lon') AS FLOAT64) AS lon,
  CAST(JSON_VALUE(data, '$.accuracy') AS FLOAT64) AS accuracy,
  SAFE_CAST(JSON_VALUE(data, '$.speed') AS FLOAT64) AS speed,
  SAFE_CAST(JSON_VALUE(data, '$.heading') AS FLOAT64) AS heading,
  CAST(JSON_VALUE(data, '$.accepted') AS BOOL) AS accepted,
  JSON_VALUE(data, '$.reject_reason') AS reject_reason,
  JSON_VALUE(data, '$.server_received_at') AS server_received_at,
  JSON_VALUE(data, '$.timestamp') AS device_timestamp,
  -- ロック状態（当時のリアルタイム便固定）
  JSON_VALUE(data, '$.lock.lock_state') AS lock_state,
  JSON_VALUE(data, '$.lock.locked_trip_id') AS locked_trip_id,
  JSON_VALUE(data, '$.lock.lock_reason') AS lock_reason,
  JSON_VALUE(data, '$.lock.origin_stop_id') AS lock_origin_stop_id,
  SAFE_CAST(JSON_VALUE(data, '$.lock.candidate_gps_count') AS INT64) AS candidate_gps_count,
  -- trip_match（当時の推定結果）
  JSON_VALUE(data, '$.trip_match.trip_id') AS tm_trip_id,
  JSON_VALUE(data, '$.trip_match.route_id') AS tm_route_id,
  JSON_VALUE(data, '$.trip_match.shape_id') AS tm_shape_id,
  SAFE_CAST(JSON_VALUE(data, '$.trip_match.delay_sec') AS FLOAT64) AS tm_delay_sec,
  SAFE_CAST(JSON_VALUE(data, '$.trip_match.distance_error_m') AS FLOAT64) AS tm_distance_error_m,
  SAFE_CAST(JSON_VALUE(data, '$.trip_match.current_distance_m') AS FLOAT64) AS tm_current_distance_m,
  SAFE_CAST(JSON_VALUE(data, '$.trip_match.current_stop_sequence') AS INT64) AS tm_current_stop_sequence,
  SAFE_CAST(JSON_VALUE(data, '$.trip_match.max_passed_stop_sequence') AS INT64) AS tm_max_passed_stop_sequence,
  JSON_VALUE(data, '$.trip_match.vehicle_stop_status') AS tm_vehicle_stop_status,
  JSON_VALUE(data, '$.trip_match.nearest_stop_id') AS tm_nearest_stop_id,
  CAST(JSON_VALUE(data, '$.trip_match.holding_at_origin') AS BOOL) AS tm_holding_at_origin
FROM deduped
WHERE rn = 1;
