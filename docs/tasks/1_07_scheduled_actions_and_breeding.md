# 07 定期アクション基盤と動物の繁殖

**状態: 完了**(Core 実装・テスト 66件通過。バランス調整は未了)

## 仕様(作者指示 2026-10-28)
- 繁殖: 最低年齢・最短間隔・最低 Energy を満たす個体が、一定 tick ごとに一定確率で子作り判定。
  成功で、一定 tick ごとに Energy を消費して胎児/卵が育つ(期間は種ごと)。
  タイプは胎生/卵生/分裂。分裂は即時 Energy 1/2 ずつで 2 体。
- 定期アクション: tick の絶対値を持ち `tick % N == 0` で実行。アクション一覧を DB 等で管理し、
  キー(エリア等)で分割してスケールできること。

## 実装
- `core/actions.py`: ScheduledAction / ActionStore(InMemory) / ハンドラ登録 / スコープ
- `core/reproduction.py`, `core/egg.py`, `species.py`(ReproductionDef)
- Region.tick に組み込み。tick_no は World が毎 tick 渡す
- テスト: `tests/test_actions.py`, `tests/test_reproduction.py`

## 残課題
- パラメータ調整(Rabbit の増減が激しい。繁殖確率・妊娠コスト・草の再生、Fox の捕食圧)
- Valkey 版 ActionStore(plan 06)。API 経由でのアクション参照/無効化(plan 03 以降、管理者権限)
- 子の Region 越境配置、Egg の捕食・踏みつけ、Player の繁殖は未対応
- 代謝・種子散布の定期アクション化(任意)

## 改訂(2026-10-28): 個体ごとの実行タイミング
世界の tick の倍数で全個体が同時に動く方式の欠点(実行タイミングの集中、妊娠成功 tick と成長 tick のずれ、
期間の不正確さ)を修正。

- アクションに 2 モードを導入: `world`(`tick_no % N == offset`)/ `entity`(個体ごとの anchor から N tick ごと)
- entity モードは `anchor % N` をスロットとするタイマーホイールで、走査なしに実行対象を引く
- 個体は `timers`(action_id → anchor)を持ち、Region 越境でも引き継ぐ。`Region.start_timer/stop_timer`
- 繁殖を entity モードに移行(`breed_check`=誕生 tick 基準、`gestation_step`=妊娠成功 tick 基準・正確な期間)
- 初期個体の誕生 tick を散らして位相を分散
- 詳細: spec.md §10。テスト: `tests/test_actions.py`(個体ごとの位相・越境・実行時追加・間隔変更など)
- 計測(4×4 Region、3000 tick): 1 tick 平均 22ms / p99 49ms / 最大 105ms(修正前との比較は未計測)
