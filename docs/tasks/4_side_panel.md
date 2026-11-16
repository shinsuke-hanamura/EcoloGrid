# issue 4 サイドパネル(Side Panel)の整理

英語版: `4_side_panel_en.md`(内容が違うときは、この日本語版が正)

**状態: 実装済み(2026-11-15)。結果は末尾「実装結果」** issue #4。issue 2(進行速度の変更)で導入したサイドパネルの、構造・キー定義・表示規則の整理。後続の「入眠のコントロール」と「呼び出し画面(Actions。ホットキーを割り当てない action)」(別の issue)の土台にする。

## 目的
マップの横の領域を**サイドパネル(Side Panel)**とし、その中身を**ページ**(Help・Config、将来は Actions)として、**1 つの仕組みで扱える**ようにする。

1. キーを足すとき、複数か所を直す状態をなくす(キーの定義を 1 か所にする)
2. 端末の大きさ(高さ・幅)が違っても、**同じ操作・同じ規則**で使える(いまは、狭い端末で、Config だけ別の方式になる。低い端末では、Help のキーの一覧が見えない)
3. 後続の issue(Actions)が、新しいページ・キーを、表の 1 行で足せる

## 現状(コードから。issue 2 のマージ後)
| 項目 | 現状 |
|---|---|
| 状態 | `view.show_help`(Help)と、`view.panel_mode`・`panel_buf`・`panel_msg`(Config)の 2 系統 |
| 実装の場所 | Help の中身は `render.py`(`LEGEND`・`LEGEND_KEYS`・`legend_lines`)、Config は `panel.py`。描画(`render.py`)が、両方の分岐を知っている |
| キーの定義 | ホットキーは `play.py` の `KEYS`、`h`・`c`・`t`・`q` は `play.py` のループの中の `if`、Help の「Keys」は `LEGEND_KEYS`、最下行は `FOOTERS`(4 種類)。**同じキーが、4 か所に別々に書かれている** |
| 高さ | 地図の高さは `rows - 11`。Help は約 25 行で、24 行の端末では、13 行しか見えない(**「Keys」の節は、見えない**)。はみ出しの表示も、スクロールもない |
| 幅 | 幅 76 桁(`40 + PANEL_W`)以上でパネルを出す。未満では Help は出ず、`c` は、画面全体の入力(`config_screen`・`tick_screen`・`rename_screen`)に切り替わる(**操作が 2 種類**) |
| 見出し | パネルに、現在のページと、切り替えのキーを示す行がない |

## 設計

### ページの仕組み(4a)
- `panel.py`: パネルの状態 `PanelState`(`page`・ページの中の状態 `sub`・入力中の文字列 `buf`・メッセージ `msg`)を、`view.panel` の 1 つにまとめる。`view.show_help`・`panel_mode`・`panel_buf`・`panel_msg`・`toggle_help()` は廃止
- **ページの登録表**: `PAGES = {"help": …, "config": …}`(将来 `"actions"`)。各ページは、`title`・`body(view, width, color)`(本文の行)・`hint(view)`(最下行のヒント)・`handle_key(view, session, key)`・`captures_input(view)` を持つ。Help・Config の中身は `pages.py` に移す(`panel.py` は仕組みだけ)
- 描画(`render.py`)は、パネルの中身を知らず、`panel.compose`(見出し・本文・最下行を組み立てる)を呼ぶだけにする

### キーの表 `KEYMAP`(4b)
- 新規 `keymap.py`: `KeyDef(key・表示名・説明・種類・コマンドまたはページ)`。種類: `move`(wasd・矢印)/ `command`(1 キーで実行: e・r・z・.・x)/ `panel`(ページを開く: h・c)/ `toggle`(t)/ `quit`(q)/ `action`(ホットキーなし。Actions ページの一覧にだけ出る。**今回は、行を持たない**)
- 次を、すべてこの表から**生成**する: `play.py` のホットキー(`KEYS`)と、キーの振り分け、Help の「Keys」、最下行(`FOOTERS` の各種類。詳しいものから、幅に収まるものを選ぶ)
- キーを足すのは、表の 1 行だけにする

### 見出し・操作の規則(4c)
- **見出し行**(パネルの 1 行目): `[Help]  Config` のように、現在のページを強調し、他のページの名前(切り替えのキー `h`・`c`)を示す。ページの中のキー操作(Enter・Esc など)は、パネルの最下行にヒントとして出す
- **キー**: `h` = Help を開く/閉じる。`c` = Config を開く/閉じる。Esc = 1 つ戻る(入力中 → 項目の選択 → 閉じる)。Help が開いている間に `c` を押すと Config へ、Config の項目の選択で `h` を押すと Help へ切り替わる(入力中の `h`・`c`・`q` は、文字)。Config が開いている間は、キーをパネルが受け取る(現状どおり)
- **高さが足りないとき**(確定): 本文が収まらないときは、**`↑` `↓` でスクロール**する。最下行に `Up/Down:scroll` と、続きがある向き(`^`・`v`)を出す。**収まっているときの `↑` `↓` は、これまでどおり、移動に使う**(はみ出している間だけ、パネルがスクロールに使う)
- **幅が足りないとき**(確定): パネルを、**地図の上に重ねて、全面に出す**(Esc で戻る。開いている間、地図は隠れるが、ゲームは進む)。**Config の別方式(画面全体の入力)を廃止**し、操作を 1 方式にする。起動時の名前の重複だけは、画面を描く前なので、従来の入力(`rename_screen`)のまま

### 文書とテスト(4d)
- `cli-how-to-play.md`: サイドパネル(Help・Config・見出し・キー・狭い端末)を 1 節にまとめる。`spec.md` §17。TODO に C-7 を追加し、完了にする
- テスト(完了条件を参照)

## 実装順(段階)
4a ページの仕組み → 4b キーの表 → 4c 見出し・高さ・幅 → 4d 文書・テスト。各段階の最後に、既存のテストと、擬似端末での確認(下記)を行う。

## 変更ファイル(見積もり)
`cli/ecologrid_cli/panel.py`(仕組みに縮小)、新規 `pages.py`・`keymap.py`、`render.py`・`play.py`・`view.py`・`session.py`(必要な範囲)、CLI のテスト、文書。**サーバの変更は、なし。**

## 完了条件
- `show_help`・`panel_mode`・`panel_buf`・`panel_msg` が残っていない。Help・Config は、同じ仕組み(ページの登録表)で動く
- **キーの表と、各所の一致**(ずれたら失敗するテスト): 表の `command`・`panel`・`toggle`・`quit` のキーが、ホットキーの振り分け・Help の「Keys」・最下行(詳しい種類)のすべてに現れる
- **端末の大きさの組み合わせ**(幅 40〜240 × 高さ 16〜41 × ページ(閉じている・Help・Config の 3 状態。広い端末の横並びと、狭い端末の全面表示。スクロールの先頭と末尾)): 行数・幅が端末を超えない(issue 2 のテストを拡張)。低い端末(24 行)でも、`↑` `↓` でスクロールすると、Legend と Keys のすべての項目が見える。収まる高さでは、`↑` `↓` は、パネルが使わない(移動に使う)
- 狭い端末(幅 60 など)で、Help・Config が、全面表示で使え、Esc で戻る。Config の入力・名前の重複・tick の変更が、広い端末と同じ操作でできる
- 操作のテスト: `h` の切り替え(開く → 閉じる)、スクロール(端で止まる)、`c`・`h` の相互の切り替え、入力中の `h`・`c`・`q` が文字になる、Esc の戻り方、開いている間もゲームのキーが効く(Help)
- 擬似端末での一連の操作(広い・狭い端末)が通る(issue 2 で使った確認の手順を、狭い端末でも行う)
- 既存のテスト(設定画面・凡例・端末の大きさ)を、新しい仕組みに合わせて直し、全体が通る

## リスク
- **キー入力の変更が、広く影響する**(`play.py` のループと `panel.py`)。擬似端末での確認を、段階ごとに行う
- **端末の大きさの組み合わせが多い**: 表示の崩れは、テストで拾いにくい。大きさの行列のテストと、実機の確認で補う
- **狭い端末の動作の変更**: Config の別方式を廃止するので、狭い端末の操作が変わる(全面表示になる)。文書に書く
- **issue 2 の画面の配置の規則**(`odd_at_most`・`clip`・`RESERVED_ROWS`)と、全面表示の整合: 全面表示でも、同じ規則(行数・幅を超えない)を守る

## 範囲外
入眠のコントロール、Actions ページ、`GET /players/{id}/actions`(別の issue。この issue の後)、色の整理、マウス操作、パネルの位置(右・下)の切り替え。

## 確定事項
1. **高さ**: **↑↓ でスクロールする**(Help を 2 つの節に分ける案は、採らない)
2. **幅**: 地図の上に重ねて、全面に出す(推奨のとおり。Config の別方式を廃止)
3. **`h`・`c` の相互の切り替え**: 切り替えられる(推奨のとおり)

## 実装結果(2026-11-15)
- **4a ページの仕組み**: `panel.py`(仕組み: 登録表 `PAGES`・`open_page`・`close`・`toggle`・`scroll`・`handle_key`・`compose`)と、新規 `pages.py`(Help・Config の中身)に分離。状態は `view.panel`(`PanelState`: `page`・`sub`・`buf`・`msg`・`scroll`・`max_scroll`)の 1 つにまとめ、`view.show_help`・`panel_mode`・`panel_buf`・`panel_msg`・`toggle_help()` を廃止。描画(`render.py`)は、パネルの中身を知らず、`panel.compose` を呼ぶだけ。共通部品を `textutil.py`(`char_width`・`fit`・`tail`・`clip`・`odd_at_most`)と `style.py`(記号・色・`my_glyph`)に切り出した
- **4b キーの表**: 新規 `keymap.py`(`KEYMAP`・`KeyDef`・`BY_KEY`・`HOTKEYS`・`help_key_lines()`・`footer_variants()`)。`play.py` のホットキーと振り分け(`dispatch_key`。`run_script` も共通)、Help の「Keys」、最下行(4 種類)を、すべてこの表から作る。キーを足すのは、表の 1 行だけ。ホットキーのない action 用の種類は、今回は、行を持たない(後続の issue)
- **4c 見出し・高さ・幅**: 見出し行 `[Help]  Config`(現在のページを `[ ]` で示す)、最下行のヒント(`Up/Down:scroll`・`^`・`v`)。本文がはみ出すときだけ、`↑` `↓` でスクロール(収まっていれば、移動に使う)。`h`/`c` の切り替え(Help で `c` → Config、Config の項目の選択で `h` → Help)、Esc は 1 つ戻る。**狭い端末(幅 76 桁未満)は、パネルを地図の上に重ねて全面に出す**。Config の画面全体の入力(`config_screen`・`tick_screen`・プレイ中の `rename_screen`)は廃止し、操作を 1 方式にした(起動時の名前の重複は、画面を描く前なので、`rename_screen` が残る)
- **4d 文書とテスト**: `cli-how-to-play.md`(§3.1 サイドパネル)、`spec.md` §17、TODO C-7 を完了に。テスト: `cli/tests/test_cli_keymap.py`(新規: 表の一貫性・ホットキー・Help・最下行の一致、`dispatch_key`、`h`/`c`/Esc の規則、Help の間のゲームのキー)、`test_cli_view_render.py`(広い・狭い端末の Help・Config、スクロール、端末の大きさ(幅 40〜240 × 高さ 16〜41 × 各ページ・スクロールの先頭と末尾)の行列)、`test_cli_session.py`(パネルのキー操作)
- **擬似端末での確認**: 広い端末(110×24): `h` → ↓ で Keys までスクロール → `c` → `2` → `max` → Enter → Esc → Esc → `q`。狭い端末(60×24、全面表示): `c` → `1` → 名前 → Enter → Esc → `h` → ↓ → Esc → `q`。いずれも、期待どおり
- **残る課題**: Actions ページと、ホットキーを割り当てない action(入眠のコントロール)は、後続の issue
