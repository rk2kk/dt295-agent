# CFW の復号（descramble）検討（2026-11-17）

最終確認: 2026-11-17

## 結論

**MB8AC031-CFW は TS の復号（descramble）を一切行わない。**

CFW ソース（`soralis0912/MB8AC031-CFW`）を精査した結果:

- `docs/ts.md`: DMA リングから 188 バイトの TS パケットを読み出して
  そのまま USB で送るだけ。`0x47` 同期とパケット長の確認のみ。
- `docs/card.md`: B-CAS カードは ISO 7816 / T=1 の APDU を中継するだけ。
  ECM を受け取って鍵を作る処理は無い。
- ソース全体に ECM / descramble / even-odd 鍵 / DES の処理は存在しない
  （`grep -i 'scramble\|descramble\|ecm\|even\|odd\|key'` で該当なし、
  見つかった "key" は ARM IPC や random store の話）。

つまり **CFW は「スクランブルされた生 TS」を出す**。復号はホスト側の責務。

## denpa 側のアーキクチャ（確認済み）

- denpa の復号はエージェントの `POST /denpa/decode` が担う
  (`src/lib/server/scramble.ts` の `descramble()`)
- denpa の ffmpeg は**復号済み TS を前提**にしている
- 旧 dt295-agent の pix-ts-legacy は DT295 内で復号していたため、
  `/denpa/stream` が復号済み TS を返していた
- CFW では生 TS になるため、ffmpeg が
  `Invalid data found when processing input` で失敗する

公式 denpa エージェント (C#) には完全な ECM 復号がある:
- `agent/Denpa.Agent/BCas.cs`: ECM (0x90 0x34) をカードへ送り鍵を取得、
  3 秒キャッシュ
- `agent/Denpa.Agent/Cas.cs`: TS から ECM を抽出し even/odd 鍵で復号
- `EcmAnswer` / `CacheFor = 3s` / `ShortRetries = 2`

## t-agent では代替できない（確認済み）

`docker` の `t-agent` コンテナ (172.18.0.4:25252) の
`POST /denpa/decode` は 404 (`not found`)。
→ 暗号復号には使えない。

## CFW で denpa 復号視聴を実現するには

`dt295-agent` に `/denpa/decode` を新設し、次を実装する必要がある:

1. TS から ECM PID (PMT の CA 記述子) を抽出
2. ECM セクション本体 (8 バイト見出しの後〜CRC の前) を切り出す
3. B-CAS へ `0x90 0x34` APDU を送り鍵を取得 (`mb8cfw-card-confirm` を拡張)
4. even/odd 鍵で TS パケットを DES 復号
5. 復号済み TS を出力ファイルへ書く

これは公式 C# エージェントと同等のフル実装で、
C (CFW ドライバ側) と Python (エージェント側) にまたがる大きな作業。

## 選択肢

1. **`/denpa/decode` をフル実装する** — 上記 1〜5 を実装。作業量が大きい
2. **純正ファームへ戻す** — `frc1_load restore` で復元。denpa での復号視聴は
   従来通り動くが、CS06 の受信がまた不安定になる
3. **現状維持** — `mb8cfw-rec` で直接 CS の生 TS は録れるが、
   denpa UI での復号視聴は不可

## 参考

- CFW: <https://github.com/soralis0912/MB8AC031-CFW>
  - `docs/ts.md`, `docs/card.md`, `src/main/card.c`
- denpa: `src/lib/server/scramble.ts` (`descramble()`),
  `agent/Denpa.Agent/BCas.cs` (ECM), `agent/Denpa.Agent/Cas.cs` (復号)
