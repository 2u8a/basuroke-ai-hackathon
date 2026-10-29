# Firebase Setup Guide（Module B / Backend）

本ドキュメントは、**現在の実装（HTTP + Cloud Functions Gen2 + Firestore）**を前提とした最新のセットアップ手順です。  
旧ドキュメントの Realtime Database 前提構成は使用しません。

---

# 🧭 アーキテクチャ概要

```

[Module A / Raspberry Pi]
│
│ HTTPS POST (X-API-KEY)
▼
Cloud Functions (Gen2 / Python)
│
├─ Firestore へ保存
│
└─ GTFS-RT (feed.pb) を生成
│
▼
HTTP GET /gtfs_rt

````

---

# 🔧 使用技術スタック

| 役割 | 技術 |
|------|------|
| サーバレスAPI | Cloud Functions (2nd Gen / Python 3.13) |
| 実行基盤 | Cloud Run（内部） |
| データ保存 | Firestore |
| 認証 | API Key（環境変数） |
| デプロイ | Firebase CLI |
| 仮想環境 | Python venv |

---

# 1️⃣ 前提条件

- Node.js LTS
- Python 3.11 以上（※安定版）
- Firebase CLI
- Google Cloud 課金有効（Blazeプラン）

---

# 2️⃣ プロジェクト初期化

```bash
firebase login
firebase use <project-id>
````

---

# 3️⃣ Backend ディレクトリ構成

```
backend/
 ├─ main.py
 ├─ requirements.txt
 ├─ data/
 └─ venv/
```

---

# 4️⃣ Python 仮想環境のセットアップ

```bash
cd backend
py -3.11 -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

# 5️⃣ requirements.txt

```txt
firebase-functions
functions-framework==3.5.0
firebase-admin
gtfs-realtime-bindings
```

---

# 6️⃣ main.py の基本構造

```python
import os
import firebase_admin
from firebase_admin import firestore
from firebase_functions import https_fn

firebase_admin.initialize_app()
db = firestore.client()

API_KEY = os.environ.get("API_KEY")

@https_fn.on_request()
def gps(req):
    key = req.headers.get("X-API-KEY")
    if key != API_KEY:
        return ("Unauthorized", 401)

    data = req.get_json()
    db.collection("gps_logs").add(data)

    return {"ok": True}

@https_fn.on_request()
def gtfs_rt(req):
    return ("binary-data", 200, {"Content-Type": "application/x-protobuf"})
```

---

# 7️⃣ デプロイ

```bash
firebase deploy --only functions
```

成功すると以下のURLが発行される：

```
https://us-central1-<project-id>.cloudfunctions.net/gps
https://us-central1-<project-id>.cloudfunctions.net/gtfs_rt
```

---

# 8️⃣ APIキー設定（Cloud Run）

Cloud Functions Gen2 は Cloud Run 上で動作します。

### 手順

1. Google Cloud Console を開く
2. Cloud Run → 対象サービス（gps）
3. 編集してデプロイ
4. 環境変数追加

```
KEY: API_KEY
VALUE: 任意の長い文字列
```

保存して再デプロイ。

---

# 9️⃣ 動作確認

## ❌ ヘッダーなし（401になる）

```bash
curl -X POST <gps-url>
```

## ✅ 正しいキー付き（200になる）

```bash
curl -X POST <gps-url> \
 -H "X-API-KEY: <your-key>" \
 -H "Content-Type: application/json" \
 -d '{"vehicle_id":"bus-01","lat":35.0,"lon":139.0}'
```

---

# 🔟 Firestore構造

```
gps_logs (collection)
 ├─ auto-id
 │    ├─ vehicle_id
 │    ├─ timestamp
 │    ├─ lat
 │    ├─ lon
 │    ├─ accuracy
 │    └─ speed (optional)
```

---

# 🔐 セキュリティ方針

* APIキー必須
* キーは公開リポジトリに含めない
* Module A 側は環境変数または .env 管理
* Firestore ルールはサーバー専用アクセス

---

# 💰 課金について

本構成は Blaze（従量課金）前提。

主な課金対象：

* Firestore 書き込み回数
* Cloud Run 実行時間
* ネットワーク転送量

PoC規模では月数百円程度。

---

# 📍 今後の拡張

* JPFS-JP照合ロジック追加
* 便特定アルゴリズム
* 遅延算出
* TripUpdate / VehiclePosition 対応
* Google Transit 提供用正式GTFS-RT出力

---

# 🎯 現在の正しい設計

本プロジェクトは **HTTP + Firestore 構成を正式仕様とする。**

Realtime Database は使用しない。

---

End of Document

```
