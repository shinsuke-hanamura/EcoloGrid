# 03 WebAPI(FastAPI)

**状態: 完了**(API 統合テスト 15 件、全体 67 件通過。実サーバで curl による参加→移動→差分取得を確認)

## エンドポイント(`/api/v1`、Swagger は `/docs`)
| メソッド/パス | 認証 | 内容 |
|---|---|---|
| `GET /healthz` | 不要 | 生存確認(tick・人数・tick_errors) |
| `GET /config` | 不要 | **システム設定値**(下記)。クライアントは起動時に 1 回取得してキャッシュする |
| `POST /players` | 不要(`ECOLOGRID_JOIN_KEY` 設定時は `X-Join-Key`) | 参加。`player_id` と `token` を返す。満員は 503 |
| `GET /players/{id}` | Bearer(本人のみ) | 状態(alive / 位置 / Hydration・Energy・Health) |
| `DELETE /players/{id}` | Bearer(本人のみ) | 退出 |
| `POST /players/{id}/commands` | Bearer(本人のみ) | `move`/`eat`/`drink`/`wait`/`respawn`。`command_id` で冪等(重複は `duplicate`)。キュー満杯 429、死亡中の respawn 以外は 409 |
| `GET /world/regions` | Bearer | Region 一覧(directory。`url: null` = 自サーバ) |
| `GET /world/snapshot?region=x,y` | Bearer | 地形 + Entity 全量 + `event_seq`。region 省略で全域 |
| `GET /world/events?since=&region=&wait=` | Bearer | 差分(ロングポーリング、`wait` は最大 30 秒)。`410` = `events_expired` / `world_reset` → snapshot を取り直す |

### `GET /config` の内容
`api_version` / `world_id` / `join_key_required`(鍵そのものは返さない)/ `tick`(`tick_dt`・`tick_rate_hz`。ゲーム時間 = tick × tick_dt)/
`world`(`seed`・`width`・`height`・`region_size`・`regions_x/y`)/ `limits`(`max_players`・`name_max_length`・`command_queue_max`・
`command_dedup_size`・`max_poll_wait_sec`・`event_log_size`・`player_idle_timeout_sec`)/ `command_types` / `directions` / `cell_kinds`(terrain 文字の意味)。
- 認証不要(参加前に取得できる)。毎レスポンスには含めない。サーバ再起動で値が変わりうるため、`world_id` が変わった(410 `world_reset` を受けた等)ときに取り直す
- 種ごとのゲームパラメータ(移動速度など)は含めない(必要になれば別エンドポイント)

## 実装(`server/ecologrid/api/`)
- `main.py`(ルータ・認証依存・`create_app`)/ `game.py`(GameServer: tick ループ・トークン・退出)/ `schemas.py` / `settings.py`(環境変数 `ECOLOGRID_*`)
- **スレッド方針**: World に触るのはイベントループ上のみ(全ハンドラ `async def`、tick もループ内で同期実行)。コマンドはキュー投入のみで、次 tick に処理
- **tick ループ**: 固定間隔。遅れても追いつき実行はせず基準時刻を現在に合わせる(`behind_count`)。tick 内の例外はログして継続(`tick_errors`)
- **認証**: Bearer token(ランダム 32 byte、サーバにはハッシュで保持)。差し替えは `current_player` のみ
- **切断時の扱い(暫定)**: API に 300 秒触れないプレイヤーは退出(`ECOLOGRID_IDLE_TIMEOUT`)
- **世界のリセット検出**: 起動ごとに `world_id` が変わり、再起動後の古い `since` は 410 `world_reset`
- **最低限の濫用対策**: 最大人数(既定 100)・名前 1〜24 文字・コマンドキュー上限(8)・join key(任意)

## 残課題
- 本格的なレート制限(IP/トークン単位)、HTTPS・本番認証(OIDC 等)
- tick が重い場合のスレッド分離(現状は 1 tick ≒ 数十 ms でイベントループ内)
- SSE/WebSocket(同じイベント列を push するだけの最適化。必要になれば)
- 管理用 API(ActionStore の参照・無効化など。管理者権限が前提)

## 修正履歴
- 2026-10-28: Region 越境中の Entity が tick の合間に snapshot/status から欠ける不具合を修正(`World._deliver` で移動先に即座に取り込む)。CLI の整合性テストで発見(docs/tasks/1_04 参照)
