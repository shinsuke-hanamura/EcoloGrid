# 02 サーバ Core 実装

**状態: 完了**。実装は `server/ecologrid/core/`。テスト `server/tests/`(25件、`make test`)。
残課題: 動物の繁殖(spec.md §9)。Region ごとの Valkey 保存は plan 06。

## 構成(案)
```
server/
├── pyproject.toml
├── ecologrid/
│   ├── core/        # FastAPI 非依存の純 Python
│   │   ├── entity.py, living.py, animal.py, plant.py, seed.py, matter.py
│   │   ├── species.py   # Fox/Rabbit/Player/Wheat/Grass の定義(データ駆動)
│   │   ├── region.py, world.py, grid.py
│   │   ├── terrain.py   # seed 指定の Perlin/value noise
│   │   ├── commands.py, events.py   # Command 入力 / Event 出力
│   │   ├── store.py     # RegionStore(InMemory のみ)
│   │   └── rng.py       # Region 別 seed RNG
│   └── api/         # plan 03
└── tests/
```

## 前提作業
- Python 3.11+ の venv 用意(ローカルは 3.8、fastapi 未導入)

## 作業(spec.md §7 の順に)
1. Entity → LivingBeing → Animal → Plant(+ tick(dt)、パラメータ相互作用)
2. Seed / Grass / Wheat / Rabbit / Fox / Player(捕食・散布・発芽)
3. Grid/Region(Entity 配置、近傍検索、Region 間メッセージの抽象)
4. TerrainGenerator(Perlin 自前実装) / WaterPlacer(窪みに静的に配置)
5. World.tick / Command 処理 / Event 発行(通し seq、リングバッファ)
6. 決定論テスト(同 seed・同入力→同結果)、生態系ヘッドレス実行スクリプト

## 設計制約(アーキテクチャ文書 §4)
Region 越境はメッセージ経由 / 全 ID グローバル一意 / 状態はシリアライズ可能。

## 完了条件
- pytest 通過、ヘッドレスで N tick 回して個体数推移を出力できる

## 環境(用意済み)
Docker 環境は構築済み(`docker/README.md`、`make build/test/up/cli`)。
`server/` に疎通用の `/api/v1/healthz` とテスト 1 件、`cli/` に疎通用 CLI あり(いずれも雛形)。
