# 19 PC の知覚の範囲だけを返す API・記憶/地図/助言の API・クライアントの対応

**状態: 実装済み(2026-11-10)。結果は末尾「実装結果」** 仕様: `docs/spec/animal-memory-spec.md` §10・§11(作者確定 2026-11-09〜10)。TODO G-43・G-39。
**依存**: plan 16(知覚・地図・`decide`)、plan 17(記憶)。plan 16 の完了後に、19a から着手できる。

## 目的
1. **PC の UI には、知覚の範囲しか見せない**: サーバ側で、PC ごとに、**有効な範囲(自分のセル + 距離 r まで)の内側だけ**の地形・個体・イベントを返す。**r = 0 のときは、自分のセルだけ**。
2. PC が、**自分の記憶・知覚した地図・助言(NPC ならこう行動する)**を、API で取得できるようにする(UI は後ほど)。
3. NPC の記憶・地図は、管理 API だけ。

## 実装順(段階)
### 19a 知覚の範囲だけを返す view(大。**既存 API の変更を伴う**)
- **`GET /api/v1/players/{id}/view`**(本人のみ。Bearer): 有効な範囲 r の**現在(ライブ)**の、(2r+1)×(2r+1) セルの地形・個体(種・状態など、公開してよい項目)と、自分の状態(`stats`)、`tick`、`game_time`、`r`、中心。
- **イベント**: 範囲内のイベントだけを返す、プレイヤーごとの取得(`GET /api/v1/players/{id}/events?since&wait`。ロングポーリング)。共通の `world/events` は、PC のクライアントには使わせない(全体が見える)。
- **既存の `world/snapshot`・`world/events`**: 観戦・管理用に限定する(管理者のキー、または別のスコープ)。**互換性は考慮しない(既存の方針)**。CLI・テストの対応が要る。
- 完了条件: 範囲外の地形・個体が、レスポンスに含まれない。r が覚醒度で変わる。r = 0 で自分のセルだけ。睡眠中も、自分のセルは見える。範囲内のイベントだけが届く。他のプレイヤーのトークンでは取れない。
### 19b CLI の対応(中)
- `Session`・`WorldView`・`render` を、`view` と、範囲内のイベントだけで動くようにする。範囲外は描画しない(空白)。r = 0 のときは自分のセルだけ。状態バーに有効な範囲 r を表示。既存の CLI テストの更新。`bot` も `view` を使う。
- Player の標準の範囲は 20(41×41 セル)。CLI の画面(約 40×19 セル)より大きいので、画面に収まる範囲だけ描画し、全体は `view` に入っている。
### 19c 記憶・地図の API(中)
- **`GET /api/v1/players/{id}/memory`**(本人のみ): `kind=log|long|all`、`action`・`outcome`・`subject`、`near=x,y&radius`、`since_tick`、`limit`・`offset`(上限あり)。レスポンスに `tick`。`action`・`outcome` は安定した文字列。
- **`GET /api/v1/players/{id}/map`**(本人のみ): 知覚した地図(地形の既知セル・資源の層)。将来の UI 用。ページネーション・領域指定。
- **管理 API**(`X-Admin-Key`): `GET /api/v1/admin/entities/{id}/memory`・`.../map`、記憶の件数・大きさの統計(plan 17f)。
- 公開用 `to_dict()`・snapshot・イベントに、記憶・地図を含めない(プライバシー)。共通のイベントに記憶のイベントを載せない。
### 19d 助言(advice)の API(中)
- `decide`(plan 16d)を、PC について**実行せずに**計算し、**助言**(提案する行動・対象・理由。判断の材料: 地図の要約、思い出した記憶〔想起 A・B〕、欲求のレベル)を返す。**`GET /api/v1/players/{id}/advice`**(要求のときに計算。毎 tick の負荷なし)。
- 完了条件: 助言が NPC の判断と同じ規則・同じ入力から計算される。PC の行動(コマンド)には影響しない。本人のみ。
### 19e テストと文書
- API のテスト(本人のみ・範囲・ページネーション・管理キー)。OpenAPI。`docs/cli-how-to-play.md`・`docker/README.md`・設計書の更新。

## 変更ファイル(見積もり)
`api/main.py`・`api/schemas.py`・`api/game.py`(プレイヤーごとの可視範囲のイベント)・`core/world.py`(可視範囲の抽出)・`core/region.py`・`core/animal.py`・`cli/ecologrid_cli/*`・テスト(server・cli)・文書。

## 完了条件(横断)
- PC が、範囲外の情報を、どの API からも取得できない(snapshot・events の限定を含む)。
- 既存のゲームプレイ(CLI)が、範囲の制約下で動く。API の負荷: `view` が tick の予算を食わない(範囲内のセルだけ、区画の索引を使う)。

## リスク
- **既存の API・CLI の互換性が壊れる**(snapshot・events を PC が使えなくなる)。Unity クライアント(plan 05)は未着手なので、影響は CLI・テストに限られる。
- 範囲が 41×41 と大きいときの `view` の大きさ・頻度(差分の返却を検討)。
- 観戦・デバッグ用の全体表示の手段(管理 API)。

## 実装結果(2026-11-10)
- **view**: `GET /api/v1/players/{id}/view`(`World.player_view`): 自分のセル + 距離 r(有効な範囲。チェビシェフ距離)の地形(rows)・個体(公開項目だけ。`Entity.to_public()`)・自分の全状態・`event_seq`・気温。範囲の外は返さない。r = 0 で自分のセルだけ。範囲は Region をまたぐ(PC の表示は世界全体から引く)。
- **イベント**: `GET /api/v1/players/{id}/events`(`World.visible_events`): 自分に関するものと、範囲内のものだけ。他の個体の `entity_spawned`・`seed_germinated` は公開項目に絞る。イベントに `pos`・`species` を追加(`entity_removed`・`entity_moved`・睡眠・休憩・繁殖など)。
- **世界全体の `/world/snapshot`・`/world/events` は、管理者(X-Admin-Key)だけ**に限定(互換性は考慮しない方針)。
- **記憶・地図・助言**: `GET .../memory`(`kind=log|long|all`・`action`・`outcome`・`subject`・`near`・`radius`・`since_tick`・`limit`(上限 500)・`offset`)、`GET .../map`(既知の地形・資源。範囲指定可)、`GET .../advice`(`decide` を実行せず計算。判断の材料・想起した記憶・欲求)。すべて本人のみ。死亡中は 409。
- **管理 API**: `GET /admin/entities/{id}/memory`・`.../map`・`/admin/memory-stats`(X-Admin-Key。未設定なら無効)。
- **CLI**: `ApiClient.view/events/memory/map/advice`、`WorldView.load_view`(窓だけ持つ。範囲外は描かない)、`Session` は snapshot を使わず view + events で追従し、自分が動いたら view を取り直す。状態バーに `Range r` を表示。範囲の外は空白、r = 0 は自分のセルだけ。`World:` の行は、`In view:`(範囲内の個体数)。
- テスト: `server/tests/test_player_api.py`(15 件)、`test_api.py`(既存の更新)、CLI(view・窓・session・範囲 0 の描画)。
