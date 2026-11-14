# Ecologrid: サーバ権威型アーキテクチャ(案)

> **注意**: §3 の API の例は初期案で、現行の API は `api-spec.md`（PC は `/players/{id}/view`・`/events`、世界全体の `snapshot`・`events` は管理者専用）。
> spec.md の補足。Phase 1 から「ゲームエンジンは WebAPI サーバ(Python/FastAPI)に置く」方針とする。
> Unity / CLI / その他はすべて同じ API のクライアントとなる。

## 1. 決定事項

| 項目 | 決定 |
|---|---|
| サーバ言語 | Python + FastAPI |
| 進め方 | 最初からサーバ前提(ローカル直結モードは作らない) |
| 通信 | HTTP(REST)を基本とし、WebSocket 常時接続を必須にしない |
| 規模 | 1サーバ数人〜最大100人程度。将来サーバを増やしワールド分散 |
| 永続化 | Phase 1 はメモリのみ(再起動でリセット)。Phase 2 以降は KVS は **Valkey** を採用(§5) |

spec.md §1, §6 の「エンジン: Unity(C#)」「Assets/Scripts 配下にコア実装」は、
**ロジックはサーバ(Python)、Unity は表示・入力クライアント**に読み替える(要設計書改訂)。

## 2. 全体構成

```
Unity ──┐
CLI   ──┼── HTTP(REST) /api/v1/... ──▶ FastAPI ──▶ GameLoop(asyncio タスク, 固定tick)
他    ──┘   (任意) SSE / WebSocket                    └─ World ─ Region[] ─ Entity[]
```

- サーバが **唯一の権威**。クライアントは「意図(Command)」を送り、「結果(State/Event)」を受け取る。
- `World.tick(dt)` は固定 dt(例 0.2s=5Hz)で、リクエスト処理とは独立に進む。
- ゲームロジック(`core/`)は FastAPI に依存しない純 Python。API 層は薄いアダプタ。

## 3. 通信設計(WebSocket 常時接続を前提にしない)

コアは「**コマンド投入 + 差分取得(カーソル方式)**」の 3 種の HTTP のみで成立させる。

| 用途 | エンドポイント案 | 備考 |
|---|---|---|
| 参加 | `POST /players` | プレイヤー作成 → `player_id` と `token` 発行 |
| 操作 | `POST /players/{id}/commands` | `command_id`(クライアント生成 UUID)で冪等。次tickで処理 |
| 初期状態 | `GET /world/snapshot?region=...` | 地形 seed/チャンク + 範囲内 Entity 全量 + `event_seq` |
| 差分 | `GET /world/events?since={seq}&region=...&wait=20` | **ロングポーリング**。`wait` 秒まで待ち、無ければ空で返す |
| 状態 | `GET /players/{id}` | 自分の Hydration/Energy/Health 等 |

- **イベントログ**: サーバは直近 N 件(または T 秒分)のイベントをリング
  バッファで保持し、通し番号 `seq` を振る。クライアントは `since=seq` で続きを取る。
  `since` が古すぎる場合は `410 Gone` + 「snapshot を取り直せ」を返す。
- **WebSocket/SSE は後から追加できる最適化**:同じイベント列を push するだけ。
  プロトコルの意味は変えない。CLI・テスト・Bot はポーリングだけで動く。
- **コマンド**: `{command_id, type, args}`。`move(dir)` / `eat(target_id)` / `wait` 等。
  プレイヤー 1 人につき 1 tick 1 コマンド(キュー長上限あり)。入力検証はサーバ側。
- **認証**: Phase 1 はプレイヤー作成時に発行するランダム token(Bearer)。
  数人用途なので十分。将来 OIDC 等に差し替え可能なよう依存は 1 箇所に隔離。
- **バージョン**: 最初から `/api/v1` を付ける(後から版を付けると、既存のパスの移行が要るため)。

### 3.1 イベントとスナップショットの設計

- Event 種別例: `entity_spawned` / `entity_moved` / `entity_removed(reason)` /
  `stats_changed` / `seed_germinated` / `player_died`。
- 位置は Entity の `GridPosition`(整数)。補間はクライアント側。
- 配信範囲は **Region(チャンク)単位**で絞れるようにする(AOI の土台)。

## 4. 分散(将来)を見据えた Phase 1 の設計制約

「ワールドどうしを接続して分散」を、**空間分割(Region 分散)**で実現する前提で、
Phase 1 から次を守る。

1. **World は Region の集合**。1 Region = 例 32×32 セル。Phase 1 は全 Region を 1 プロセスが持つ。
2. **Entity は常に 1 つの Region に所属**。tick は Region 単位で独立に実行でき、
   Region 間の相互作用は「隣接 Region への **メッセージ**」(Entity の越境、種子の飛散、
   捕食判定の問い合わせ)に限定する。直接オブジェクト参照で他 Region を触らない。
3. **全 ID は グローバル一意**(Entity は GUID、Region は `world_id` + 座標)。
4. **決定論**: 乱数は Region ごとの seed 付き RNG。固定 dt。
5. **状態はシリアライズ可能**(pydantic/dataclass → JSON)。Entity 越境・永続化・
   スナップショットで同一表現を使う。
6. **API の宛先に `region` を含める**。将来「Region → サーバ」のルーティング
   (ゲートウェイ or クライアントが directory を引く)を足してもプロトコルが変わらない。

将来の分散形態(参考): 各 Region サーバが隣接サーバと内部 API(同じ REST か gRPC)で
越境 Entity を受け渡す。クライアントは Region directory(`GET /world/regions`)
で担当サーバ URL を取得。Phase 1 では directory は常に自サーバを返すだけ。

## 5. 永続化の提案(Phase 2 以降)

結論: **KVS が適している(推奨に変更)**。保存単位が「Region = キー、スナップショット = 値」の
単純な get/put で、検索やJOINが要らず、Region 分散とも相性が良いため。

| 保存対象 | KVS 上の表現 |
|---|---|
| Region 状態 | `region:{world}:{x}:{y}` → スナップショット blob(+ `version`) |
| イベントログ(WAL) | Valkey Streams(Redis 互換)の追記ログ(Region 単位)。スナップショット後に切り詰め |
| プレイヤー/token | `player:{id}` → JSON、`token:{hash}` → player_id |
| Region directory(分散時) | `region_owner:{world}:{x}:{y}` → サーバ URL(+ リース TTL) |

- **Valkey を採用**(Redis 互換の OSS、BSD ライセンス。Redis 用クライアント/Streams/Cluster がそのまま使える)。AOF(`appendfsync everysec`)で永続化し、
  サーバが増えれば同一 KVS を共有して Region directory とリース(排他)にも使える。
  数人〜100人規模なら単一インスタンスで十分。
- **注意点**: ①メモリ常駐なので Phase 3 の大きなメッシュ blob はサイズを監視する
  (大きくなれば blob だけ S3 互換ストアへ、KVS には参照を置く) ②ランキング/管理者向け検索など
  「問い合わせ」が要る機能が出たら、その部分だけ RDB を追加する ③アカウント(メール/OTP 等)を
  本格化するなら RDB のほうが扱いやすい。
- **代替**: 単一サーバだけで運用する間は組み込み KVS(LMDB/RocksDB、SQLite を KV として使用)
  でも足りる。ただし分散時に共有できないので、最初から Valkey で揃える方が移行が少ない。
- **抽象化**: Phase 1 から `RegionStore`(`load/save/list`)と `PlayerStore` を切り、
  実装は `InMemoryStore` のみ。Phase 2 で `ValkeyStore` を足すだけにする。
- 復旧手順: 起動時に Region ごと「スナップショット + 以降のログ再生」。
  リース切れの Region のみ他サーバが引き取れる(分散時)。

### 5.1 配置トポロジ: Region ごとにエンジンサーバ + DB サーバ?

**エンジンは Region 単位で分けられる設計にするが、DB を Region ごとに専用サーバにはしない。**
Region は「論理的な分割単位」であり、「サーバ台数の単位」ではない。

- 1 Region は 32×32 セルと小さく、数十〜数百 Entity。Region ごとに専用サーバを置くと、
  人のいない Region のためにサーバが遊び、運用対象が膨れる。
- 実行時の読み書きはエンジンのメモリ上で完結し、Valkey へはスナップショット(数十秒ごと)と
  ログ追記だけ。DB 負荷は小さく、Region ごとの DB 分離で得られる性能面の利点は薄い。

推奨する単位は **セル(Cell)= エンジンプロセス 1 つ + 担当 Region 群**:

| 段階 | 構成 |
|---|---|
| Phase 1 | エンジン 1 プロセス(全 Region)。ストアはメモリ |
| Phase 2 | エンジン 1 + Valkey 1(全 Region のスナップショット/ログ/プレイヤー) |
| 分散期 | エンジン N(各自が複数 Region を担当)。**共有 Valkey**(小規模なら 1 + レプリカ、増えれば Valkey Cluster) |
| 大規模・障害分離が必要な場合のみ | セルごとに Valkey を持つ(エンジン + Valkey を 1 組) |

- **Region の担当割り当て**は Valkey 上の directory + リース(TTL)で管理。エンジン停止時は
  リースが切れ、他エンジンがスナップショット + ログから引き継ぐ。**これには Region 群が共有ストアに
  いる方が簡単**(セルごとの DB だと、落ちたホストの DB に引き継ぎ元データが閉じ込められる)。
- **Valkey Cluster 対応**: キーに hash tag を使い、同一 Region のキーを同じスロットに載せる
  (`region:{w:x:y}:snap`, `region:{w:x:y}:log`)。Region 単位の原子的操作が保てる。
- `RegionStore` は「Region → ストア接続」の対応表を持てるようにしておく(Phase 2 は常に同一接続)。
  これで将来「セルごとの Valkey」へも設定変更だけで移行できる。
- directory/リース用の小さな共有 Valkey は、どの構成でも 1 つ必要。

## 6. クライアント

- **CLI**: Python の `httpx`(標準 `urllib` でも可)で上記 API を呼ぶ。ASCII 描画、
  ターン制入力。ポーリングだけで動作。サーバ実装とは別パッケージとして分離(コード共有なし)。
- **Unity**: `UnityWebRequest` で同 API を呼ぶ。C# クライアントは `/openapi.json` から
  生成可能(OpenAPI からの生成)。ロジックは持たず描画と補間のみ。

## 7. リスクと対応

| リスク | 対応 |
|---|---|
| ポーリングで操作が重い | ロングポーリング + 後から SSE/WS 追加。Phase 1 動物は低速グリッド移動なので許容見込み |
| Python の tick 性能(Entity 数千 × 5Hz) | Region 単位 tick、プロファイル後に必要なら numpy/マルチプロセス(Region 単位で分離しやすい) |
| ローカルの Python が 3.8 で FastAPI 等未導入 | venv + 新しい Python(3.11+ 推奨)を用意(plan 02 の前提作業) |
| 設計書(Unity 前提)との乖離 | plan 01 で設計書を改訂 |
