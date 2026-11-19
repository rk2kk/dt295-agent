# ファームウェア復元と最終状態（2026-11-17）

最終確認: 2026-11-17

## 実施内容

1. CFW の MULTI2 復号実装を試みたが、性能壁で断念
   （`MULTI2-PERF-WALL.md` 参照）
2. ユーザー判断で純正ファームへ復元
   - `frc1_load restore frc1-dt295.frc` 実行
   - hook が純正バイト (`c4 6d 02 b0 a8 10 e3 2b`) に戻ったことを確認
3. エージェントを pix-ts-legacy バックエンドに戻した
   - `compose.override.yml` の `--cfw` / `--cfw-binary` を削除
   - `--binary /opt/pix/pix-ts-legacy` の形に復元

## 検証結果（純正ファーム）

| 局 | 結果 |
|---|---|
| GR T21 | ✅ code=200 / 12.8MB（probe）、79MB（EPG スキャン時） |
| BS15 | ✅ 20MB |
| CS06 | ❌ rc=7 bytes=0（以前の不安定が戻った） |

→ denpa での復号視聴は可能になったが、CS06 の受信不安定は再発。

## 現状の構成（最終）

- ファームウェア: 純正（`frc1_load restore` 済み）
- エージェント: pix-ts-legacy バックエンド（`--cfw` なし）
- denpa: 起動中
- EPG_COLLECT_INTERVAL: 86400000（24時間、スキャン抑制設定は残っている）
- TUNER_AGENT_URL: http://t-agent, http://tuner-agent

## 残課題

- CS06 の受信不安定（純正ファームの既知の問題）
  - 別 USB ポートに挿した直後だけ一時的に回復する
  - 物理リセット後の一時回復後、また不安定になる
  - ハードウェア側の問題の可能性が高い
- CFW 時の CS 安定受信は実証済みだが、復号が不可能なため現状維持は不可

### 新しい選択肢（2026-11-19 追加）

- `tatsuya0902/mb8cfwd` を発見。**CFW + ホスト側 MULTI2 復号**で
  CS の安定受信と復号視聴を両立するデーモン。
  作業リスト: `MB8CFWD-TASKS.md`

## 関連ドキュメント

- `CFW-FLASH.md`: CFW 書き換えの記録
- `AGENT-CFW.md`: CFW バックエンドのエージェント改造と未解決点
- `CFW-DECODE-ANALYSIS.md`: 復号のアーキテクチャ解析
- `MULTI2-IMPL-LOG.md`: MULTI2 移植の作業ログ
- `MULTI2-PERF-WALL.md`: 性能壁の記録
- `TS-OUTPUT-CAUSE.md`, `CS06-RETUNE-LOOP.md`: 原因記録
