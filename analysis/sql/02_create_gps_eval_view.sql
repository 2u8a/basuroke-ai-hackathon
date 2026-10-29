-- 評価用ビュー: gps_flat に正解(ground_truth)を結合し、
-- 「当時の推定 est_trip」と「便一致フラグ」を付与する。
-- 精度集計・遅延分析・GPS品質分析の共通土台。

CREATE OR REPLACE VIEW `mizuho-basuroke-f370e.bus_gps_data.gps_eval` AS
SELECT
  f.*,
  g.gt_trip_id,
  g.gt_route_id,
  g.gt_in_service,
  g.gt_confident,
  g.gt_delay_sec,
  g.gt_nearest_stop_id,
  g.run_id,
  -- 当時システムが表示していた便（便固定中は locked、それ以外は trip_match）
  CASE WHEN f.lock_state = 'TRIP_LOCKED' THEN f.locked_trip_id ELSE f.tm_trip_id END AS est_trip_id,
  -- 便一致（正解が運行中・高信頼のときのみ有効）
  CASE
    WHEN g.gt_in_service AND g.gt_confident THEN
      CASE WHEN (CASE WHEN f.lock_state='TRIP_LOCKED' THEN f.locked_trip_id ELSE f.tm_trip_id END) = g.gt_trip_id
           THEN 1 ELSE 0 END
    ELSE NULL
  END AS trip_match_correct
FROM `mizuho-basuroke-f370e.bus_gps_data.gps_flat` f
LEFT JOIN `mizuho-basuroke-f370e.bus_gps_data.ground_truth` g
  USING (event_id);
