# DT295 ファームウェアを MB8AC031-CFW に差し替え（2026-11-17）

最終確認: 2026-11-17

## 目的

CS チャンネル（特に CS06）の受信安定化。純正ファームでは
「ロックしたが TS が出ない／80KB で停止して rc=7」だった。

## 経緯（確認済み）

- 参考リポジトリ `soralis0912/MB8AC031-Driver` / `MB8AC031-CFW` は
  PIX-DT295 (`06b8:103b`) を明示サポート。独自 CFW を使い、ホストが
  `I2CXFER` で選局する方式（純正ファームの pix-userland とは別系統）。
- 純正ファームに対するレジスタ試験（`0x0E=111` / `0x8E=0x02`）は
  効果がなく、`0x0E=111` は GR まで壊したため revert した
  （詳細は `TS-OUTPUT-CAUSE.md`）。
- デバイスは別 USB ポートに挿し替えた直後だけ回復し、使用後にまた
  固まる状態だった。

## 実施内容（確認済み）

1. CFW リポジトリを clone、リリース `v0.0.1` の `frc1-dt295.frc` を取得。
   SHA256 検証 OK（`35f24c48...`）。
2. `frc1_load` をビルド（libusb）。
3. denpa / tuner-agent を停止し、USB 抜き差しで
   `state=0x00000000`（`no container has booted`）にしてから
   `frc1_load install frc1-dt295.frc` を実行。
   - 純正の 8 バイトは `frc1-dt295.frc.hook` に保存（復元用）
   - 全セグメント書き込み＋readback 検証 OK
   - フック書き込み後 `answered through the new gate`
4. `mb8cfw-rec` で CS06 を録画検証（下記）。

## 検証結果（確認済み）

`mb8cfw-ctl scan --sat`: CS2/4/6/8/…/24 の全 CS トランスポンダーが
ロック。**CS6 は TSID 0x7060 (= 28768) でロック**。

`mb8cfw-rec --dev dt295 CS6/0x7060 15 /tmp/cfw_cs06.ts`:
- 73,400,276 bytes / 390,427 packets
- **resync 0 / dropped 0 / overflow 0 / fw TEI 0 / fw ring full 0**
- PAT 正常
- exit 0

→ 純正ファームで不可能だった CS06 の安定受信を達成。

## 復元方法（参考）

- `frc1_load restore frc1-dt295.frc` で純正の 8 バイトを戻せる
- 控えが無くても、フック（ローダ）は RAM にあるため USB 抜き差しで
  純正バイトが flash から復活する（CFW ドキュメント記載）

## 未確認 / 今後の課題

- denpa との連携: CFW は pix-ts-legacy の選局コマンドを理解しないため、
  現在の dt295-agent（pix-ts-legacy 前提）はそのままでは動かない。
  `mb8cfw-rec` を denpa エージェントのバックエンドにする改造が必要。
- カード（B-CAS）: CFW 側 README は「カード確認」とあるが、
  Driver 側 TODO は PIX-DT295 の `CARD_ON` が `E_IO` で NG としている。
  録画（暗号解除）ができるかは未検証。
- BS の実録画は未検証（scan ではロック確認済み）。
- 長期連続運用の安定性は未検証。

## 参考

- <https://github.com/soralis0912/MB8AC031-CFW>
- <https://github.com/soralis0912/MB8AC031-Driver>
- ファーム: `frc1-dt295.frc` (release v0.0.1)
