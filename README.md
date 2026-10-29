# 瑞穂町コミュニティバス  
## バスロケーションシステム（PoC）

---

## 🎯 Purpose

本プロジェクトは、瑞穂町コミュニティバスを対象とした  
**1台構成のバスロケーションシステム実証（PoC）**である。

目的は、清瀬市で採用されている

> **GPS → GTFS-Realtime生成 → Google Maps等への提供**

と同等の技術要件を満たしつつ、

**「1台構成でいくらで実現可能か」**  
を明確に示すことで、行政向け費用実証レポートを作成することである。

---

## 🧭 Scope / Out of Scope

### ✅ Scope
- バス **1台構成** のPoC
- Module A（車載）・Module B（クラウド）の設計・実装
- Google GTFS-Realtime仕様準拠の feed 生成
- 実証期間中のログ蓄積
- 費用・通信品質・更新頻度の評価

### ❌ Out of Scope
- 住民向け完成UI（Module Cは任意）
- 複数台本格運用（将来拡張として検討）
- 運転士操作を必要とする仕組み

---

## 🏗 System Architecture

### データフロー概要

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

### 設計方針

* Module A は **Firestoreへ直接書き込まない**
* 必ず Module B の受信APIを経由する
* Module B が整形・保存・GTFS-RT生成を一元管理する

---

## 🧩 Modules

### Module A（車載）

* GPS測位
* 約10秒間隔で HTTPS POST
* 運転士操作不要
* 24V → DC/DC → 機器給電
* LTE/Cat-M 通信

### Module B（クラウド）

* GPS受信API
* Firestoreへ生ログ保存
* GTFS-JPと照合
* GTFS-Realtime (VehiclePosition) 生成
* 外部取得可能なURLで公開

### Module C（任意）

* デモ用フロントエンド
* 本PoCでは必須ではない

---

## ⚙ Key Technical Requirements

* 更新頻度：通常10秒間隔（最大30秒以内）
* 運転士操作不要（完全自動）
* Google要件を満たすGTFS-Realtime feed
* 実証期間ログ保存
* 費用算出可能な構成

---

## 🌐 Endpoints

### POST /gps

車載モジュールから現在位置を送信。

詳細仕様 → `docs/api-spec.md`

### GET /gtfs_rt

GTFS-Realtime（VehiclePosition）取得。

---

## 📁 Repository Structure

```
.
│
├─ README.md
├─ backend/
│   └─ main.py
├─ frontend/
├─ firebase.json
├─ .firebaserc
└─ docs/
    ├─ architecture.md
    ├─ api-spec.md
    ├─ requirements.md
    ├─ roadmap.md
    └─ firebase_setup.md
```

---

## 📖 Documentation

* 要件定義: `docs/requirements.md`
* アーキテクチャ: `docs/architecture.md`
* API仕様: `docs/api-spec.md`
* Firebase構築手順: `docs/firebase_setup.md`
* 開発ロードマップ: `docs/roadmap.md`

---

## 👥 Team

* Project Lead / Backend / Frontend
  戸谷 (@2u8a)

* Edge Device / Module A
  関 (@hbki3)

---

## 🚀 Current Status

* GPS受信API完成
* APIキー認証実装済み
* Firestore保存確認済み
* GTFS-Realtime生成確認済み
* 1台構成PoC稼働可能

---

## 📊 Deliverable

本PoCの成果物は：

1. 技術実証結果
2. 通信品質評価
3. 更新頻度・遅延分析
4. 1台あたり費用試算レポート

行政向け導入判断材料の提示を最終目的とする。

```
