# issue 6 状態(`state`)の dict 化と、体制(stance)・入眠のコントロール

**状態: 実装済み(2026-11-18)。結果は末尾「実装結果」** issue #6。TODO G-27(睡眠の拡張)のうち「自発的に眠る」ことと、G-46(体制の拡張)の土台を扱う。**呼び出し画面(Actions ページ)は、次の issue**。仕様: `docs/spec/animal-states-spec.md` §4・§5。

## 目的
1. **`state` を、名前 → 真偽の dict にする**(`p.state["asleep"] = True`・`p.state.get("awake")`)。`state != AWAKE` のような、排他の文字列への分岐を改め、「毒を受けていて、かつ眠っている」のような、**複数の状態の同時**を表せるようにする
2. 状態に、**体制(stance)**と**状態(condition)**の区別(`kind`)と、**体制・状態ごとの、できる action(`actions`)と、パラメータの係数(`factors`)**を、登録表で定義できるようにする
3. 最初の体制として、**入眠体制(`sleep_stance`)**を作る。`sleep` で入り、`cancel_sleep` で解除する。体制の間は、覚醒度が低く扱われ、**自然な入眠**がしやすくなる。移動・食事・飲水はできない
4. 入眠体制に入る・解除する action(`sleep`・`cancel_sleep`)に、**仮のホットキー**を割り当てる(呼び出し画面(Actions ページ)は、次の issue で作り、そのときに、ホットキーを外して、Actions ページに移す)

## 確定事項
**状態の dict**
1. `state` は、`StateDict`(`dict` を継承)。キーは状態の名前、値は真偽(書かれていない = 偽)。`p.state["poisoned"] = True`、`p.state.get("asleep")`
2. **互換性は保たない**。`p.state` は、もう文字列ではない。**コード・テスト・API・CLI の、`state` の使用を、すべて直す**
3. **数値のパラメータ(`hydration`・`energy`・`health`・`stamina`・`wakefulness`・`toxicity`)は、属性のまま**。dict にまとめない
4. **排他性は、実装ではなく、ルールで緩く与える**。ただし、**(1) 書き込む場所を絞る**。**(2) 排他のものどうしから導く**(`awake` は、`not (asleep or unconscious)` として、`get("awake")` が計算して返す)
5. **体制と状態の区別は、保存の形には持ち込まず、登録表の属性(`kind`)として持つ**。保存は、平らな 1 つの dict
6. **挙動が変わらないことの確認(ゴールデンの比較)は、行わない**。ゲームバランスの調整のときに、デバッグの必要があれば、その時に行う

**体制・入眠**
7. `sleep` で入眠体制になり、体制の係数が働く。**遷移は 1 回だけ**(連打は意味を持たない)
8. **入眠の判定は変えない**(自然な入眠のまま。`sleep` の瞬間の判定はしない)。**知覚の範囲の計算も変えない**(覚醒度が低く扱われれば、それに応じて狭まる)
9. **解除は `cancel_<action 名>`**(`cancel_sleep`)。**その体制でないときに取っても、何も起こらない**
10. **体制の覚醒度の低下は、体制の間だけ**。眠った時点で体制を解除し、係数の影響を外す。眠りは、本来の覚醒度から回復する
11. **体制ごとに、できる・できない action を定義できる**。入眠体制では、移動・食事・飲水はできない
12. **表示は、係数をかけた後の値**(入眠体制では、覚醒度のバーが下がる)
13. **係数・できる action は、`<状態名>_factors`・`<状態名>_actions` の名前**で持つ(例: `sleep_stance_factors = {"wakefulness": "x * 0.2"}`)
14. **呼び出し画面(Actions ページ)は、次の issue に分離する**。この issue では、`sleep`・`cancel_sleep` に、仮のホットキー(下記)を割り当てる。**API の `state` は、導いた状態(`awake`・`hungry`・`thirsty`)も含める**(本人には全部、他のプレイヤーには `public` な状態だけ)
15. 「動物は rest を必須とし、種ごとに必要な体制を定義できる」は、**今回は実装しない**(TODO G-46。`rest` は、これまでの `resting` のまま)

## 現状(コードから)
- `Animal.state` は、`awake`・`sleeping`・`unconscious` のうち、1 つだけを持つ文字列。使用は、コードに 34 か所(`animal.py` に 23、`memory.py` に 3、`api/main.py` に 3、`region.py` に 2、`player.py` に 2、`simulate.py` に 1)、テストに 67 か所。API の `state`・公開の `state`(他のプレイヤーの描画に使う。`World.PUBLIC_KEYS`)・CLI(`view.py` の `STATE_EVENTS` など)にも出る
- `in_combat`・`falling` は、`Animal` の旗(属性)で、`is_safe` が使う。テストに 14 か所。`resting` も旗(今回は触れない)
- 分岐は、**許可**(移動・食事・飲水・`can_eat`・休憩の開始・NPC の判断・知覚)と、**力学**(覚醒度の減少・回復、入眠・起床の判定、意識消失からの回復)に分かれる
- 入眠は、`Animal.sleep_check`(600 tick ごと)の確率判定だけ。覚醒していて安全なときに、`p = min(1, gain × 入眠圧^n)`。入眠圧は、覚醒度の割合 `x` の低さと睡眠周期で上がり、飢え・渇きが深いほど下がる
- 覚醒度の割合は、**入眠の判定の `x`** と、**有効な知覚の範囲**(`awake_ratio` → `effective_range`)で使われる
- 眠っている間は、`respawn` 以外のコマンドを拒否する。コマンドの受理の判定は、`Region` に直に書かれている
- `toxicity`(毒性)は、数値の属性だけで、効果は未実装
- サイドパネルは、Help・Config の 2 ページ(issue 4)。キーの表(`keymap.py`)は、種類 `action`(ホットキーなし)を、まだ使っていない

## 設計

### 状態の dict と登録表(6a)
- **保存**(個体ごと): `Animal.state`(`StateDict`)に、**保存する状態**だけを持つ。今回: `asleep`・`unconscious`・`sleep_stance`・`in_combat`・`falling`(`in_combat`・`falling` は、旗を dict に移す)。真のキーだけを保存(`to_state` / `load_state`)する
- **導く状態**: `get(名前)` が、登録表の導出の関数で計算する: `awake` = `not (asleep or unconscious)`、`hungry`(Energy < `hunger_threshold`)、`thirsty`(水分 < `thirst_threshold`)。**導く状態は、`state[...] = ...` で書けない**(エラー)
- **`get(名前, default=False)`**: 保存された値、なければ導いた値、どちらもなければ `default`。登録されていない名前を `get` したときは、**エラー**(つづりの間違いを防ぐ)
- **`StateDict.__setitem__`**: 登録された、保存する名前だけ受け付ける。**値が変わったときだけ**、(a) 状態のイベント(`state_entered`・`state_left`。項目 `state`・`kind`・`reason`)を出し、(b) 「できる action」のキャッシュを無効にする。値が同じなら、何もしない
- **登録表 `STATES`**(`core/state.py`): 名前 → `StateDef`(`kind` = `stance` / `condition`、`label`、`public`(他のプレイヤーに見せるか)、`actions`(できる・できない)、`factors`、導出の関数(導く状態のみ)、体制のとき `enter_action`・`cancel_action`(= `cancel_` + `enter_action`)・`ends_on`)。**名前は、`kind` によらず、一意**(登録時に検査)
- **排他のルール**(仕様に表で書く): `asleep` と `unconscious` は、同時に真にならない(`awake` は導く)。他は、独立
- **書き込む場所を絞る**: `state[...] = ...` と書いてよいのは、**遷移の関数だけ**(`fall_asleep`・`wake_up`・`_faint`・`_update_wakefulness` の意識消失 → 睡眠への移行・`enter_state`/`leave_state`・`load_state`・`bind`/生成時の初期化)。**これを守っているかを、テストで確認する**(`ecologrid/` のソースを構文解析し、許可された関数の外で、`state` に書き込んでいたら失敗)
- **検査の関数** `check_state_rules(個体)`: ルール違反(`asleep` と `unconscious` が同時に真、など)を返す。シミュレーションを回すテストから、毎 tick 呼ぶ(実行時には、強制しない)
- **名前の対応**: これまでの `sleeping` は `asleep`(状態の名前は、形容詞・過去分詞)。`awake` と `unconscious` は、そのまま

### 書き換えの対象(6a・6b)
- **許可の分岐**(`state != AWAKE` で、移動・食事・飲水・休憩・NPC の判断・知覚を止めているもの)→ `self.state.get("awake")`。のちに(6c)`action_allowed` に統一する
- **力学の分岐**(覚醒度の減少・回復、入眠・起床の判定、意識消失からの回復)→ `asleep`・`unconscious`・`awake` の `get` に置き換える(処理の内容は、変えない)
- `is_safe` → `not (state.get("in_combat") or state.get("falling"))`(旗の置き換え)
- **API・保存**: `to_dict` の `state` を、**状態の名前の一覧**にする(★1)。`to_state` は、保存する状態の dict。公開(`to_public`・`PUBLIC_KEYS`)は、`public` な状態だけ
- **テスト**: `p.state == "sleeping"` → `p.state.get("asleep")` など(67 か所)、`p.in_combat = True` → `p.state["in_combat"] = True` など(14 か所)
- 「挙動が変わらない」ことは、**書き換えた既存のテストが、すべて通ること**で確認する(ゴールデンの比較は、しない)

### 許可の一元化(6c)
- `Animal.action_allowed(action 名)`: 真の状態(保存・導出の両方でなく、**保存された真の状態**)の `actions` を合成する(1 つでも False なら、できない。書かれていなければ、できる)。結果は、状態が変わるまで、キャッシュする
- **コマンドの受理の判定を、1 つの関数にまとめる**: `check_command(個体, 種類, 引数)` → 拒否の理由または None。コマンドの処理(`Region`)が使う。次の issue の Actions ページ用の API も、同じ判定を使う予定
- `asleep`・`unconscious` の `actions` は、これまでの拒否と同じ(`respawn` 以外のコマンドの拒否。理由は、これまでの `asleep`・`unconscious`)

### 体制 `sleep_stance` と、係数・できる action(6d)
- **種のパラメータ**(`SpeciesDef`): `sleep_stance_factors`(`dict[パラメータ名, 式]`。既定 `{"wakefulness": "x * 0.2"}`)と、`sleep_stance_actions`(`dict[action 名, bool]`。既定 `{"move": False, "eat": False, "drink": False}`)。種の標準値を、個体の発生時に**個体の値としてコピー**する(将来は遺伝子)。他の状態の `factors`・`actions` は、登録表の既定(種で上書きしたいときに、同じ名前の項目を足す)
- **式の言語**: 数値・変数 `x`(適用前の値)・`+ - * / **`・括弧・`min`・`max` だけ(安全な、限られた構文。`eval` は使わない。起動時に検査・コンパイル)。結果は、パラメータの範囲に収める。体制の係数が効くパラメータは、**登録されたものだけ**(今回は `wakefulness`)。未登録の名前は、起動時にエラー
- **有効な覚醒度の割合** = `effective("wakefulness", 覚醒度の割合)`(真の状態の `factors` を、入った順に適用)。**`wakefulness` そのものは、書き換えない**。使う場所は、**入眠の判定の `x`**(`sleep_check`。式は変えない)と、**有効な知覚の範囲**(`awake_ratio` → `effective_range`。式は変えない)
- **体制の終わり**: `ends_on`(入眠体制: 眠った・意識消失・安全でない)の出来事で解除(`state_left`。`reason` つき)。眠った時点(`fall_asleep`)で、体制の影響が外れ、睡眠は本来の覚醒度から回復する。死亡・リスポーンで、体制は空になる。Region 越境(`to_state`)で、体制は保たれる
- **コマンド**: `sleep` = 覚醒していて、安全で、Player の睡眠が有効なときに受理し、入眠体制に入る(`state_entered`)。**すでに体制なら、何も起こらない**。拒否: `asleep`・`unconscious`・`unsafe`・`sleep_disabled`。`cancel_sleep` = 体制を解除する(`reason` = `cancelled`)。**体制でないときは、何も起こらない**。眠っている間は、`asleep` で拒否(自分で起きられない)。**できない action**(体制の `actions` が False): **止めている状態の名前を、拒否の理由にして**拒否する(例: 入眠体制の間の `move` は、理由 `sleep_stance`。眠っているときの `asleep`・`unconscious` と、同じ規則。★3)。`cancel_<体制の action>` は、その体制の間、常に受理

### API(6e)
- `entity`・`stats_changed` の `state`: 状態の名前の一覧(★1)。**`wakefulness_pct` は、有効な覚醒度の割合**(係数をかけた後)。本来の値は、`wakefulness`(`to_dict`)に残す
- `/config`: `command_types` に `sleep`・`cancel_sleep`、`species` に `sleep_stance_factors`・`sleep_stance_actions`
- イベント: `state_entered`・`state_left`(本人と範囲内のプレイヤーに届く。公開する項目は、`public` な状態の名前だけ)。これまでの、`fell_asleep`・`woke_up`・`fainted` は、そのまま出す(ログ・描画が使う)

### CLI(6e)
- **仮のホットキー**: キーの表(`keymap.py`)に、種類 `command` の行として、**`l`(sleep の l)= `sleep`**、**`L` = `cancel_sleep`** を足す。次の issue で、Actions ページに移し、ホットキーは外す。Help の「Keys」と最下行は、キーの表から生成されるので、自動で出る(最下行は、幅に収まる候補が選ばれる)
- **状態の行**: 入った体制の `label`(`Ready to sleep`)と、眠っている・意識消失を出す。**覚醒度のバーは、有効な値**(体制に入ると、下がる)。体制の開始・終了(眠った・解除・意識消失など)を、ログに出す(`You get ready to sleep.` など)
- 描画(`render.py`)・`view.py`・`play.py` の、`state`(文字列)の使用を、`state` の一覧(`"asleep" in state` など)に書き換える

## 実装順(段階)
- **6a `StateDict`・登録表・コードの書き換え**: `core/state.py`(`StateDef`・`STATES`・`StateDict`・導出の関数・`check_state_rules`)、`Animal` ほかの `state` の使用の書き換え(`in_combat`・`falling` を含む)、`to_state` / `load_state`
- **6b テスト・API・CLI の書き換え**: テスト 67 + 14 か所、`to_dict`・公開・CLI の `state`、書き込む場所のテスト、`check_state_rules` のテスト。**ここで、既存のテスト(書き換え後)が、すべて通ること**を確認する
- **6c 許可の一元化**: `action_allowed`・`check_command`、`Region` のコマンド処理の書き換え
- **6d 体制 `sleep_stance`**: 登録、`sleep_stance_factors`・`sleep_stance_actions`、式の評価、有効な覚醒度、`sleep`・`cancel_sleep`、体制の終わり、イベント
- **6e API と CLI**: `state` の一覧(導いた状態を含む)と有効な `wakefulness_pct`、`/config`、仮のホットキー、状態の行、バー、ログ
- **6f 文書とテスト**: `animal-states-spec.md`(状態の dict・排他のルール・体制)、`api-spec.md`、`cli-how-to-play.md`、TODO(G-27・G-46)

## 変更ファイル(見積もり)
新規 `server/ecologrid/core/state.py`、`server/ecologrid/core/animal.py`・`player.py`・`region.py`・`memory.py`・`species.py`・`messages.py`・`config.py`・`world.py`(公開の項目)、`api/main.py`・`schemas.py`・`memory_views.py`、`tools/simulate.py`、`cli/ecologrid_cli/keymap.py`・`render.py`・`view.py`・`session.py`・`api.py`・`play.py`、サーバ・CLI のテストの広い範囲、文書。

## 完了条件(テスト)
**状態の dict**
- `p.state["asleep"] = True`・`p.state.get("asleep")`・`p.state.get("awake")`(導く)が動く。`awake` は、`asleep`・`unconscious` のどちらかが真なら偽。導く状態(`awake`・`hungry`・`thirsty`)に書くとエラー。登録されていない名前は、`get`・`set` ともエラー
- **「毒かつ睡眠」を、同時に表せる**(`poisoned` と `asleep` が、同時に真。互いに影響しない)
- **書き込む場所のテスト**: 許可された関数の外で、`state` に書き込むコードがない
- **ルールの検査**: シミュレーション(複数の seed。睡眠・意識消失を含む長さ)を回し、毎 tick `check_state_rules` が違反を返さない
- `to_state` / `load_state` で、保存する状態が保たれる(Region 越境を含む)。公開(他のプレイヤー)には、`public` な状態だけが出る
- **既存のテストが、(`state` の使用を書き換えた後)すべて通る**

**体制・入眠**
- **式の評価**: `x * 0.5`・`min(x, 0.3)` などが正しく評価され、範囲に収まる。危険な式は、起動時に拒否される。未登録のパラメータ名は、エラー
- **連打が意味を持たない**: `sleep` を何度取っても、`state_entered` は 1 回、有効な覚醒度の割合は、式を 1 回適用した値のまま
- **入眠の判定の式が変わらない**: 体制でないときの確率は、これまでと同じ(回帰)。体制のときは、同じ式を、有効な覚醒度の割合で評価した値
- **覚醒度そのものが書き換わらない**。**眠ったとき、体制が解除され、本来の覚醒度から回復する**(`state_left`。`reason` = `asleep`)。意識消失・安全でない・死亡・リスポーンでも、解除される。Region 越境で保たれる
- **知覚の範囲**: 体制の間、有効な範囲が、有効な覚醒度の割合で決まる(式 `x * 0.2` なら、体制のない場合の 1/5)。式は変わらない
- **体制は、時刻(睡眠周期)に応じて、眠りやすくする**(係数 0.2): 冴えている(覚醒度 1.0)Player は、体制を取らなければ、どの時刻でもほぼ眠らないが、体制に入ると、習慣の入眠時刻の近くでは眠りやすく(300 回の判定で 4 割以上)、反対の時刻ではほぼ眠れない(3 回以下)。判定は、決定的な乱数で再現できる。飢え・渇きが深いと、眠りにくい
- **できない action**: 体制の間、移動・食事・飲水は拒否され、`wait`・`rest`・`cancel_sleep` は受理される。`sleep_stance_actions` を変えると、できる・できないが変わる
- **`cancel_sleep`**: 解除する(`reason` = `cancelled`)。体制でないとき、何も起こらない(拒否も、イベントもない)。眠っている間は `asleep` で拒否

**API・CLI**
- `state` の一覧(**導いた状態を含む**。本人には全部、他のプレイヤーには `public` な状態だけ)と、有効な `wakefulness_pct` が出る。`/config` に `sleep`・`cancel_sleep` と、`sleep_stance_factors`・`sleep_stance_actions` が出る
- **仮のホットキー**: `l` で `sleep`、`L` で `cancel_sleep` が送られる(キーの表から作った `dispatch_key`)。Help の「Keys」と、最下行の詳しい候補に出る(キーの表との一致のテスト)
- 状態の行に `Ready to sleep` が出て、体制に入ると、バーが有効な値で下がる。体制の開始・終了が、ログに出る
- 端末の大きさのテスト(行数・幅)が通る(状態の行・最下行の変更後)
- 擬似端末での一連の操作(`l` → 状態の確認 → `L`)が通る
- 全体のテストが通る

## リスク
- **`state` の使用を、すべて書き換える(コード 34 か所、テスト 81 か所)**: 範囲が広く、見落としや、挙動の変化を起こしやすい。**対策**: 6a・6b を、体制の追加より先に完了し、既存のテストがすべて通ることを確認してから、6c 以降に進む。ゴールデンの比較は行わないので、書き換えの作業は、1 か所ずつ、**意味を変えず**に行う(`state != AWAKE` → `not state.get("awake")` のように、機械的に)
- **コマンドの判定の切り出し**(6c): `Region` の直書きの判定を `check_command` にまとめる変更は、既存の挙動を変えやすい。既存のコマンドのテストで、回帰を確認する
- **式を文字列で持つ**: 検査が要る。限られた構文だけにし、起動時に検査・コンパイルする
- **有効な値が、複数の場所に影響する**: 入眠の判定と知覚の範囲に使う。体制に入ると、知覚の範囲が、すぐ変わる(意図した仕様)
- **issue の規模**: 状態の dict 化、体制、入眠、API を含み、大きい(Actions ページは、次の issue に分離した)。段階(6a〜6f)ごとに、テストと擬似端末の確認を行う。**6b の時点で、いったん全体を通して確認する**
- **仮のホットキー**: 次の issue で外す。キーの表の行を、`command` から `action`(ホットキーなし)に変えるだけで移せるようにしておく
- **ゲームバランスへの影響**: ゴールデンの比較をしないので、書き換えによる挙動の変化は、バランス調整のときに、デバッグの必要があれば、見つける

## 範囲外
- **呼び出し画面(Actions ページ)と `GET /players/{id}/actions`**(次の issue)
- 動物は rest を必須とし、種ごとに必要な体制を定義すること、`rest` を体制にすること(G-46)
- 毒の効果(`toxicity` と `poisoned`)、`in_combat`・`falling` の効果(戦闘・落下の実装。G-26・G-41)、`hungry`・`thirsty` の警告の表示(G-19)
- 自分で起きること、昼寝・睡眠の長さの指定、寝床、NPC の自発的な睡眠、外からの刺激による起床(G-27 の残り)、簡略計算モード(`lite`)
- 入眠の判定の変更、遺伝子による体制の係数の個体差(個体の値としてコピーする仕組みまで)
- ゴールデンの比較(バランス調整のときに、必要なら行う)

## 確定した事項(確認事項の結果)
- 体制の名前は **`<action 名>_stance`**(入眠体制は `sleep_stance`。係数 `sleep_stance_factors`・できる action `sleep_stance_actions`)
- 仮のホットキーは **`l` = `sleep`、`L` = `cancel_sleep`**(次の issue で、Actions ページに移して外す)
- できない action の拒否の理由は、**止めている状態の名前**(入眠体制の間の `move` は `sleep_stance`。既存の `asleep`・`unconscious` と同じ規則。複数あるときは、登録表の順で最初のもの)
- API の `state` は、導いた状態を含む一覧(本人には全部、他のプレイヤーには公開の状態だけ)

## 実装結果(2026-11-18)
- **6a `StateDict`・登録表・コードの書き換え**: 新規 `core/state.py`(`StateDef`・`STATES`・`StateDict`・導出の関数・式の評価 `compile_expr`/`apply_factor`・検査 `validate_factors`/`validate_registry`・`check_state_rules`)。`Animal.state` は `StateDict`。保存する状態は、`asleep`・`unconscious`・`sleep_stance`・`in_combat`・`falling`・`poisoned`(`in_combat`・`falling` の旗は、dict に移した。`poisoned` は、登録だけで、効果は未実装)。導く状態は、`awake`・`hungry`・`thirsty`。書き込みは `Animal.set_state` に集約し、意識の遷移は `Animal._become`(排他のルールを守る唯一の場所。眠った・意識を失ったとき、`ends_on` の体制を終える)。`state.awake` は、`get("awake")` と同じ値を、状態が変わるたびに計算し直す、高速な経路(毎 tick の判定用)。`consciousness`・`set_consciousness` は、表示・テスト・デバッグ用
- **6b テスト・API・CLI の書き換え**: サーバのテスト(`state` の使用 67 か所、`in_combat`・`falling` の旗 14 か所)を、機械的に書き換え。`to_dict` の `state` は、真の状態の名前の一覧(導く状態を含む)、`to_public` は公開の状態だけ。`stats_changed`・`state_entered`・`state_left` と、CLI(`view.py`・`render.py`・`bot.py`)を一覧に対応。**既存のテスト(書き換え後)は、すべて通った**
- **6c 許可の一元化**: 新規 `core/commands.py` の `check_command`(止めている状態・`exhausted`・`bad_dir`・`unsafe`・`sleep_disabled`・`unknown_command`。副作用なし)。`Animal.blocking_state`・`action_allowed`(結果は、状態が変わるまでキャッシュ)。`Region` のコマンド処理は、`check_command` を使う。`try_step`・`eat_here`・`drink`・`Player.act` も、`action_allowed` を使う(入眠体制の前に積んだ移動が、体制の間に実行されない)。実行して初めて分かる理由(`no_prey`・`no_water`・`unsafe`(rest))は、実行側のまま
- **6d 入眠体制**: 登録表の `sleep_stance`(`kind` = `stance`、`enter_action` = `sleep`、`cancel_action` = `cancel_sleep`、`ends_on` = 眠った・意識消失・安全でない)。種のパラメータ `sleep_stance_factors`(既定 `{"wakefulness": "x * 0.2"}`)・`sleep_stance_actions`(既定 `{"move": False, "eat": False, "drink": False}`)を、`SpeciesDef` には**不変のタプル**で持ち、個体の発生時に、**個体の dict としてコピー**する。有効な覚醒度(`Animal.effective`・`awake_ratio`)を、入眠の判定の `x` と有効な知覚の範囲に使う(式は変えない)。`sleep`・`cancel_sleep`(`COMMAND_TYPES`)。起動時の検査(`config._validate_stances`)
- **6e API と CLI**: `/config` に `states`(登録表)と `species` の `sleep_stance_*`。`stats_changed` の `wakefulness_pct` は、係数をかけた後の値。可視のイベント(`World.visible_events`)は、公開でない状態の変化を、他のプレイヤーに届けない。CLI: 仮のホットキー `l`・`L`(キーの表)、状態の行(`Ready to sleep`・`Resting, Ready to sleep`・`Sleeping`・`Unconscious`)、ログ、拒否の表示(`Rejected: blocked by Ready to sleep (press L to cancel)`)
- **6f 文書とテスト**: `animal-states-spec.md` §4・§5.5、`api-spec.md`、`cli-how-to-play.md`、TODO(G-27 を実装済みに、G-47(毒の効果)を追加)。テスト: `server/tests/test_state.py`(新規 22 件: `StateDict` の意味、導く状態、登録表、**書き込む場所のテスト(ソースの構文解析)**、排他のルールの検査(シミュレーション)、式の安全性、入眠体制の連打・係数・知覚の範囲・入眠の判定への適用(`sleep_pressure` に渡す `x`)・眠ったときの解除と本来の覚醒度からの回復・意識消失と安全でないときの解除・`cancel_sleep`・できない action と理由・個体の dict・`check_command` の副作用なし・Region 越境・公開・API)、CLI のテスト(状態の一覧・状態のイベント・拒否の表示・仮のホットキー・サーバと CLI の一連の流れ)。**全体のテスト 363 件が通る**
- **擬似端末での確認**: `l` → 状態の行 `Ready to sleep` → `w`(拒否の表示)→ `L`(解除)→ `l` → `h`(Help の Keys に `l`・`L`)→ `q`。期待どおり
- **性能**: 標準の世界で、1 tick あたり約 0.88 ms → **約 0.94 ms(+7%)**(判定の高速な経路 `state.awake`・`StateDict.get` の保存された名前の経路で、初回の +15% から縮めた)
- **係数の変更**(2026-11-18): 入眠体制の既定の係数を、`x * 0.5` から **`x * 0.2`**(覚醒度の割合が 1/5)に変更した。1 回の自然な入眠の確率(Player。覚醒度 1.0 → 0.2 など)は、習慣の入眠時刻の近く(rhythm 1)で、覚醒度 1.0 でも 0.67、0.8 以下ならほぼ 1。反対の時刻(rhythm 0)では、1.0 で 1.2e-3、0.5 で 1.2e-2、0.3 で 2.9e-2。つまり、**「冴えていて眠れない」かどうかは、覚醒度よりも、時刻(睡眠周期)で決まる**ようになった
- **残る課題**: 呼び出し画面(Actions ページ)(次の issue)、毒の効果(G-47)、`rest` の体制化と必要な体制の定義(G-46)、`hungry`・`thirsty` の警告表示(G-19)、`state` の判定の、さらなる高速化(必要なら)
