# 13 tick 50 ms・時間の設定を tick 数に統一・移動速度・パラメータ換算(段階 1 より先に行うことを推奨)

**状態: 実装済み(2026-11-04)。** テスト 108 件通過(サーバ + CLI)。仕様: `docs/spec/time-spec.md`、`docs/spec/voxel-spec.md` §5

## 実装結果(要点)
- **時間の tick 化**: `core/planets.py`(earth: 86,400 / 21,600 / 0.0245。**2026-11-06 の指示で、`start_offset_ticks` を 0 に変更する(仕様のみ変更済み、コード・テストは未変更。TODO G-34)**)、`core/clock.py`(暦・時計・`Tick N` 表記)、`WorldConfig.tick_interval_ms`・`region_planets`・`validate_config`(不正値は拒否、推奨外は警告)。`Entity.tick(region)` から `dt` を廃止。種のパラメータはすべて tick 数(`species.py`、現行 × 648)。Player は水分・Energy 100 で開始。
- **移動**: `move_interval_ticks`(Rabbit 20 / Fox 12 / Player 5)。`next_move_tick` で「次に動ける tick」を管理。`entity_moved` に `duration_ticks`。AI は `move_interval_ticks` ごとにだけ考える(初期個体は位相を散らす)。
- **植物の遅延評価**: 毎 tick の更新をやめ、水分・Energy・Health は読み取り時と `plant_cycle`(誕生 tick を基準に散布間隔ごと)に経過 tick 分を反映。種子散布は `plant_cycle`(entity モードの定期アクション)。死亡の判定・除去は `plant_cycle`。
- **GC の制御**: tick ループの間だけ、生成済みオブジェクトを凍結し、世代 2 の自動収集を実質止めて、100,000 tick ごとに手動で実行(`GameServer.run`)。
- **API**: `/config` に `tick.tick_interval_ms`・`time_units`・`planets`・`species`(`move_interval_ticks`)。`/world/regions` に `planet`。`/healthz`・プレイヤー状態・events に絶対 `tick` と `game_time`(`day`・`hour`・`minute`・`second`・`text`)。環境変数 `ECOLOGRID_TICK_INTERVAL_MS`(既定 50)。
- **CLI**: ヘッダを `Day 1 06:04:33 (earth)` に。`--time-format clock|tick` と `t` キー(実行中の切り替え)。ボットは 1 歩にかかる時間ごとにだけコマンドを送る。
- **文書**: spec.md §4・§8・§9・§10、`docs/cli-how-to-play.md`、`docker/README.md` を更新。

## 計測結果(1 tick の処理時間、予算 = 50 ms)
| 世界 | 個体数 | 平均 | p99 | 予算比 |
|---|---|---|---|---|
| 通常(初期配置) | 約 1,000 | 1.0 ms | 1.2 ms | 2% |
| 植物が多い(Grass 約 3,100) | 約 3,800 | 1.7 ms | 1.9 ms | 3% |
| 植物 + 動物が多い | 約 3,200 | 2.5 ms | 2.9 ms | 5% |
(植物の遅延評価の前は、植物が多い世界で 20.6 ms(41%)、通常で 5.7 ms(11%)。)

## 設計どおりでない点・注意
- **種子散布の量子化**: 以前は「間隔経過後、Energy が足りた tick」に散布していたが、今は周期ごとに 1 回だけ試す(Energy 不足なら次の周期)。散布の頻度は平均でやや下がりうる。
- **設定ファイルによる tick 値の上書き**は設けていない(計画どおり。種・惑星のデータ(コード)が既定)。
- **分散時の絶対 tick の同期**は未実装(単一サーバでは `World.tick_no` が絶対 tick。TODO S-8)。
- **生態系のバランス**: 下記「新たに分かった課題」を参照。

## 新たに分かった課題(要判断)
tick 数の値に移行した世界で 4 日分(345,600 tick)をシミュレーションすると、**ゲーム内の 1 日たたないうちに草と小麦がほぼ枯渇し、動物も絶滅する**。

原因の仮説: 動きは体感で決めた tick(Rabbit は 20 tick に 1 歩)なのに、生態(植物の再生 9,720 tick など)は 27 倍遅くなったので、**動物が触れた植物を、空腹でなくても自動で食べる**挙動(spec.md §4.4)により、植物が再生より遥かに速く食べ尽くされる。

| 経過 | 現行(触れたら食べる) Grass / Wheat / Rabbit / Fox | 検証(空腹のときだけ食べる) Grass / Wheat / Rabbit / Fox |
|---|---|---|
| 開始 | 794 / 196 / 64 / 15 | 794 / 196 / 64 / 15 |
| 12 時間 | 202 / 13 / 83 / 17 | 1,615 / 170 / 161 / 19 |
| 1 日 | 106 / 3 / 33 / 27 | 2,415 / 140 / 162 / 26 |
| 2 日 | 76 / 0 / 0 / 12 | 2,348 / 72 / 243 / 30 |
| 3 日 | 311 / 0 / 0 / 1 | 1,032 / 7 / 171 / 45 |
| 3 日 12 時間 | 470 / 0 / 0 / 0 | 550 / 0 / 90 / 40 |

- 現行: Wheat は 2 日で、Rabbit は 2 日で、Fox は 3 日 12 時間で絶滅する。
- 検証(NPC は Energy が `hunger_threshold`(60)未満のときだけ自動で捕食する。本体のコードは変更していない): 3 日間は植物・動物とも維持されるが、**増減の振れは残る**(Grass は 1.5 日の 2,714 から 3 日 12 時間の 550 へ減少、Wheat は 0 に、Rabbit は 243 から 90 に減少)。
- 結論: 「空腹のときだけ食べる」で大きく改善するが、それだけでは安定しない。生態系のバランス調整(TODO G-2)が必要。NPC の自動捕食の変更は仕様(spec.md §4.4)の変更なので、作者の判断待ち。
