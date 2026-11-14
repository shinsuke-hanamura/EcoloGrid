# 15 周期アクションの解決のキャッシュ(高速化 S2 の残り)

**状態: 実装済み(2026-11-07)。キャッシュをクリアする管理 API(X-Admin-Key)を追加。結果は末尾「実装結果」** 背景: `docs/research/headless-speedup-proposal.md` §5(S2)、TODO G-36。前段(植物・種子・卵を毎 tick のループから外し、発芽・孵化をタイマー化)は実装済みで、150,000 tick の個体数の推移は変更前と完全に一致、1 tick あたり 1.25 ms → 0.84 ms。

## 現状(コードから)
`Region.tick` は毎 tick `run_due_actions(region, store, tick_no)` を呼ぶ。その中で、**毎 tick・毎 Region**:
1. `scopes_for_region(rid)` が、リストと `f"region:{x}:{y}"` の文字列を作る(`["global", "region:x:y"]`)。
2. スコープごとに `store.due(scope, tick_no)`(全アクションを `is_due` で走査するリスト内包)と `store.entity_actions(scope)`(全アクションを filter するリスト内包)が、**新しいリストを作る**。
3. 個体単位アクション(現在 11 件: breed_check×2・gestation_step×2・plant_cycle×2・germinate×2・sleep_check×3)ごとに `Region.run_entity_action` を呼ぶ(辞書引き + `tick % n` + スロット判定)。ほとんどの tick で、実行する個体はいない(空振り)。
- 16 Region で、**約 176 回の `run_entity_action` と約 64 個の小さなリストの生成が、毎 tick**。
- 個体の追加(`Region.add`。種子・子・植物の散布のたび)も、`_entity_actions_for(species)` で、スコープの走査とリスト内包をやり直している。`find_action` も同様(`start_timer` から)。
- 前回のプロファイルでは、周期アクションの処理(`run_due_actions` の累計)が 1 tick の約 2 割。

## 設計: Region ごとの「アクション計画」をキャッシュする
ストアの内容(アクションの一覧)が変わらない限り、解決の結果は毎 tick 同じ。**ストアに版番号を持たせ、Region が版番号の変化を検出して計画を作り直す**。

### 1. ストアに `version`(版番号)
- `ActionStore` の約束に、整数の `version`(`put`・`remove` のたびに +1)を加える。`InMemoryActionStore` で実装。
- アクション(`ScheduledAction`)は不変(`frozen=True`)なので、有効・無効の切り替えや間隔の変更は、`put` による置換で、必ず版番号が変わる。
- Valkey などの別の実装(Phase 2)では、版番号(または ETag)を持つ。**`version` を持たないストア**(テスト用の自作など)は、`getattr(store, "version", None)` が `None` のとき、**毎 tick 作り直す**(速くならないが、正しく動く)。

### 2. Region の計画(`ActionPlan`)
版番号が変わった tick の最初に作る。内容:
- **実行の順序を保った手順**: 現行と同じ順序 = スコープ順(global → region)で、各スコープの「world モードの有効なアクション → entity モードの有効なアクション」。
- **world モード**: `(interval, offset % interval, act)` のタプルのリスト。実行判定は `tick_no % interval == offset` をその場で行う(`is_due` の呼び出しをやめる)。
- **entity モード**: `(act, wheel)` のリスト。**`TimerWheel` への参照を計画が持つ**(毎 tick の辞書引きと間隔の比較をやめる)。ホイールは、計画の作成時に `_wheel_for(act)` で作る(従来は初回の個体の追加で作っていた。同じ結果)。間隔が変わった場合は、置換で版番号が変わり、計画の作り直しでホイールも作り直される(既存の `test_changing_interval_rebuilds_wheel` と同じ挙動)。
- **ハンドラ**: 関数への参照を、**最初に実行されるときに解決してキャッシュ**する(未登録のハンドラは、従来どおり「実行されるとき」に `KeyError`。`test_unknown_handler_raises` を保つ)。
- **種ごとの索引**: `species → [個体単位アクション]`(`Region.add` の `_entity_actions_for` が使う)と、`action_id → act`(`find_action` が使う)。

### 3. 毎 tick の実行(速い経路)
```
plan = self._plan()                       # 版番号が同じなら、そのまま返す(整数の比較 1 回)
for step in plan.steps:                   # 現行と同じ順序
    world: if tick % interval == offset: handler を呼ぶ
    entity: slots = wheel.slots
            if tick % wheel.n in slots:   # 空振りのほとんどは、ここで終わる(リスト生成なし)
                for eid in wheel.due(tick): 現行の run_entity_action の本体(死亡・anchor の確認、handler 呼び出し)
```
- リストの生成(`scopes_for_region`・`due`・`entity_actions`)と、`run_entity_action` の呼び出しが、空振りの tick からなくなる。
- 実行する個体がいるときの処理(`wheel.due()` のコピー、`anchor >= tick_no` のスキップ、死亡チェック)は、現行のまま。

### 4. 同じ間隔のホイールをまとめる(任意)
sleep_check×3(600)のように、同じ間隔のホイールが複数ある。`tick % n` の計算を、間隔ごとに 1 回にする。効果は小さいので、計測して必要なら追加する。

## 守ること(挙動を変えない)
- **実行の順序**(スコープ順、world → entity、アクションの登録順、ホイール内の挿入順)が従来と同じ。
- **実行時にアクションを追加・削除・置換・無効化**したとき、**次の tick から**反映される(既存のテストと同じ)。
- 既存の関数の互換: `run_due_actions(region, store, tick_no)`・`Region.run_entity_action(act, tick_no)`・`Region.find_action` は残す(テストが呼ぶ)。
- 結果が**完全に一致**すること(今回は、順序を変えないので、前段と違って、個体数の推移だけでなく、スナップショットも一致するはず)。

## 実装の手順
1. `ActionStore` に `version`、`InMemoryActionStore` に実装。
2. `Region` に `ActionPlan` の作成・キャッシュ・版番号の検出。`add`・`find_action`・`start_timer` が計画の索引を使う。
3. `run_due_actions` を、計画を使う経路に置き換える(版番号のないストアのときは毎回作り直す)。
4. テストの追加と、既存の `test_actions.py` の通過の確認。
5. 150,000 tick のベンチ(前段と同じ条件。`/tmp/spd/bench.py`)で、結果の一致と 1 tick あたりの時間を比較。
6. `docs/research/headless-speedup-proposal.md`・`core/actions.py` の docstring の更新。

## テスト
- **等価性**: キャッシュなしの旧い経路(テスト内に、旧い `run_due_actions` の実装を置く)と、新しい経路で、複数 Region の世界を数千 tick 回し、**スナップショットとイベント列が一致**(繁殖・妊娠・出産、種子散布・発芽、睡眠の判定を含む)。
- **版番号**: `put`・`remove` で `version` が増える。置換・無効化で計画が作り直される。
- **反映のタイミング**: 実行時の追加・削除・置換・間隔の変更・無効化が、次の tick から反映される。既存の個体への後付け(backfill)も従来どおり。
- **順序**: global → region、world → entity、登録順。
- **`version` のないストア**でも動く。未登録のハンドラは、実行されるときにだけ `KeyError`。
- 既存テスト全体の通過。ベンチ(150,000 tick)で、個体数の推移が前段と一致。

## 効果の見積もり(推定。実装後に再計測)
- 周期アクションの処理は、1 tick の約 2 割(0.15〜0.2 ms)。空振りの 176 回の呼び出しと 64 個のリスト生成が、`tick % n` と `in` の判定 176 回(1 回あたり 0.1 µs 台)に置き換わる。
- **0.84 ms → 約 0.65〜0.70 ms(約 1.2〜1.3 倍)**。累積では、変更前の 1.25 ms から約 1.8 倍。
- 世界が大きくなる(Region 数が増える)ほど、Region 数に比例して効く。

## リスク
- 版番号を持たないストア・別実装(Valkey)で、計画が古いまま使われる(`version` の約束と、テストで担保)。
- ホイールの作成のタイミングが、「個体の追加時」から「計画の作成時」に変わる(出力は同じ。等価性のテストで確認)。
- 計画がホイールの参照を持つので、ホイールを差し替える処理(間隔の変更)は、必ず計画の作り直しとセットにする。

## 関連する小さな最適化(別の段階。依存なし)
`World.tick` の `sorted(self.regions)`(tick・`_deliver`×2・`_collect_events` で 4 回)の使い回しと、メッセージ・イベントのない Region のスキップ(B1・B2)。ここでは行わない。


## 実装結果(2026-11-07)
- **実装**: `core/actions.py`(`InMemoryActionStore.version`、基準の `run_due_actions` は残す)、`core/region.py`(`ActionPlan`・`_plan`・`_build_plan`・`_run_actions`・`_run_wheel`・`clear_action_cache`・`cache_actions` フラグ)、`core/world.py`(`clear_action_cache`・`action_cache_status`)、`api/`(管理 API)。
- **管理 API(キャッシュのクリア。管理者権限)**:
  - 認証: サーバに環境変数 `ECOLOGRID_ADMIN_KEY` を設定したときだけ有効。リクエストの `X-Admin-Key` ヘッダが一致(`secrets.compare_digest`)しないと 403。**未設定なら常に 403(無効)**。プレイヤーの Bearer トークンでは通らない。
  - `GET /api/v1/admin/action-cache`: 状態(`store_version`・`regions`・`cached_regions`・`plan_builds`・`clears`)。
  - `POST /api/v1/admin/action-cache/clear`: 全 Region のアクション計画のキャッシュを捨てる(次の tick で作り直す)。本文 `{"rebuild_wheels": true}` を付けると、タイマーホイールも捨てて、各個体の `timers` から作り直す(不整合の復旧用)。ゲームの状態(個体・タイマーの基準 tick)は変わらない。実行はログに残る(`admin: action cache cleared`)。
- **テスト**: 新規 `test_action_plan.py`(版番号・計画の再利用と作り直し・クリアの前後で実行 tick が不変・ホイールの再構築・実行時の追加/無効化/削除・実行順序・版番号のないストア・キャッシュなしとの一致・**キャッシュあり/なしの世界のスナップショット・イベント数・個体数が 2,500 tick で完全に一致**)、`test_api.py` に管理 API(キー未設定・不一致・プレイヤートークン・クリアの流れ)。全体 188 件が通る。
