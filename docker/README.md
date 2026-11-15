# Docker の使い方

docker-compose 1.22 / Docker 20.10 前提(古いため bullseye ベース。理由は Dockerfile 冒頭)。

| コマンド | 内容 |
|---|---|
| `make build` | イメージをビルド |
| `make up` / `make down` / `make logs` | サーバの起動・停止・ログ(`http://localhost:8000/api/v1/healthz`、Swagger は `/docs`) |
| `make test` | dev コンテナで pytest(サーバ + CLI、サーバ未起動でも可) |
| `make cli NAME=alice` | CLI で対話プレイ(キー入力・実時間。TTY 必要) |
| `make cli-check` | CLI からサーバ疎通確認 |
| `make bot NAME=bot1 SECONDS_=60` | 自動プレイのボット(負荷試験) |
| `make shell` | dev コンテナで bash |

- `server`: 本番相当の常駐サーバ。ワールドはメモリ上のため `--workers 1` 固定。
- `dev`: ソースを `/work` に mount。依存だけ焼き込み、`PYTHONPATH` でサーバ/CLI を import。
  コード変更後は `make up` の再起動(`docker-compose restart server` は mount していないので `make build up`)。
- ポートは `ECOLOGRID_PORT` で変更可能。

## サーバ設定(環境変数、`docker-compose.yml` の `server.environment` か `-e` で指定)

`ECOLOGRID_SEED` / `ECOLOGRID_REGIONS_X` / `ECOLOGRID_REGIONS_Y` / `ECOLOGRID_REGION_SIZE`(1 Region の一辺のセル数、既定 32。世界の大きさ = REGION_SIZE × REGIONS_X(Y)。視界(最大 8)の 2 倍未満は警告) / `ECOLOGRID_TICK_INTERVAL_MS`(1 tick の実時間(ms)。既定 50、整数の 0〜1000。**0 = 待たずに最速**。初期値で、稼働中は、プレイヤーが CLI の設定画面(`c`)または `PUT /api/v1/world/tick-interval` で変更できる) /
`ECOLOGRID_MAX_PLAYERS`(100) / `ECOLOGRID_IDLE_TIMEOUT`(300秒) / `ECOLOGRID_JOIN_KEY`(任意) / `ECOLOGRID_AUTOTICK` / `ECOLOGRID_PLAYER_SLEEP`(`false` で Player の睡眠を無効に)

**管理 API(管理者権限)**: `ECOLOGRID_ADMIN_KEY` を設定したときだけ有効(未設定なら常に 403)。リクエストに `X-Admin-Key: <キー>` を付ける。プレイヤーのトークンでは使えない。
- `GET /api/v1/admin/action-cache`: 周期アクションの解決(アクション計画)のキャッシュの状態。
- `POST /api/v1/admin/action-cache/clear`: キャッシュを全 Region で捨てる(次の tick で作り直す。ゲームの状態は変わらない)。本文 `{"rebuild_wheels": true}` でタイマーホイールも作り直す。

```bash
curl -s -X POST -H "X-Admin-Key: $KEY" http://localhost:8000/api/v1/admin/action-cache/clear
```

## 動作確認(curl)

```
H=http://localhost:8000/api/v1
curl -s -XPOST $H/players -H 'content-type: application/json' -d '{"name":"alice"}'   # token と player_id を控える
curl -s $H/players/$PLAYER_ID/view -H "Authorization: Bearer $TOKEN"        # 知覚の範囲だけ(世界全体の snapshot は X-Admin-Key が必要)
```
API 仕様は `http://localhost:8000/docs`(Swagger)。


## PC 向け API(知覚の範囲だけ)と管理 API
- **PC(プレイヤー)**: `GET /api/v1/players/{id}/view`(自分のセル + 距離 r までの地形・個体)、`GET .../events`(自分と範囲内のイベント)、`GET .../memory`・`.../map`・`.../advice`(本人のみ。Bearer)。
- **世界全体**(`GET /api/v1/world/snapshot`・`/world/events`)は、**管理者(`X-Admin-Key`)だけ**。
- **管理 API**(`ECOLOGRID_ADMIN_KEY` を設定したときだけ有効): `GET /api/v1/admin/entities/{id}/memory`・`.../map`(NPC を含む個体の記憶・地図)、`GET /api/v1/admin/memory-stats`、`/admin/action-cache`。
- `simulate` のオプション: `--no-recall`(想起なし)・`--memorise-all-steps`(全ステップのログ)。実行の最後に、死因(thirst / hunger)と記憶の統計を表示する。
