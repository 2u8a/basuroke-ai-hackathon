# Module B – GPS Ingestion & GTFS-RT Generator

このモジュールは、バス車両から送信されるGPSデータを受信し、
Firestoreに保存し、GTFS-Realtime (VehiclePosition) を生成するAPIサーバーです。

---

## 🏗 全体構成

```mermaid
flowchart TB
  subgraph Device
    A[Module A: Raspberry Pi + SIM]
  end

  subgraph Cloud
    B[Cloud Functions: gps]
    D[Cloud Functions: gtfs_rt]
    C[Firestore]
  end

  A -->|POST /gps JSON + X-API-KEY| B
  B -->|write gps_logs| C
  B -->|update latest| C
  D -->|read latest| C
  D -->|return feed.pb| E[GTFS-RT VehiclePosition]

````

* Module A は **Firestoreへ直接書き込まず**、必ず Module B の受信APIを経由する。
* Module B が受信データを整形し、Firestoreに「生ログ」と「最新状態」を保存する。
* Module B は Firestore の最新状態から GTFS-RT（VehiclePosition）を生成し、外部提供可能なURLで返す。Realtime (feed.pb)


---

## 📡 API エンドポイント

### 1️⃣ GPS送信（POST）

```

[https://us-central1-mizuho-basuroke-f370e.cloudfunctions.net/gps](https://us-central1-mizuho-basuroke-f370e.cloudfunctions.net/gps)

```

#### Headers

```

Content-Type: application/json
X-API-KEY: <APIキー>

````

#### Body (JSON)

```json
{
  "vehicle_id": "mizuho-bus-01",
  "timestamp": "2026-02-19T18:45:00+09:00",
  "lat": 35.771234,
  "lon": 139.345678,
  "accuracy": 8.5,
  "speed": 7.2,
  "heading": 180
}
````

#### 動作仕様

* 送信間隔：10秒
* 失敗時：その回は破棄（再送キュー不要）
* 異常値は accepted=false で保存

---

### 2️⃣ GTFS-Realtime取得（GET）

```
https://us-central1-mizuho-basuroke-f370e.cloudfunctions.net/gtfs_rt
```

* `VehiclePosition` のみ出力
* content-type: `application/x-protobuf`
* Firestore の latest データを参照

---


## 🗄 Firestore構造

### gps_logs（生ログ）

| フィールド              | 説明     |
| ------------------ | ------ |
| vehicle_id         | 車両ID   |
| lat/lon            | 緯度経度   |
| accuracy           | 精度(m)  |
| speed              | 速度     |
| heading            | 方位     |
| accepted           | 採用可否   |
| reject_reason      | 不採用理由  |
| server_received_at | 受信時刻   |
| raw                | 受信JSON |

---

### vehicles/{vehicle_id}/state/latest

最新の採用済みGPSデータのみ保存。

GTFS-RT生成時に参照する。

---

## 🧪 テスト方法

### GPS送信テスト（PowerShell）

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://us-central1-mizuho-basuroke-f370e.cloudfunctions.net/gps" `
  -Headers @{ "X-API-KEY" = "<APIキー>" } `
  -ContentType "application/json" `
  -Body '{"vehicle_id":"mizuho-bus-01","lat":35.7,"lon":139.3,"accuracy":8.5}'
```

### feed.pb確認

```powershell
Invoke-WebRequest `
  -Uri "https://us-central1-mizuho-basuroke-f370e.cloudfunctions.net/gtfs_rt" `
  -OutFile feed.pb
```

---
## で、ラズパイにどんなの書けばいいか

```python
import requests
from datetime import datetime, timezone

url = "https://us-central1-xxxx.cloudfunctions.net/gps"

headers = {
    "Content-Type": "application/json",
    "X-API-KEY": "APIキー"
}

data = {
    "vehicle_id": "mizuho-bus-01",
    "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
    "lat": 35.77,
    "lon": 139.34,
    "accuracy": 8.5
}

try:
    response = requests.post(url, json=data, headers=headers, timeout=5)
    print(response.status_code, response.text)
except requests.exceptions.RequestException as e:
    print("通信エラー:", e)
```
---

## ⚙ 技術スタック

* Firebase Cloud Functions (Gen2)
* Cloud Run
* Firestore
* firebase-admin
* gtfs-realtime-bindings

---

## 🎯 現在のステータス（Step1完了）

* GPS受信API完成
* 認証実装済み
* Firestore保存確認済み
* GTFS-RT生成確認済み

---

## 🚀 次ステップ

* JPFS-JP静的GTFSとの突合
* trip推定アルゴリズム実装
* 遅延判定ロジック
* リージョンを asia-northeast1 に統一

```
