# CS06 再チューニング無限ループの原因

最終確認: 2026-11-17

## 概要

CS06 の選局要求で、再チューニングが約 1 秒間隔で無限に繰り返す
（`retune-source` 312 回 / 3 時間）。録画一覧取得などの他機能には影響しない。

## 原因

`dt295-agent` の commit `d95cb8a`（2026-11-16 21:27、
"agent: warm up CS08 before CS06 so fast-retune path is used"、+49 行）で
追加された `_ensure_retune_source()` が原因。

この関数は CS06 の選局要求の前に CS08 を暖気し、データが流れるまで
最大 12 秒待つ（コメントには「CS06 は単独チューンだと PSI 直後にデータが
止まる個体」とある）。しかし、**暖気用の CS08 自体が起動に失敗**する。

```
retune-source: opening CS08 for CS06:28768
retune-source: CS08 ended early
capture ended channel=CS06:28768 rc=7 bytes=0
```

`~/denpa/config/dt295-capture.log`:

```
{"event":"detail","message":"DT295 authentication failed; state-changing request was not retried"}
{"event":"end","exit":7,"state":16,"packets":0,...}
```

- `exit=7` / `phase=4` / `state=0x10 (bootable)` / 受信パケット 0
- 実行中コンテナに `_ensure_retune_source` が存在することを
  `docker exec denpa-tuner-agent-1 grep /opt/dt295-agent.py` で確認済み
- 同じ暖気方式は同日中に既に一度 revert 済み
  （`cda9aa4` → `e0526ee`）。`d95cb8a` はその再挑戦

したがって次のループが継続する:

```
CS06 を直接流す → 失敗 → CS08 を暖気 → CS08 も失敗 → CS06 を再試行 → …
```

## 未確認

`authentication failed` が B-CAS / カード認証そのものの失敗か、
デバイスの状態変更要求の拒否かはログだけでは切り分けられない。
ただし、どちらであっても暖気方式では回避できない
（CS08 も同じエラーになるため）。

## 参考

- 実行環境: `k-raspi5` (`192.168.100.2`, Raspberry Pi 5 / `6.18.50+rpt-rpi-2712`)
- コンテナ: `dt295-agent:local` / `denpa-tuner-agent-1`
- 選局不可能時の denpa 側ログ:
  `CS:CS06:28768: Error: チューナーに空きがありません {"message": "選局が間に合いませんでした"}`
- 対応は本ドキュメントの対象外（原因記録のみ）
