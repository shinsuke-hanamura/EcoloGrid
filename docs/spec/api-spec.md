# Ecologrid: WebAPI の仕様(現行)

`/api/v1`。Swagger(OpenAPI)は `http://localhost:8000/docs`(正確なスキーマはこちらが正)。サーバ構成と通信の方針: `architecture.md`。環境変数と管理 API の運用: `docker/README.md`。実装: `server/ecologrid/api/`(plan: `docs/tasks/1_03`、`docs/tasks/1_19`)。

## 1. 認証

| 種類 | 付け方 | 対象 |
|---|---|---|
| なし | - | `GET /healthz`、`GET /config`、`POST /players`(`ECOLOGRID_JOIN_KEY` 設定時は `X-Join-Key`) |
| プレイヤー | `Authorization: Bearer <token>`(参加時に返る。サーバにはハッシュで保持) | 本人の `/players/{id}/...`(他人のものは 403) |
| 管理者 | `X-Admin-Key: <キー>`(`ECOLOGRID_ADMIN_KEY` 設定時のみ有効。未設定は常に 403) | `/world/snapshot`・`/world/events`・`/admin/...` |

## 2. エンドポイント

### 2.1 参加・状態・コマンド
| メソッド/パス | 内容 |
|---|---|
| `GET /healthz` | 生存確認(tick・人数・`tick_errors`) |
| `GET /config` | システム設定値(`world`・`tick`・`limits`・`species`・`command_types`・`directions`・`cell_kinds`)。参加前に取得でき、クライアントは起動時に 1 回キャッシュする。`world_id` が変わったらサーバの再起動とみなす |
| `POST /players` | 参加。本文 `{name}`。`player_id` と `token` を返す。**名前が使用中なら 409 `{code: name_taken}`**。満員は 503 |
| `GET /players/{id}` | 自分の状態(生死・位置・`name`・Hydration/Energy/Health など) |
| `PUT /players/{id}/name` | **名前の変更**。本文 `{name}`。使用中なら 409 `name_taken`。他のプレイヤーには `player_renamed` イベントで届く |
| `DELETE /players/{id}` | 退出 |
| `POST /players/{id}/commands` | `move`(`dir`: n/s/e/w)・`eat`・`drink`・`rest`・`wait`・`respawn`。`command_id` で冪等(重複は `duplicate`)。キュー満杯は 429、死亡中の `respawn` 以外は 409 |
| `GET /world/regions` | Region の一覧(プレイヤー認証) |

### 2.2 PC の知覚: view と events(知覚の範囲の内側だけ)
| メソッド/パス | 内容 |
|---|---|
| `GET /players/{id}/view` | 自分のセル + 距離 r(**有効な知覚の範囲**。Chebyshev 距離)までの、現在の地形(`rows`)・個体・`event_seq`。範囲の外は含まない。r = 0(眠っている間など)は自分のセルだけ。死亡中は 409 |
| `GET /players/{id}/events?since=&wait=` | 自分に関するイベントと、有効な範囲内のイベントだけ(ロングポーリング。他の個体の内部状態は含まない)。`since` は前回の `next_seq`(初回は view の `event_seq`)。410 `events_expired` / `world_reset` は view を取り直す |

- **公開する個体の項目**: `id`・`name`・`species`・`pos`・`state`・`player_id` だけ。記憶・地図・嗜好・吸収性などの内部状態は含まない
- 範囲外のイベント(他の場所の誕生・移動など)は届かない。クライアントは、自分が動いたら view を取り直す
- **UI に出すのは知覚の範囲だけ**(`animal-memory-spec.md` §10)

### 2.3 PC の記憶・地図・助言(本人のみ)
| メソッド/パス | 内容 |
|---|---|
| `GET /players/{id}/memory` | 短期記憶(経験ログ)と長期記憶(強さの降順)。`kind`(log/long/all)・`action`・`outcome`・`subject`・`near=x,y`+`radius`・`since_tick`・`limit`・`offset` で絞り込み・ページネーション |
| `GET /players/{id}/map` | 自分が知覚した地図(`PerceptMap`)。未知のセルは空白。`x,y,w,h` で範囲を指定(省略は既知のセルの外接の矩形) |
| `GET /players/{id}/advice` | 「NPC ならこう行動する」という助言(判断のみ。実行しない)と、判断の材料(地図の要約・想起した記憶・欲求)。仕様: `animal-memory-spec.md` §11 |

### 2.4 管理者専用
| メソッド/パス | 内容 |
|---|---|
| `GET /world/snapshot?region=x,y` | 世界全体の地形と全個体(観戦・管理用。PC には返さない) |
| `GET /world/events?since=&region=&wait=` | 世界全体のイベント |
| `GET /admin/entities/{id}/memory` / `.../map` | NPC を含む個体の記憶・地図 |
| `GET /admin/memory-stats` | 記憶の統計(件数・推定バイト数) |
| `GET /admin/action-cache` / `POST /admin/action-cache/clear` | 周期アクションの解決(アクション計画)のキャッシュの状態/破棄(`spec.md` §10) |

## 3. イベント
共通項目: `seq`・`tick`・`type`・`regions`。PC には、本人に関するものと範囲内のものだけが届く。主な種別: `entity_spawned`・`entity_removed`(`reason`: death/ate/left…)・`entity_moved`・`stats_changed`・`fell_asleep`・`woke_up`・`fainted`・`rest_started`/`rest_ended`・`player_died`・`player_renamed`・`command_rejected`・`reproduced` など。個体に関する項目には `pos`・`species` が付く。

## 4. 互換性
- 世界全体の `snapshot`・`events` は管理者専用になった(2026-11-10、plan 19)。PC は view/events を使う
- バージョンの接頭辞(`/api/v1`)の集約と、他の版への対応は、将来の課題(案: `docs/tasks/TODO.md`)

## 5. 案(未実装)
- 本格的なレート制限、HTTPS・本番認証(OIDC など)、SSE/WebSocket(同じイベント列の push)
- Region の分散時の `url`(Region directory。現状は自サーバ)
