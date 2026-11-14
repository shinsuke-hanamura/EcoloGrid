# Ecologrid タスク一覧

**ファイル名の規則**: 数字で始まる plan のファイルは、`docs/tasks/<issue>_<連番>_<名前>.md`。`<連番>`は作業順の番号で、issue とは無関係(作業順の番号が、issue の番号と取り違えられないように、issue の接頭辞を付ける)。数字で始まらないファイル(`TODO.md`)には、接頭辞を付けない。`<issue>`は関連する GitHub issue の番号(`1_` = issue #1、EcoloGrid の初期コミット)。**issue #1 で実装されていないもの**(未着手・plan のみ・縮小)は、`x_`(issue 未定)。実装したら、`1_` などに改名する。対象: `x_05`(Unity)、`x_06`(永続化)、`x_08`〜`x_11`(ボクセル化)、`x_12`(縮小)、`x_20`(強化学習の段階)。

方針: サーバ権威型(Python/FastAPI)。詳細は `docs/spec/architecture.md`。

| # | plan | 内容 | 状態 |
|---|---|---|---|
| 01 | 1_01_design_update.md | 設計書の改訂 | **完了** |
| 02 | 1_02_server_core.md | Core(Entity〜World, Region, tick) | **完了**(pytest 81件) |
| 03 | 1_03_server_api.md | FastAPI | **完了**(テスト 81 件) |
| 04 | 1_04_cli_client.md | CLI クライアント | **完了**(テスト 81 件) |
| 05 | x_05_unity_client.md | Unity クライアント | 未着手 |
| 07 | 1_07_scheduled_actions_and_breeding.md | 定期アクション基盤(world / entity モード)+ 繁殖 | **完了**(要バランス調整) |
| 08 | x_08_voxel_stage1_volume_range.md | ボクセル化 段階 1: 容積・容量・進入ルール・範囲(2 次元のまま) | plan のみ |
| 09 | x_09_voxel_stage2_3d_world.md | 段階 2: 3 次元化(高さ 8、地形、水量(静的)、登る、API・CLI 大改修) | plan のみ |
| 10 | x_10_voxel_stage3_gravity_damage.md | 段階 3: 速度・重力・落下・エネルギー/ダメージ統一 | plan のみ |
| 11 | x_11_voxel_stage4_water_flow.md | 段階 4: 水の流れ(水平・下方向・押しのけ) | plan のみ |
| 14 | 1_14_animal_states_sleep_arousal.md | 動物の状態(睡眠・覚醒度・食事可能)と個体の能力値(筋力・精神力・体力)。14a で NPC の自動捕食を「空腹かつ食事可能(意識あり・体が動く・安全)」に限る | plan のみ |
| 15 | 1_15_action_plan_cache.md | 周期アクションの解決のキャッシュ(アクション計画)+ キャッシュをクリアする管理 API | **完了**(2026-11-07) |
| 16 | 1_16_perception_percept_map.md | 個体ごとの知覚の範囲(PC を含む。覚醒度をかけた有効な範囲)・知覚した地図(`PerceptMap`)・NPC の判断の地図化・経路探索(BFS) | **実装済み**(2026-11-10。結果は各 plan の末尾) |
| 17 | 1_17_memory_system.md | 記憶システム(経験ログ・長期記憶・入眠時の整理と忘却・想起 A/B・`memorise_all_steps`) | **実装済み**(2026-11-10。結果は各 plan の末尾) |
| 18 | 1_18_diet_preference_absorption.md | 食べ物の嗜好と吸収性(個体ごと・種ごと) | **実装済み**(2026-11-10。結果は各 plan の末尾) |
| 19 | 1_19_player_view_memory_api.md | PC の知覚の範囲だけを返す API(view)・記憶/地図/助言の API・CLI の対応 | **実装済み**(2026-11-10。結果は各 plan の末尾) |
| 20 | x_20_rl_gate_stages.md | 強化学習の段階とゲート(測って判断) | plan のみ(ゲート 0 の後) |
| 12 | x_12_realtime_engine_spike.md | 処理の軽量化の追加策(縮小。基本部分は plan 13 で実装済み) | 縮小 |
| 13 | 1_13_game_time_and_tick.md | tick 50 ms・時間の設定を tick 数に統一・移動速度・パラメータの tick 化・惑星と時計・植物の遅延評価・GC の制御 | **完了**(テスト 108 件。生態系のバランス崩れが判明: TODO G-2) |
| 06 | x_06_phase2_persistence.md | Phase 2 永続化(Valkey) | 後回し |

Docker 環境: `docker/README.md`(`make test` / `make up` / `make cli`)。
遊び方(CLI): `docs/cli-how-to-play.md`
ヘッドレス実行: `docker-compose run --rm dev python -m ecologrid.tools.simulate --ticks 1500`

## 文書の構成
- `docs/spec/`: **確定した現在の仕様**。全体は `spec.md`、細かい仕様は `time-spec.md`・`animal-states-spec.md`・`animal-memory-spec.md`・`reproduction-parameters.md`・`api-spec.md`・`architecture.md`・`voxel-spec.md`。未実装の将来仕様は、各文書・項目の末尾に「案」として記載
- `docs/research/`: 検討・調査の記録(高速化、植物処理、tick の実現可能性、強化学習の応用)
- `docs/cli-how-to-play.md`: 遊び方 / `docker/README.md`: 運用
- `docs/tasks/`: 実装計画・進捗(本書)・TODO

## 既知の課題(未着手)
- **生態系のバランス調整**: 繁殖導入後、Rabbit が増えすぎて草を食べ尽くし激減する(詳細: spec.md §9、docs/tasks/1_07)

## 記憶・知覚・地図(18・16・17・19 は実装済み 2026-11-10)
仕様: `docs/spec/animal-memory-spec.md`(確定。内部名を含む)、`docs/research/animal-memory-rl-notes.md`(強化学習)。**推奨の実施順**: 18(小)→ 16a → 16b → 16c → 16d → 16e(渇きによる死の解消を確認)→ 17a〜17g(ゲート 0 の測定)→ 19 → 20(ゲート 0 の結果を見てから)。18・16・17・19 は実装済み。**残り: 標準の世界(128×128)での想起の評価(ゲート 0)→ 20(ゲート 0 の結果を見てから。実装は指示待ち)**。

## 仕様変更の検討中(ボクセル化)
仕様: `docs/spec/voxel-spec.md`(確定事項と案を区別して記載。未決は §9)。実装は指示待ち。

## TODO リスト
未着手・後回しの課題の一覧: **`docs/tasks/TODO.md`**(ゲーム仕様、ボクセル化、サーバ、クライアント、文書)。
