# Ecologrid: グリッド生態系サンドボックスゲーム 仕様書

> **思想【確定】**: Ecologrid は、**格子点(整数座標)を扱うゲーム**である(名前の「Grid」)。現在のセルと、将来のボクセルの**一辺の長さは可変**とし、規則は格子点と格子の個数で定義して、一辺の長さには直接依存させない。現行は 2 次元のセル、将来は 3 次元のボクセル(`voxel-spec.md`。「1 ボクセル = 1 辺 1 m」は既定値)。

本書は、確定した**現在の仕様**の全体像と索引である(旧「設計書」。2026-11-10 に `spec.md` に改名)。細かい仕様は、下表の文書が担う。**未実装の将来仕様は、それぞれの文書・項目の末尾に「案」として記載している。**

| 領域 | 文書 | 本書の節 |
|---|---|---|
| 時間(tick・暦・惑星・サーバごとの速さ) | `time-spec.md` | §4.3・§8・§10 |
| 動物の状態(睡眠・覚醒・スタミナ・休憩)・能力値・体力の増減 | `animal-states-spec.md` | §12 |
| 知覚・知覚した地図・記憶・嗜好・吸収性・助言 | `animal-memory-spec.md` | §13〜§15 |
| 繁殖・種子散布のパラメータ | `reproduction-parameters.md` | §4.5.1・§8 |
| WebAPI | `api-spec.md` | §16 |
| サーバ権威型の構成・通信・分散・永続化の方針 | `architecture.md` | §1 |
| ボクセル化(容積・3 次元・重力・ダメージ)【案・未実装】 | `voxel-spec.md` | §11 |
| 遊び方(CLI の操作・画面) | `../cli-how-to-play.md` | §17 |
| 運用(起動・環境変数・管理 API) | `../../docker/README.md` | §17 |

関連: 実装計画と進捗 `../tasks/1_00_overview.md`、未着手の課題 `../tasks/TODO.md`、検討・調査の記録 `../research/`。

## 1. 開発方針

- **アーキテクチャ**: サーバ権威型。ゲームエンジンは Python/FastAPI の WebAPI サーバに置き、Unity・CLI は同じ API のクライアント（詳細: `architecture.md`、`api-spec.md`）
- **クライアント**: Unity（C#, 表示・入力のみ）/ CLI（Python）
- **視点**: 1人称（将来的にオプションで3人称追加）
- **ジャンル**: オープンワールド探索 × グリッドベースサンドボックス
- **開発方式**: フェーズ分割で段階的に実装

---

## 2. フェーズ計画

| フェーズ | 内容 |
|---|---|
| **Phase 1（当初スコープ）** | 生物システム + 手続き生成地形 + 静的な水 |
| Phase 2 | 物質の結合・積み重ね |
| Phase 3 | 物質の分割・境界面操作（Boolean Mesh Cutting） |
| Phase 4 | 液体シミュレーション |

---

## 3. Phase 1 スコープ

```
世界
├── 石盤（大きな固定メッシュ・手続き生成・分割なし）
├── 水（石盤の窪みに静的に溜まる）
└── グリッド上のEntity
    ├── 草・小麦（植物・種子散布）
    └── Player / Fox / Rabbit（Hydration・Energy・Health）
```

---

## 4. クラス設計

### 4.1 継承ツリー

```
Entity
├── LivingBeing         # Hydration / Energy / Health
│   ├── Animal          # 移動・捕食
│   │   ├── Player
│   │   ├── Fox
│   │   └── Rabbit
│   └── Plant           # 移動不可・種子散布
│       └── SeedPlant
│           ├── Wheat
│           └── Grass
├── Seed                # 独立Entity（アイテム的存在）
└── Matter              # 物質（Phase 1は固定メッシュ）
    ├── Rock
    └── Water
```

---

### 4.2 Entity（スーパークラス）

全エンティティの基底クラス。

| プロパティ | 型 | 説明 |
|---|---|---|
| `EntityName` | string | 表示名 |
| `EntityId` | string | 一意ID（GUID） |
| `GridPosition` | (x, y) 整数 | グリッド上の座標（エンジン非依存。実装は `Pos`） |

※実装上のプロパティ名は snake_case（`entity_name` / `entity_id` / `grid_position`）。

---

### 4.3 LivingBeing（生物）

`Entity` を継承。3つの生命パラメータを持つ。

| パラメータ | 英語名 | 説明 |
|---|---|---|
| 水分 | `Hydration` | 0になると脱水症状 → Health減少 |
| エネルギー | `Energy` | 0になると移動不可 → Health減少 |
| 体力 | `Health` | 0になると死亡（OnDeath発火） |

**パラメータの相互作用**

- 時間は **tick**（標準 1 tick = 50 ms。**1 tick = ゲーム内の 1 秒**）で進み、時間に関する設定はすべて tick 数で定義する（`docs/spec/time-spec.md`）。水分・Energy は「満タンから 0 まで何 tick か」で定義する（§8）
- `Hydration == 0` → 毎 tick `Health` が減少（脱水症状。Health 100 → 0 まで 32,400 tick = 9 時間）
- `Energy == 0` → 移動不可 + 毎 tick `Health` が減少
- `Health == 0` → 死亡処理（`OnDeath()`）
- `Hydration` と `Energy` がともに > 0 の間、`Health` は毎 tick わずかに回復する（0 → 100 まで 129,600 tick = 1 日 12 時間）
- 両方 0 の場合はダメージが重なる（各々が独立にダメージを与える）

---

### 4.4 Animal（動物）

`LivingBeing` を継承。

| プロパティ | 説明 |
|---|---|
| `MoveSpeed` | 移動速度 |
| `MoveEnergyCost` | 移動時のエネルギー消費量 |
| `PreyList` | 捕食対象のリスト（Entity IDで定義） |
| `Absorption`（吸収性） | 捕食時に得る Energy（と毒性）の割合。食べ物ごとに持てる（`absorption_by_food`）。個体ごとの値（種の標準値を発生時にコピー。§15） |
| `Preference`（嗜好） | 食べ物ごとの好み。`ate` の reward になる（既定 0。§15） |
| `PerceptionRange` | 知覚の範囲（Chebyshev 距離）。Rabbit 6・Fox 8・Player 20（§13） |

**捕食ルール**: 定義された捕食対象に接触 → 対象を消費 → そのエネルギーの一定割合を自身のEnergyに加算

- **接触 = 同一セル**。**NPC は、空腹で、かつ食事が可能なときに、接触した獲物を自動で捕食する**（空腹 = Energy が `hunger_threshold` 未満。食事が可能 = 意識がある・体が動く・安全な状態である（戦闘状態でない・落下中でない）。`docs/spec/animal-states-spec.md`）。Player は `eat` コマンドで明示的に捕食（睡眠・意識消失中は不可）
- **プレイヤー名**: 名前は**他のプレイヤーと重複できない**（参加時・変更時にサーバで検査。`name_taken`）。名前は `PUT /players/{id}/name` で変更でき、リスポーンしても保持される。CLI のマップ上の表示は**名前の 1 文字目**（自分は白背景・黒文字）。`api-spec.md` §2.1、`../cli-how-to-play.md`
- `PreyList` は Entity ID ではなく **種キー**（Fox / Rabbit / Wheat / Grass）で定義
- 動物は種子を踏むと種子が消滅する。水セル・岩セルは通行不可。水は隣接セルから `drink`（Hydration 回復）
- `Energy == 0` の間は移動不可（歩数の消費で Energy が減る）

---

### 4.5 各動物の捕食対象

| クラス | 捕食対象 |
|---|---|
| `Player` | Fox / Rabbit / Wheat |
| `Fox` | Rabbit / Wheat / Grass |
| `Rabbit` | Wheat / Grass |

---

### 4.5.1 繁殖（Animal）

種ごとに `ReproductionDef` を持つ（持たない種は繁殖しない。Player は現状なし）。時間はすべて **tick の絶対値**で扱う。

| パラメータ | 意味 |
|---|---|
| `type` | 繁殖タイプ: `viviparous`（胎生）/ `oviparous`（卵生）/ `fission`（分裂） |
| `min_age_ticks` | 産める最低年齢（年齢 = 現在tick − `birth_tick`） |
| `min_interval_ticks` | 前回の出産（分裂）からの最短間隔 |
| `min_energy` | 子作り判定に必要な最低 Energy |
| `check_interval_ticks` | 子作り判定の周期（個体単位の定期アクション。誕生 tick を基準に数える） |
| `success_prob` | 判定1回あたりの成功率（当面は一定確率） |
| `gestation_period_ticks` | 胎児・卵を育てる期間（種ごと） |
| `gestation_step_ticks` / `gestation_energy_cost` | 育つ周期 / 1ステップごとに母体が消費する Energy |
| `litter_size` / `offspring_energy` | 1回の出産数 / 生まれた子の初期 Energy |
| `incubation_ticks` | 卵生のみ。産卵から孵化まで |

- **判定**: 個体の誕生 tick から `check_interval_ticks` ごとに、年齢・Energy・間隔の条件を満たし妊娠中でない個体が `success_prob` で成功する（個体ごとにタイミングがずれる）
- **胎生/卵生**: 成功すると妊娠（抱卵）。妊娠成功の tick から正確に `gestation_step_ticks` ごとに Energy を消費して育ち、`gestation_period_ticks` 経過で出産（期間が step の倍数なら期間ちょうど）（胎生=子が生まれる / 卵生=`Egg` を産み、`incubation_ticks` 後に孵化）。Energy 不足なら**流産**（卵は失われる）
- **分裂**: 成功すると即座に Energy を 1/2 ずつにして 2 体になる
- 子は母体の隣接する歩けるセルに生まれる（同じ Region 内に限る）。初期個体は成体として配置
- 実装: `core/reproduction.py`（定期アクションのハンドラ）、`core/egg.py`

### 4.6 Plant（植物）

`LivingBeing` を継承。移動不可。

| プロパティ | 説明 |
|---|---|
| `SeedDispersalRange` | 種子散布の最大距離（グリッド単位） |
| `SeedDispersalInterval` | 種子散布の間隔（tick。誕生 tick を基準に数える。`plant_cycle`） |

**SeedPlant**: `Plant` を継承。一定時間ごとに `Seed` エンティティをランダムな近傍グリッドに生成する。

| クラス | 説明 |
|---|---|
| `Wheat` | 小麦。SeedPlant。 |
| `Grass` | 草。SeedPlant。 |

---

### 4.7 Seed（種子）

`Entity` を直接継承する独立したエンティティ（アイテム的存在）。

| プロパティ | 説明 |
|---|---|
| `PlantType` | 発芽後に生成される植物の種類 |
| `GerminationTime` | 発芽までの時間（tick） |

- グリッド上に落下して一定時間後に対応する植物に変化する
- 動物に踏まれる・捕食されると消滅する

---

### 4.8 Matter（物質）

`Entity` を継承。

**Phase 1の仕様**（分割・変形なし）

| プロパティ | 説明 |
|---|---|
| `MatterType` | Rock / Water |
| `IsStatic` | true（Phase 1は全て固定） |

**将来仕様**（Phase 3以降）
- 頂点はグリッド格子点にスナップ（整数座標）
- 面は頂点を結んだ任意の多面体
- 「削る」= Boolean Mesh Cutting（EzySlice等を検討）
- 物質どうしの接触・結合あり（土の上に石を積む等）
- 水は最初静的、Phase 4で流体シミュレーションへ

---

## 5. グリッドシステム

- 1グリッドセル = 1ワールド単位
- 世界は **Region（既定 32×32 セル）** の集合。Region は独立に tick でき、Region 間の相互作用（越境移動・種子散布）はメッセージで行う（分散対応の土台）
- 各セルのEntity配置: terrain層（陸/水/岩のセル種別）/ object層（Entity）。1セルに植物・種子は1つ
- 地形（石盤）は seed 指定の Perlin ノイズで高さ場を作り、水位未満=水・高所=岩・他=陸に分類（Phase 1 は固定）
- 世界サイズ既定: 4×4 Region = 128×128 セル（1 Region = 32×32 セル。`ECOLOGRID_REGION_SIZE`・`ECOLOGRID_REGIONS_X`・`ECOLOGRID_REGIONS_Y` で変更可。シミュレーションは `python -m ecologrid.tools.simulate --regions-x N --regions-y N --region-size S [--scale-animals]`）

---

## 6. プロジェクト構成

```
ecologrid/
├── server/                  # ゲームサーバ（Python / FastAPI）
│   └── ecologrid/
│       ├── core/            # エンジン（FastAPI 非依存）
│       │   ├── entity.py living.py animal.py player.py plant.py seed.py matter.py
│       │   ├── species.py   # 種ごとのパラメータ（§8）
│       │   ├── ai.py        # NPC の判断（decide / execute / think。§13）
│       │   ├── perception.py percept_map.py pathfinding.py   # 知覚・知覚した地図・BFS（§13）
│       │   ├── memory.py    # 記憶（§14）
│       │   ├── actions.py sleep.py reproduction.py clock.py planets.py   # 周期アクション・睡眠・繁殖・時間
│       │   ├── region.py world.py grid.py terrain.py
│       │   └── messages.py rng.py config.py factory.py
│       ├── api/             # WebAPI（api-spec.md）
│       └── tools/simulate.py  # ヘッドレス実行
├── cli/                     # CLI クライアント（plan 04）
├── Assets/                  # Unity クライアント（plan 05。未着手）
├── docker/ docker-compose.yml Makefile
└── docs/
    ├── spec/                # 確定した仕様（本書 + 詳細。未実装の将来仕様は各文書の末尾に「案」）
    ├── research/            # 検討・調査の記録
    ├── tasks/               # 実装計画・進捗・TODO（ファイル名の規則は 1_00_overview.md）
    └── cli-how-to-play.md   # 遊び方
```

Phase 1 の岩・水は地形のセル種別で表現し、`Matter`/`Rock`/`Water` クラスは Phase 2 以降の受け皿として定義のみ。

---

## 7. 実装順序（推奨）

docs/tasks/ の plan に従う（01 設計書改訂 → 02 サーバ Core → 03 WebAPI → 04 CLI → 05 Unity）。
Core 内の順序は Entity → LivingBeing → Animal → Plant → Seed → 各種 → Region/World → Terrain。

---

## 8. パラメータ表（暫定値・バランス調整対象）

実装: `server/ecologrid/core/species.py`、`config.py`、`planets.py`、`clock.py`。**時間はすべて tick 数**（1 tick = ゲーム内の 1 秒。1 分 = 60 tick、1 時間 = 3,600 tick、1 日 = 86,400 tick）。値は暫定。詳細な表: `docs/spec/time-spec.md` §3・§4。

| 種 | 移動の間隔（tick/ボクセル） | 移動Energy消費（歩あたり） | 水分: 満タン→0（tick） | Energy: 満タン→0（tick） | 吸収性（absorption） | 知覚の範囲 | 捕食対象 |
|---|---|---|---|---|---|---|---|
| Rabbit | 20 | 0.0185 | 216,000（2 日 12 時間） | 162,000（1 日 21 時間） | 50% | 6 | Wheat, Grass |
| Fox | 12 | 0.0223 | 216,000（2 日 12 時間） | 129,600（1 日 12 時間） | 60% | 8 | Rabbit, Wheat, Grass |
| Player | 5 | 0.0062 | 259,200（3 日） | 216,000（2 日 12 時間） | 60% | 20 | Fox, Rabbit, Wheat |

| 種 | 散布間隔（tick） | 散布距離 | 散布Energy消費 | 発芽（tick） | Energy: 0→満タン（tick） |
|---|---|---|---|---|---|
| Grass | 97,200（1 日 3 時間） | 2 | 10 | 64,800（18 時間） | 129,600 |
| Wheat | 162,000（1 日 21 時間） | 2 | 15 | 97,200（1 日 3 時間） | 162,000 |

- 共通: 各最大値 100。欠乏（水分 0 または Energy 0）で Health 100 → 0 まで 32,400 tick（両方なら半分）、回復 0 → 100 まで 129,600 tick
- Player は水分・Energy 100（満タン）で開始。drink は水分 +10（動物は +5）
- 植物は水セルから Manhattan 距離 6 以内なら Hydration が満たされる（外では 1,296,000 tick で 100 → 0）。植物の状態は**遅延評価**（毎 tick の更新はしない。読み取り時と `plant_cycle` で経過 tick 分を反映）
- 繁殖・散布・発芽の周期は 2026-11-07 に長くした（繁殖 10 倍など。`docs/spec/reproduction-parameters.md`）
- 初期配置: 動物は世界全体の頭数（Rabbit 32・Fox 4。`WorldConfig.initial_counts`）、植物は密度（Grass 0.08・Wheat 0.02）。繁殖のパラメータは `docs/spec/reproduction-parameters.md`
- 動物の水分は気温で決まる（`docs/spec/animal-states-spec.md` §6.9。20 ℃で Player 約 62.8 時間）。上表の「水分」の列は旧値
- 知覚の更新周期 `perception_interval_ticks` = 20 tick（全種）。嗜好 `preference` の既定は 0
- NPC AI（§13）: 睡眠・休憩の判断、水分 < 50 で水へ、Energy < 60（空腹）で、食事が可能なとき獲物へ、その他は徘徊。判断は知覚した地図だけで行う

## 9. 未決・既知の課題

- ~~動物の繁殖が未定義~~ → §4.5.1 で定義済み。**バランスは未調整**: 旧(秒)の値では Rabbit が急増して草を食べ尽くし激減する振れがあった。tick 数の値（§8）に移行後の推移は未確認（TODO G-2）。パラメータ調整が必要
- 飢えた個体が入眠圧 0 で眠れず意識消失を繰り返す（確定仕様の帰結。TODO G-2）
- 草が増えすぎる（陸の約半分で飽和）。種子散布・植物の間引き（寿命/密度）の調整が必要
- Player 切断時の扱い: API に 300 秒触れないプレイヤーは退出（`ECOLOGRID_IDLE_TIMEOUT`。`api-spec.md`）
- 死亡時: `respawn` コマンドでランダム地点に新規 Player（暫定）
- NPC が動く個体（獲物など）を認識できるのは担当 Region 内（地形・植物の地図と経路探索は Region をまたぐ。Region 越境の知覚は TODO G-11）

## 10. 定期アクション（N tick ごとの処理）

「N tick ごとに処理を実行する」仕組み。実装: `core/actions.py`。

**世界の tick**: `World.tick_no`（絶対値、1 始まり。Region には `Region.tick_no` として渡される。API の `tick`）。

### 10.1 2 つの実行モード

| モード | 用途 | 実行条件 |
|---|---|---|
| `world` | 世界全体の周期処理（天候・昼夜・定期保存など） | `tick_no % N == offset`（`offset` で同じ N のアクションの実行 tick をずらせる） |
| `entity` | 個体ごとの周期処理（妊娠の成長・子作り判定など） | 個体ごとの基準 tick（anchor）から N tick ごと: `(tick_no − anchor) % N == 0` かつ `tick_no > anchor` |

- 世界の時計に揃えると、同じ N のアクションが全個体で同じ tick に集中し、基準もずれる（例: tick 1003 に妊娠した個体の最初の成長が 1025 になる）。個体単位の処理は **個体自身の anchor から数える**
- anchor: `start="spawn"` は誕生 tick（`birth_tick`）、`start="manual"` は `Region.start_timer` を呼んだ tick（妊娠成功など）。個体の `timers`（action_id → anchor）に保持し、Region 越境でも引き継ぐ
- 初期個体は誕生 tick を乱数で散らして配置（実行タイミングの分散）

### 10.2 タイマーホイール（走査なしで実行対象を引く）

`(tick_no − anchor) % N == 0 ⇔ tick_no % N == anchor % N`。`(アクション, N)` ごとに N 個のスロットを持ち、個体を `anchor % N` のスロットに登録する。各 tick は `tick_no % N` のスロットだけを引く（全個体の走査なし）。個体の移動・死亡・越境ではスロットから外し／付け替える。

### 10.3 保管とスケール

- アクションは **データ**（`ScheduledAction`: `action_id` / `handler` / `interval_ticks` / `mode` / `offset` / `species` / `start` / `params` / `enabled`）として **ActionStore** に保管。処理本体は名前でコード側に登録するハンドラ（`@register_handler` / `@register_entity_handler`）
- ストアは **スコープ**（キー）ごとに一覧を持つ: `global`（全 Region 共通）/ `region:{x}:{y}`（その Region のみ）。将来 `area:...` 等を足せばエリア単位に分割できる
- Phase 1 は `InMemoryActionStore`。Phase 2 で Valkey 実装に差し替え（一覧: `actions:{scope}`、ホイール: `due:{scope}:{action_id}:{slot}` → 個体 ID の集合）。運用中の追加・無効化・間隔変更が可能（追加・変更時はホイールを既存個体から作り直す）
- 登録済みアクション（entity モード）: 繁殖定義を持つ種ごとに `breed_check:{種}`（子作り判定、start=spawn）と `gestation_step:{種}`（妊娠進行、start=manual。分裂を除く）
- Region 内の実行順: 受信メッセージ → Player コマンド → **定期アクション** → 各 Entity の tick
- **植物の種子散布は `plant_cycle`（誕生 tick を基準に `seed_dispersal_interval_ticks` ごと。entity モード）**。植物の水分・Energy・Health は遅延評価で、毎 tick の処理はない。動物の代謝・卵の孵化は個体の状態から毎 tick 計算する（動物の AI は `move_interval_ticks` ごとにだけ考える）

## 11. 仕様変更予定: ボクセル化（容積・3 次元・重力・ダメージ）

セルを 1 辺 1 m の立方体「ボクセル」に置き換え、個体に容積（大きさ）、ボクセルに容量（1.0 固定）を持たせる。
座標を (x, y, z)（世界の高さ 8）にし、重力・落下・エネルギー/ダメージの統一、アクションの範囲を導入する。
**仕様と未決事項: `docs/spec/voxel-spec.md`**。実装計画: `docs/tasks/x_08`〜`x_10`、時間の仕様: `docs/spec/time-spec.md`（tick 50 ms、時間の設定はすべて tick 数）、後回しの課題: `docs/tasks/TODO.md`（実装は未着手。本書 §4〜§8 は現行の 2 次元仕様のまま）。

## 12. 動物の状態（睡眠・覚醒・食事可能）と個体の能力値（実装済み 2026-11-06。簡略計算モード `lite` を除く）

動物（Player を含む）に睡眠状態と覚醒度（`wakefulness`）を導入する。**睡眠のコアタイムは設けず**、覚醒度（行動によらず覚醒中に一定量ずつ減り、睡眠中に回復する。体力には影響されない。0 で意識消失・強制睡眠）と、**入眠圧**（(1 − 覚醒度) + 睡眠周期要素。入眠確率 = 係数 × 入眠圧^n で、1 以上になれば必ず入眠。睡眠周期要素は、種ごとの標準入眠周期（地球の動物は 1 日）と、個体が記録した直近 5 回の入眠時刻の最頻値を頂点とするサインカーブ。確率への換算はべき指数 `sleep_pressure_exponent` を使う。判定は一定間隔。飢え・渇きが深刻なほど眠りにくい。入眠の記録がない個体は種ごとの標準入眠時刻を使う。離れた Region の簡略計算モード `lite` では、定刻睡眠 `scheduled_sleep`（種の標準入眠時刻に入眠。将来実装）で計算する）・**起床確率**（覚醒度が高いほど高い右上がりのシグモイド関数に、飢え・渇きを加える）で睡眠を決める。個体の能力値として、筋力（攻撃力・防御力に影響）・精神力（覚醒度の最大値を決める。個体ごとの初期値。現状は種の値、将来は遺伝子）・体力（攻撃力・防御力・覚醒度に影響しない）を持たせる。**スタミナ**（行動で消費。最大値は現在の体力値。回復は体力の 10 倍）と**休憩**（`rest` アクション）を加え、体力・スタミナの増減を**状態とアクションごとの増減表**で定める。NPC の自動捕食は「空腹で、食事が可能（意識がある・体が動く・安全な状態: 戦闘状態でない・落下中でない）なとき」に限る。
**仕様: `docs/spec/animal-states-spec.md`**。実装計画: `docs/tasks/1_14`（実装済み）。

## 13. 知覚・知覚した地図・NPC の判断・経路探索（実装済み 2026-11-10）

**仕様: `animal-memory-spec.md` §3.1・§10**。実装計画と結果: `docs/tasks/1_16`。

- **知覚の範囲**: 個体ごとの `perception_range`（Rabbit 6・Fox 8・Player 20）。**有効な範囲 r = floor(範囲 × 覚醒度の割合)**、眠っている間は 0（自分のセルだけ）。範囲は「自分のセル + 距離 r」の正方形（Chebyshev 距離）。UI・API に出すのは、この範囲だけ
- **知覚した地図 `PerceptMap`**: 個体が知覚して得た地形（セル）と資源（4×4 の区画ごとの件数・見た tick・代表のセル）。`perception_interval_ticks`（20 tick）ごとに更新する。強化学習での状態に相当する。動く個体は地図に残さない
- **NPC の判断**: `decide`（副作用のない判断）/ `execute`（実行）/ `think`。**判断は地図だけ**で行う（動く獲物だけ、現在の知覚の範囲を直接見る）。目標への経路は BFS（`core/pathfinding.py`）で、未知のセルは通れると仮定して計画し、塞がれたら再計画する
- Region 内の NPC が、遠い目標へ向かう途中で意識消失するなどの課題: TODO G-2

## 14. 記憶（実装済み 2026-11-10）

**仕様: `animal-memory-spec.md`**。実装計画と結果: `docs/tasks/1_17`。

- **短期記憶**: 経験のログ（件数の上限なし）。**入眠・意識消失の整理で空になる**。**長期記憶**: 集計表（行動・結果・対象・距離・場所の区画ごとの `count`・`reward_sum`・`strength`）
- **整理（入眠・意識消失のとき）**: リプレイのフック → 忘却（係数 0.9、強さが 0.5 未満は消える。長期記憶と地図の資源の層）→ ログを長期記憶へ統合 → ログを空にする
- **強さ** = (1 + |reward|) × 乱数（決定的な乱数）。場所は距離 D で丸めて記憶（drink・rest は 2、その他は 4）
- **想起**: 目標指向（A: 強さに比例して代表の結果を選び、向かう/除外/避けるを決める）と、場所による（B: 近づくと `p = 1 − exp(−strength/5)`、最大 3 件、再想起まで 600 tick、境界で 1 回）
- `look`（見る）は action ではない（知覚は地図の更新）。全ステップを記録するフラグ `memorise_all_steps`（既定 False）、想起の有効化 `recall_enabled`
- 強化学習の段階化と判断のゲート: `docs/tasks/x_20`（未実装。検討: `../research/animal-memory-rl-notes.md`）

## 15. 食性（嗜好・吸収性）（実装済み 2026-11-10）

**仕様: `animal-memory-spec.md` §9**。実装計画と結果: `docs/tasks/1_18`。

- **吸収性 `absorption`**: Energy と毒性の吸収率だけを決める（Rabbit 0.5・Fox 0.6・Player 0.6）。食べ物ごとの値（`absorption_by_food`）も持てる
- **嗜好 `preference`**: `ate` の reward（既定 0）。記憶の強さに影響する
- 個体の値は、種の標準値を発生時にコピーする（将来は遺伝子）。公開 API には出さない

## 16. WebAPI

**仕様: `api-spec.md`**。PC（プレイヤー）には、**知覚の範囲の内側だけ**の view・events を返す。世界全体の snapshot・events は管理者専用。自分の記憶・地図・助言の API、名前の変更（重複不可）がある。

## 17. クライアント・運用

- **CLI**: 画面と操作は `../cli-how-to-play.md`。知覚の範囲だけを表示する（範囲の外は空白）。**サイドパネル**（マップの横の領域。狭い端末では、マップの上に重ねて全面）に、`h`（Help: 凡例・現在の進行の速さ・キー）、`c`（Config: 名前の変更・進行の速さ（tick の間隔）の変更）を出す。ページの仕組みは `panel.py`・`pages.py`、キーの定義は `keymap.py` の表に 1 か所。内容が収まらないときは ↑↓ でスクロール。`t`（時間表記の切替）
- **運用**: 起動・環境変数・管理 API は `../../docker/README.md`。ヘッドレスのシミュレーションは `python -m ecologrid.tools.simulate`（`--no-recall`・`--memorise-all-steps` など）
