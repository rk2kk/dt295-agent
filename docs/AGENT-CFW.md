# DT295-agent を MB8AC031-CFW バックエンドに改造（2026-11-17）

最終確認: 2026-11-17

## 目的

CFW 化で CS の安定受信ができた（CFW-FLASH.md 参照）ので、
denpa のチューナーエージェントを pix-ts-legacy から `mb8cfw-rec` に切り替える。

## 実装（確認済み）

`dt295-agent.py`:
- `--cfw` / `--cfw-binary` / `--cfw-card-confirm` フラグを追加
- `_cfw_channel()`: denpa 形式を mb8cfw 形式へ変換
  - `CS06:28768` / `CS06_0` -> `CS6`
  - `T21` -> `21`、`BS15:16625` -> `BS15`
- `_command()`: CFW 時は `mb8cfw-rec --dev dt295 <ch> 3600 -`
  （長時間指定して SIGTERM で止める）
- `_spawn()`: CFW 時は recover と DT295 固有 env をスキップ
- fast retune は CFW 時は無効（stdin 再選局コマンドが使えない）
- `/denpa/card/confirm`: CFW 時は `mb8cfw-card-confirm` で B-CAS に
  `0x90 0x3C` を送り、返り値コード（例 2048 = 0x0800）を返す
- `/denpa/card`: CFW 時は CARD_NOTE を付けず、カードIDを返す

`MB8AC031-Driver`:
- `mb8cfw-card-confirm` ツールを追加
  - `<verification-base64> <date-mjd>` -> code（Decimal）
  - `id` -> B-CAS カード番号（denpa に渡すだけ、ログには出さない）

Dockerfile / compose.override.yml:
- CFW バイナリをイメージに含め、`--cfw` で起動

## 検証結果

- CS06 probe: code=200 / 72MB（エージェント経由で成功）
- CS06 実視聴: 133MB / rc=0 / 崩れゼロ
- `mb8cfw-card-confirm <verification> <mjd>` -> 2048 (0x0800 = 契約あり)
- `card/confirm` -> `{"ok": true, "code": 2048}`
- `/denpa/card` -> カードIDを返す

## 未解決（途中）

denpa のライブ画面で CS06 が「この局は契約されていない」になる。

原因の切り分け:
- card/confirm は 2048 を返している
- denpa DB の `card_contracts` は「契約を再確認」で一部 subscribed に更新された
  （service_id 400191/400192/400193/600801/600349 など）
- しかし CS06 の局（ディスカバリー 340、CNN 354、カートゥーン 331、
  ホームドラマ 294、囲碁・将棋 363）は `contract_verification` が DB に無く、
  `subscribed` に含まれない
- denpa の設計では verification が無い局は「復号を確かめる」で実選局して
  判定する必要がある（設定画面の「有料契約を確認」ダイアログ）

残作業:
- EPG 収集で CS06 局の `contract_verification` が入るのを待つ、または
  「有料契約を確認」ダイアログの「復号を確かめる」で CS06 を判定させる
- カードと選局の競合（EPG スキャン）で `card/confirm` がタイムアウト
  することがある（一時的なもの）

### 2026-11-17 追記: 「復号を確かめる」も失敗

「有料契約を確認」ダイアログのディスカバリー(CS06)「復号を確かめる」を押したが、
コンソールに 500 (testDecode)、denpa ログに次が出た:

```
[live] CS:CS06:28768 ffmpeg: Invalid data found when processing input
[live] CS:CS06:28768 ffmpeg: Error initializing filters!
[live] CS:CS06:28768 ffmpeg: libsvtav1 / libopus Task finished with error code -22
```

- test-decode は `use=live+test-decode` priority=5 で選局するが、
  EPG スキャンと競合して 409 になることがある
- 選局できても ffmpeg が CS06 の TS をデコードできず 500 になる
- これは denpa 側の ECM/鍵処理（B-CAS の ECM を ffmpeg の
  libaribb25 等に渡す経路）と、CFW の生 TS が噛み合っていない可能性
- pix-ts-legacy は DT295 内で復号していたが、CFW は生 TS を出すだけなので、
  denpa 側で ECM を扱う必要がある（denpa の通常エージェントはこれをする）
- 現時点では **CFW 化で CS の「受信」は安定したが、
  denpa での「復号視聴」はまだ成立していない**

別途 t-agent（DT295 とは無関係な別エージェント）が denpa の新しい
`/denpa/channels` プローブ方式に 501 を返している（`.denpa/probe-*` GET）。
これは DT295 の問題ではないが、denpa の局取り込みを邪魔している可能性がある。

## 参考

- CFW 契約確認: `agent/Denpa.Agent/BCas.cs` の `Confirm()`
  - APDU: `90 3C 00 00 Lc date_hi date_lo <verification> 00`
  - code = レスポンス[4..5]
- denpa: `isCodeSubscribed()`: `(code & 0x0e00) !== 0 && (code & 0xf100) === 0`
