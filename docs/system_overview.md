# 現在のシステム構成

この文書は `backend/main.py`、`frontend/admin.html`、`firebase.json`、分析スクリプトから確認した構成をまとめたものです。クラウドに残るデータや現在のデプロイ状態は、接続して別途確認する必要があります。

## 稼働する部分

| 役割 | 実装・設定 |
| --- | --- |
| GPS受信と便推定 | `backend/main.py` の `gps` |
| GTFS-Realtime配信 | 同ファイルの `gtfs_rt` |
| Functionsの実行環境 | Python 3.13。`firebase.json` の `backend` を使用 |
| 永続化 | Cloud Firestore |
| Web公開 | Firebase Hosting、公開ディレクトリは `frontend` |
| 案内ページ | `frontend/index.html` |
| 管理画面 | `frontend/admin.html` |
| 地図用の静的データ | `frontend/gtfs_map_data.json` |

車載側のGPS送信プログラムは、このチェックアウトには含まれていません。

## GPS受信から配信まで

1. 初回呼び出しでFirebase AdminとGTFS・事前計算データを初期化する。
2. `POST /gps` でAPIキー（設定されている場合）とJSONを検証する。
3. 緯度・経度、サービス範囲、GPS精度などから採用可否を判定する。
4. 前回の車両状態を読み、状態機械で便の候補比較・固定・進捗・遅延を計算する。
5. 生ログ、詳細イベント、最新状態をFirestoreへ保存する。
6. `GET /gtfs_rt` は最新状態を読み、VehiclePositionと、確定便があればTripUpdateをprotobufで生成する。配信監査データも保存する。

状態は `IDLE → PRE_SERVICE_MOVE → ORIGIN_WAITING → CANDIDATE_SEARCH → TRIP_LOCKED` を中心に遷移します。終点到着・時刻超過・GPS欠損などで運行外に戻ります。現在のアルゴリズム識別子は `trip-lock-state-machine-v3.3-end-arrival` です。

便の推定と遅延追跡は現在ルールベースです。機械学習モデルやLLMの呼び出しは実装されていません。細かな閾値・例外処理は[詳細設計](new_architecture.md)と実コードを参照してください。

## データの保存先

実装・分析で指定されているプロジェクトIDは `mizuho-basuroke-f370e` です。

| 保存先 | 内容 | 主な用途 |
| --- | --- | --- |
| Firestore `gps_logs/{event_id}` | 受信GPS、採用可否、便推定・状態の記録 | 生ログ互換、管理画面、分析元 |
| `vehicles/{vehicle_id}/events/{event_id}` | GPS・便・状態遷移の詳細 | 個別受信の検証 |
| 上記イベント配下の `candidates` | 候補便とスコアの詳細 | 候補比較の検証 |
| `vehicles/{vehicle_id}/state/latest` | 最新位置、確定便、ロック状態 | 管理画面・GTFS-Realtime |
| `vehicles/{vehicle_id}/gtfs_rt_audit/{snapshot_id}` | 配信内容・protobufの記録 | GTFS-Realtime監査 |
| BigQuery `bus_gps_data.gps_logs_raw_changelog` | Firestoreログの集約元 | 分析用ビューの元データ |
| BigQuery `bus_gps_data.gps_flat` | GPSと運行状態を展開・重複排除したビュー | 分析の共通入力 |
| BigQuery `bus_gps_data.ground_truth` | 全走行から後処理で推定した便・遅延 | 評価用の参考ラベル |
| BigQuery `bus_gps_data.gps_eval` | GPS・当時の推定・参考ラベルを結合 | 比較・集計 |
| BigQuery `bus_gps_data.stop_events` / `weather` | 停留所通過・気象データ | 遅延や天候の分析 |

生GPSや認証情報の実体はリポジトリに保存されていません。`analysis/out/` にあるCSV・JSON・テキストは保存済みの集計結果で、原データの代替ではありません。`ground_truth` も人手ですべて確認した正解ではなく、走行後の軌跡を使う推定結果です。

## GTFSと事前計算

`backend/data/gtfs/` が路線・便・停留所・時刻表の元データです。`backend/precompute.py` はshape上の距離・時刻と進捗の対応等を計算し、同じディレクトリの `precomputed.json` に書き込みます。

同梱の事前計算データと、現在のスクリプトを実行した結果には不一致があります。検証なしに再生成して上書きせず、比較が必要なときはGTFSのコピーを別ディレクトリに置いて行ってください。

## 管理画面

`frontend/admin.html` はFirestoreの最新状態とGPSログを購読し、別途 `gtfs_rt` の出力を取得します。Leaflet、Firebase SDK、protobuf.js等を外部CDNから読み込みます。地図データはローカルの `gtfs_map_data.json` を使います。

現状は実証プロジェクトの接続先がコードに固定されています。ローカル用の接続設定切り替えは別途必要です。

## 分析と報告書

[analysis/README.md](../analysis/README.md)を入口として、処理の入出力とクラウドへの書き込み有無を確認してください。保存済みの図表は `graphs/`、報告書は `reports/` にあります。

今回の整理では、未使用Functionsひな型、Realtime Databaseの未使用設定、Word展開データ、旧版報告書、デバッグ一時出力、図表の同一コピーを削除しました。現行のAPI・便推定処理・GTFSデータ・管理画面と、分析ソース・保存済み集計・図表集・v5報告書を残しています。
