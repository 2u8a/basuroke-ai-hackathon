# 実証ログの分析

主にBigQueryの `mizuho-basuroke-f370e.bus_gps_data` と `backend/data/gtfs/` を入力にします。過去の集計結果は `out/`、公開用の図表集は [../graphs/](../graphs/README.md) にあります。

## 処理の入口

| 処理 | ファイル | 入出力・用途 |
| --- | --- | --- |
| GPSの展開・重複排除 | `sql/01_create_gps_flat_view.sql` | changelogから `gps_flat` ビューを作成 |
| 走行後の便・遅延推定 | `ground_truth.py` | GPS・GTFSから評価用の参考ラベルを作成する共通処理 |
| 便推定精度の集計 | `run_accuracy.py` | `gps_flat` を読み、`ground_truth` を更新、精度CSV等を保存 |
| 評価用ビュー | `sql/02_create_gps_eval_view.sql` | GPSと参考ラベルを結合した `gps_eval` を作成 |
| 停留所通過検出 | `stop_events.py` | `stop_events` テーブルを更新 |
| 気象データ登録 | `weather_data.py` | コード内の転記データから `weather` テーブルを更新 |
| 運行上の示唆 | `operational_insights.py` | 評価ビューの再作成、時間帯・停留所・天候別の集計 |
| 停留所遅延等の集計 | `stop_analysis.py` | `stop_events` 等を使いCSVを保存 |
| 図表・信頼性・費用 | `make_figures.py`、`reliability_analysis.py`、`cost_firestore.py` 等 | BigQueryや集計結果から図表・CSVを生成 |
| 個別検証 | `validate_one_day.py`、`_check_window_sideeffect.py` | 日単位の目視確認、副作用の検証 |

**クラウドへの書き込みを伴う処理があります。** 特に `run_accuracy.py` はBigQueryの `ground_truth` を `WRITE_TRUNCATE` で置き換えます。データを取り出すためだけに実行せず、読み取り用クエリで必要な列・期間を取得してください。

## 実行条件

バックエンドの依存関係に加え、分析処理に応じて `google-cloud-bigquery`、`numpy`、`pandas`、`matplotlib`、`folium` 等が必要です。BigQueryのデータ読み取り・ジョブ実行権限、更新処理には対象テーブル等への書き込み権限も必要です。スクリプトによってはimport時にBigQueryクライアントを初期化します。

過去のコードにはWindowsの日本語フォントのパスを指定する箇所があります。別OSでの図表生成ではフォント環境を確認してください。

## 保存先の扱い

- `out/*.csv`・`*.json`・集計テキスト：過去の分析結果として保持しています。
- `out/figures/fig2_delay_map.html`：保存済みの地図出力です。
- 生成される `out/**/*.png`：作業用出力としてGit管理対象から除外します。既存の同一コピーは `../graphs/` に集約しました。
- `out/**/_*`：デバッグ用の一時出力としてGit管理対象から除外します。

図表集に反映するときは、生成した画像を内容確認後に `../graphs/` へ配置します。図表名と内容は [図表一覧](../graphs/README.md) を参照してください。

評価用ラベルは全走行を見た後の推定です。オンライン推定の入力に混ぜず、精度を比較するときは対象期間・対象系統・採用条件を揃えてください。
