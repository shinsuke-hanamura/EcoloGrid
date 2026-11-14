# 04 CLI クライアント

**状態: 完了**(テスト: CLI 15 件、全体 81 件通過。実サーバで 2 ボット + 1 プレイヤーが同じ世界に入ることを確認。疑似端末で対話モードを確認)

遊び方の文書: `docs/cli-how-to-play.md`

## 使い方(`docker/README.md` も参照)
| コマンド | 内容 |
|---|---|
| `make cli-check` | サーバ疎通確認 |
| `make cli NAME=alice` | 対話プレイ(キー入力、実時間)。**TTY が必要** |
| `docker-compose run --rm dev python -m ecologrid_cli play --name alice --script "dddss.e"` | キー列を送って最終フレームを表示(非対話) |
| `make bot NAME=bot1 SECONDS_=60` | 自動プレイ(負荷試験) |

- サーバ URL: `--server` または環境変数 `ECOLOGRID_SERVER`(dev コンテナでは `http://server:8000`)。join key は `--join-key` / `ECOLOGRID_JOIN_KEY`
- Docker なしの CLI: `make venv`(初回のみ)→ `make play NAME=alice`。CLI の依存は `httpx` のみ(別マシンからは `cli/` + `pip install httpx` + `--server`)
- キー: `w/a/s/d`・矢印=移動、`e`=eat、`r`=drink、`.`/space=wait、`x`=respawn、`q`=終了
- 複数クライアントを並列起動する場合は、`docker-compose run` だと名前が衝突するため `docker run --network ecologrid_default -v $PWD:/work ecologrid-dev python -m ecologrid_cli ...` を使う

## 設計(`cli/ecologrid_cli/`)
- `api.py`: HTTP クライアント(サーバのコードは import しない)/ `view.py`: ローカル世界モデル(snapshot + events 適用)/
  `render.py`: ASCII 描画 / `session.py`: 参加・同期・復帰 / `play.py`: 対話 / `bot.py`: 自動プレイ
- **ターン制ではなく実時間**: サーバの世界は常に進み続けるため、plan の「ターン制」は成立しない。1 キー = 1 コマンドを送り、世界はその間も動く
- 起動時に `GET /config` を 1 回取得(tick_dt 等)→ 参加 → `snapshot` → `events` をロングポーリングで追従(別スレッド)
- 復帰: 410(`events_expired`/`world_reset`)は snapshot を取り直す。401(サーバ再起動/切断タイムアウトで退出済み)は再参加。429 は「Too fast」表示
- 描画: 1 セル = 2 文字幅。自分 `@` 他人 `P` Fox `f` Rabbit `r` Wheat `w` Grass `"` Seed `,` Egg `o` 水 `~` 岩 `#` 陸 `.`。HP/水分/Energy バー、世界の個体数、足元、ログ
- 表示は世界全域を受信(snapshot 全量 + 全イベント)。Region を絞った購読は未実装(必要になれば `region=` を使う)

## テストで見つかった不具合(サーバ側・修正済み)
クライアントのローカル世界がサーバ全 Entity に追従できるかを検証するテストで、**Region 境界を越えた Entity が tick の合間に
どの Region にも存在しない**不具合を検出。snapshot から欠け(「出現」イベントも来ない)、プレイヤー自身が越境する瞬間は
`GET /players/{id}` が `alive:false`、コマンドが 409 になっていた。`World._deliver` で移動先に即座に取り込むよう修正
(挙動は変わらず登録が次 tick 開始時 → 当 tick 末に早まるだけ)。

## 残課題
- Region を絞った購読(視界内のみ)による通信量削減
- 対話モードの自動テスト(現在は疑似端末での手動確認のみ)
- 描画の色・端末サイズ変更への追従は最低限
