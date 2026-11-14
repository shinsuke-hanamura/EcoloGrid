# 18 食べ物の嗜好と吸収性(eat の報酬・Energy・毒性の吸収)

**状態: 実装済み(2026-11-10)。結果は末尾「実装結果」** 仕様: `docs/spec/animal-memory-spec.md` §9(作者確定 2026-11-09〜10)。TODO G-42。**依存**: なし(plan 17 の `ate` の reward に使う)。小さな plan。

## 目的
現行の「種ごとの `energy_absorption_rate`」を、**食べ物ごとの吸収性 `absorption`(0〜1)**に置き換え、**嗜好 `preference`** を加える。**個体ごと・種ごとに持ち、種の標準値を発生時に個体へコピー**する(当面は個体差なし。将来は遺伝子)。

## 仕様(要約)
- **`eat_reward` は、種・個体に持たない**(内部で計算)。**`ate` の reward = `preference[食べ物]`**。標準の嗜好は**当面 0**。
- **吸収性**は行動の選択に関係しない。**得る Energy = 獲物の Energy × `absorption[食べ物]`**。**(将来)毒性の吸収 = 獲物の毒性 × `absorption`**。
- **吸収性の標準値は現行を維持**: Rabbit 0.5・Fox 0.6・Player 0.6(すべての食べ物に同じ値)。
- 食べ物 = 獲物の種(`prey`: Rabbit は Wheat・Grass、Fox は Rabbit・Wheat・Grass、Player は Fox・Rabbit・Wheat)。
- 嗜好・吸収性は内部状態(`to_state`)。公開用 `to_dict()` に含めない。`/config` の `species` に標準値。

## 実装順
1. `SpeciesDef`: `energy_absorption_rate` を、`absorption`(食べ物の種 → 値。既定値 + 上書き)に置き換える(`diet` の表)。`preference`(同)を追加(既定 0)。
2. `Animal`: `preference`・`absorption`(辞書)を発生時にコピー。`eat_here`: `gain = prey.energy × absorption[prey.species]`。`ate` の reward(plan 17 から使う)= `preference[prey.species]`。
3. `to_state`/`load_state`、`/config` の `species`、`validate_config`(0 ≤ absorption ≤ 1)。
4. 将来の毒性の枠(`toxicity` 属性。当面 0。効果なし)。

## 完了条件(テスト)
- 既存の捕食テストが、同じ値(吸収率 0.5/0.6/0.6)で通る(挙動が変わらない)。
- 吸収性 0.2 の食べ物で、得る Energy が 0.2 倍。吸収性 0 で Energy が増えない(獲物は消える)。
- 個体は、発生時に種の標準値をコピーし、種の標準値を後で変えても、既存の個体は変わらない。越境の往復で保たれる。
- 公開用 `to_dict()` に含まれない。

## リスク
低い(吸収性の値を維持するので、バランスは変わらない)。

## 実装結果(2026-11-10)
- `SpeciesDef.absorption`(食べ物に共通の既定値。Rabbit 0.5・Fox 0.6・Player 0.6)・`absorption_by_food`(食べ物ごとの上書き)・`preference`(既定 0)・`preference_by_food`。`energy_absorption_rate` は廃止。
- `Animal.preference`・`Animal.absorption`(食べ物の種 → 値)を、発生時に種の標準値からコピー(`to_state` で越境・永続化。公開用 `to_dict()` には含めない。種の標準値の後からの変更は、既存の個体に影響しない)。`eat_here`: 得る Energy = 獲物の Energy × `absorption[獲物]`。`ate` の reward = `preference[獲物]`(plan 17)。`toxicity` の枠(当面効果なし)。
- `Entity.to_state()`(内部状態を含む。Region 越境用)を新設し、`to_dict()`(公開用)と分けた。`/config` の `species` に `absorption`・`preference`。
- テスト: `server/tests/test_diet.py`(9 件)。
