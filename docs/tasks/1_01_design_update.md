# 01 設計書の改訂

**状態: 完了**(設計書 §1,4,5,6,7 を改訂、§8 パラメータ表・§9 未決を追加)。

## 目的
`docs/spec/spec.md` が Unity(C#) 前提のため、サーバ権威型に合わせて改訂する。

## 作業
- §1: エンジンを「Python/FastAPI サーバ(権威)+ Unity/CLI クライアント」に変更
- §4.2: `GridPosition: Vector2Int` → 整数 2 値 `(x, y)`(エンジン非依存)
- §4.3〜4.7: 「毎秒」等を「tick(固定 dt)」基準で表現。パラメータ初期値の表を追加
  (Hydration/Energy/Health 最大値、減少率、MoveSpeed、散布間隔 等。現状未定義)
- §5: Region(チャンク)概念を追記。terrain層/object層
- §6: プロジェクト構成を サーバ/CLI/Unity の 3 パッケージに差し替え
- §7: 実装順序を tasks と整合
- 新規 `docs/spec/architecture.md` へのリンク追加

## 未決事項(作者判断が必要)
- tick レート(提案 5Hz)・Region サイズ(提案 32×32)
- Player 切断時の扱い(残す/消す/タイムアウト)
- 死亡時のリスポーン仕様
- 捕食の「接触」定義(同一セル/隣接セル)
