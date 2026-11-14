# 17 記憶システム(経験ログ・長期記憶・整理・忘却・想起)

**状態: 実装済み(2026-11-10)。結果は末尾「実装結果」** 仕様: `docs/spec/animal-memory-spec.md` §0・§3〜§9(作者確定 2026-11-10)、`docs/research/animal-memory-rl-notes.md`。TODO G-39。
**依存**: plan 16(知覚した地図・判断の地図化・経路探索)。plan 18(嗜好・吸収性)と並行可。plan 19(API)・20(RL のゲート)の前提。

## 目的
動物(人間を含む)に、**短期記憶(経験ログ)と長期記憶**を持たせ、**入眠時に整理(忘却 → 統合 → 空にする)**する。NPC は**想起**を行動の選択に使う。短期記憶は RL の経験バッファを兼ねる(整理の直前にリプレイのフック)。

## 確定した仕様(要約)
- 経験(`Experience`)= tick・action・outcome・subject・座標・**D**・reward・**強さ = (1 + \|reward\|) × 乱数(0〜1)**・state(参照)。短期記憶 = 経験のログ(上限なし。寝たら整理して空)。
- 長期記憶(`LongEntry`)= key(action, outcome, subject, d, 区画)ごとに、最初の座標・`count`・`reward_sum`・`strength`(合計)・first/last tick。**結果ごとに別の項目**(打ち消さない)。
- **整理(入眠・意識消失時のみ)**: (0) リプレイのフック → (1) 忘却(全長期記憶に `forgetting_factor` 0.9 をかけ、強さ < `forgetting_threshold` 0.5 を削除。地図の資源の層にも) → (2) ログを長期記憶に統合 → (3) ログを空に。
- **想起 A(目標指向)**: 区画の全結果から強さに比例して代表の結果を選び、分類(目標・失敗・忌避)で向かう・外す。区画どうしも強さに比例。κ = 0。判断の順序は「地図 → 想起 → 探索」。
- **想起 B(場所による)**: 区画に入ったとき、`p = 1 − exp(−強さ/5)`、上限 3 件、不応期 600 tick。
- 類似 = 丸めた座標が同じ(D: `drink`・`rest` = 2、他 = 4。記憶ごとに保存)。reward の表と分類は仕様書 §7.1。
- 個体のパラメータ(種の標準値を発生時にコピー。当面全種同じ): `recall_tau`・`recall_max`・`recall_refractory_ticks`・`recall_distance_exponent`・`forgetting_factor`・`forgetting_threshold`。
- ワールドの設定: **`memorise_all_steps`**(既定 false。true で、判断のたびの全ステップをログに追加。長期記憶には統合しない。状態は窓のコピー)。

## 実装順(段階)
### 17a データ構造と記録(中)
- 新規 `core/memory.py`: `Action`・`Outcome`・分類・D・reward の表(データ)、`Experience`・`LongEntry`・`Memory`(`log`・`long`・`d_values`・`place_index`・`long_index`)、`record()`(決定論的な乱数: seed・個体 ID・tick・action)。
- `Animal.memory` と、種のパラメータ(`SpeciesDef` → 個体にコピー)。
- 完了条件: 強さの分布((1 + \|r\|) × U(0,1)、決定論)、key と丸め(D を記憶ごとに保持)、ログへの追加が O(1)。
### 17b 記録の接続(中)
- `Animal.drink`(`drank`/`no_water`)・`eat_here`(`ate`/`no_prey`。`ate` の reward = 嗜好: plan 18)・`start_rest`(`rested`)・`fall_asleep`/`_faint`(`slept`)・誕生・出産(`born`/`gave_birth`)から `record`。Player のコマンドの失敗(`no_water`・`no_prey`)も記録。`hurt` は、落下・戦闘(`docs/tasks/x_10`・G-26)ができたときに接続(枠だけ用意)。
- 経験の `state`: 内部状態(量子化)+ 知覚の範囲の地図への参照(中心・r・`map_version`)。地図の更新の差分(`MapUpdate`)を、ログに追加(日中の再構成用)。
### 17c 整理・忘却(中)
- `organize()`: 忘却 → 統合 → 空にする、索引の再構築。`fall_asleep` と `_faint` から呼ぶ。入眠しなければ呼ばれない。リプレイのフック(`replay(log)`。当面は何もしない)。`PerceptMap` の資源の層に忘却を適用(plan 16 の `decay`)。
- 完了条件: 整理の順序(忘却が今日の分に及ばない)、強さ・件数・報酬の合計に同じ係数(平均報酬が不変)、閾値未満の削除、統合の加算、最初の座標を保つ、ログが空になる、入眠なしで整理されない、意識消失でも整理される。
### 17d 想起(大)
- A: `recall_goal(action, subject, 現在地)`(強さに比例する代表の結果 → 分類 → 区画の選択。決定論的な乱数)。`decide`(plan 16d)の順序に組み込む(地図 → 想起 → 探索)。
- B: `recall_place()`(区画に入ったとき。`d_values` ごとに `place_index` を引く。確率・上限・不応期)。結果 `recalled` を、判断の材料(`decide` の理由)・助言(plan 19)に出す。
- 完了条件: A の選択確率が強さに比例(統計テスト)、失敗・忌避が選ばれた区画は外れる、嗜好 0 の `ate`(強さが小さい)も、低い確率で選ばれる、B の確率が `1 − exp(−強さ/5)`(統計テスト)、不応期、上限 3、決定論。
### 17e `memorise_all_steps` と RL のフック(小)
- `WorldConfig.memorise_all_steps`(既定 false)。true のとき、判断のたびに、step のエントリ(行動・reward ほぼ 0・状態は窓のコピー〔自己中心の相対座標〕)をログに追加。整理のとき、リプレイのフックには渡すが、長期記憶には統合しない。
- 完了条件: オフ/オンでログの内容が変わる、長期記憶は同じ、状態の窓のサイズ((2R+1)²、有効でないセルは「未知」)。
### 17f シリアライズと監視(小)
- `memory.to_state()`/`load_state()`(コンパクトな配列。越境の往復で等しい)。**公開用 `to_dict()` に記憶・地図を含めない**(テスト)。管理用に、記憶の件数・大きさの統計(ログ・長期記憶・地図)。
### 17g 評価(ゲート 0 の測定)
- 2×2×16・複数 seed・20 日の `simulate` で、**記憶あり/なし**(想起 A をオフにできるフラグ)の、渇き・飢えによる死の割合・存続日数を比較。記憶のサイズ・整理の時間・1 tick あたりの時間を記録。結果を `docs/tasks/1_17` に追記し、RL の次の段階(plan 20)に進むかを判断。

## 変更ファイル(見積もり)
新規 `core/memory.py`、`core/animal.py`・`core/ai.py`・`core/species.py`・`core/config.py`(`memorise_all_steps`)・`core/region.py`(誕生のフック)・`core/reproduction.py`(`gave_birth`)・`api/`(plan 19)・`tools/simulate.py`(記憶の統計、想起オフのフラグ)・テスト。

## 完了条件(横断)
- 決定論(同じ seed → 同じ記憶・同じ行動)。性能: 記録・整理・想起が tick の予算内(1 tick の増加 +10% 以内を目標)。メモリ: 1 個体 数十 KB。
- 既存のテストが通る。記憶が NPC の行動に効く(渇きによる死が、記憶なしより減る)。

## リスク
- 想起の確率的な選択による行動のばらつき(決定論は保つが、結果の解釈が難しい)。
- 長期記憶の大きさ(忘却で抑える)。入眠しない個体のログの増加(安全弁なし。管理で監視)。
- 地図・視界の縮小(plan 16)と記憶の効果が重なる。切り分けのため、記憶のフラグで A/B する。

## 未決
- 動く個体(獲物)の地図への残し方(plan 16)。想起と地図の優先(確定: 地図 → 想起 → 探索)。報酬の表の数値の調整。

## 実装結果(2026-11-10)
- `core/memory.py`: `Action`/`Outcome`・分類・D・reward の表(`RULES`)、`Experience`・`MapUpdate`・`LongEntry`・`Memory`、`record`(強さ = (1 + \|reward\|) × 乱数。決定論)、`organize`(リプレイのフック → 忘却 → 統合 → ログを空に)、`recall_goal`(強さに比例する選択・分類・κ = 0)、`recall_place`(確率 `1 − exp(−強さ/τ)`・上限 3・不応期 600・エッジ判定。記憶ごとの D を使う)、`to_state`/`from_state`(JSON にできる)。
- 動物への組み込み: `Animal.remember`/`organize_memory`/`recall_goal`、記録は飲水・捕食(`ate` の reward = 嗜好)・休憩・睡眠・誕生・出産と失敗(`no_water`・`no_prey`)。**入眠(`fall_asleep`)と意識消失(`_faint`)で整理**。入眠しなければ整理されない。`ai.think` が場所による想起を行い、判断の材料に含める。判断の順序は「地図 → 想起 → 探索」。
- パラメータ(種の標準値 → 個体にコピー): `recall_tau` 5・`recall_max` 3・`recall_refractory_ticks` 600・`recall_distance_exponent` 0・`forgetting_factor` 0.9・`forgetting_threshold` 0.5。ワールドの設定: `memorise_all_steps`(既定 false。全ステップを経験ログに追加。状態は窓のコピー。長期記憶には統合しない)・`recall_enabled`(既定 true。記憶あり/なしの比較用)。
- `World.memory_stats()`、管理 API の統計(plan 19)。公開用 `to_dict()` に記憶・地図を含めない(テストで確認)。
- テスト: `server/tests/test_memory.py`(29 件: 強さの分布・D の保持・整理の順序・忘却の数学・想起 A の比例性(統計)・失敗が代表のときの除外・嗜好 0 の想起・想起 B の確率(統計)・不応期・件数の上限・記憶ごとの D・`memorise_all_steps`・シリアライズと越境・決定論)。
- **17g 評価(ゲート 0 の測定)**: 2×2×16・3 seed・20 日・`--scale-animals`(Rabbit 2・Fox 1)で、想起あり/なし。**渇きによる死は、どちらも 0**(plan 16 の経路探索の効果)。想起の有無で、結果はほぼ同じ(小さな世界は、地図が約 1,023 セル = 世界の全部を既知にするため、想起が使われる場面がほとんどない)。**想起の効果の評価は、標準の世界(128×128)で行う**(次の課題)。記憶の大きさは小さい(長期記憶 69 件・約 8 KB / 個体、整理 25 回)。
- ゲート 0 の判断(作者): 想起 A の効果が、標準の世界で確認できるまで、plan 20 の次の段階には進まない。
