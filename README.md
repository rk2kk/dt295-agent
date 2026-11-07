# DT295 チューナーエージェント

denpa はチューナーを「エージェント」経由でしか扱いません。このリポジトリは、
denpa が要求する最小限の HTTP API を実装し、選局と TS 出力を
[`pix-userland`](https://github.com/Khronos31/pix-userland) の参照実装
`pix-ts-legacy` に任せるエージェントです。

- 対応デバイス: PIX-DT295（`06b8:103b`）
- 受信: GR / BS / CS
- 構成: Python スクリプト 1 本、復帰ツール、Dockerfile

## denpa v2.31.0 との関係

denpa v2.31.0 は公式の常駐チューナーエージェント（上流 PR #2173）を同梱していますが、
オンボードの H.264 変換を持ちません。そのため画質プリセット（`240p03` / `480p15` /
`720p4m` / `720p8m`）は引き続きこのエージェントが担い、変換は `pix-ts-legacy` の
オンチップ経路で行います。

上流 v2.31 は相互 TLS と合言葉（`#pair=`）を必須にしました。このエージェントは
平文 http で待ち受け、denpa 側のフォークが `TUNER_AGENT_URL: http://tuner-agent:25252`
で接続する構成です。平文なので、compose の内部ネットワークに閉じ、ポートを外部へ
公開しないでください。

## 構成

| ファイル | 内容 |
|---|---|
| `dt295-agent.py` | denpa のエージェント API 本体（標準ライブラリのみ） |
| `dt295-recover.c` | ASSP が `0x0002` などに残ったときの復帰ツール |
| `Dockerfile` | `pix-ts-legacy` とアセットを同梱する |
| `build.sh` | pix-userland からバイナリとアセットを用意してビルドする |

`pix-ts-legacy`、DT295 のアセット、ログ、`__pycache__` はリポジトリに含めません。
いずれもビルド時に用意します。

## ビルド

```sh
git clone https://github.com/Khronos31/pix-userland.git ~/pix-userland
./build.sh --userland ~/pix-userland --tag dt295-agent:local
```

- `pix-ts-legacy` は `-DDT295_CAPTURE_TESTING` を付けてビルドします。既定の
  `make` では Linux 向けにスタブ（DT295 の受信に対応しない）ができるためです。
- アセットは既定で `~/pix-userland/firmware` を使います。`dt295.bin` は CLI が
  読む `dt295.assets` として複製します（同一のバンドル）。
- 生成した `pix-ts-legacy` と `assets/` は `.gitignore` で除外しています。

## 起動（docker compose の override 例）

```yaml
services:
  tuner-agent:
    image: dt295-agent:local
    environment:
      # CS はパススルー（MPEG-2）を既定にする
      DT295_CS_PROFILE: /config/dt295-presets/dt295-passthrough.env
    # 停止時にセッションを終了させるための待ち時間。短すぎると SIGKILL され、
    # デバイスが 0x02 に残る
    stop_grace_period: 90s
    command:
      - --binary
      - /opt/pix/pix-ts-legacy
      - --assets
      - /opt/pix/assets
      - --workdir
      - /opt/pix
      - --device
      - serial:XXXXXXXX          # ← 自分の個体に合わせる
      - --agent-log
      - /config/dt295-capture.log
      - --transcode
      - /config/dt295-transcode.env
```

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/denpa/tuners` | チューナーの状態（選局中のチャンネル、利用者） |
| GET | `/denpa/stream?type=…&channel=…&priority=…&use=…` | TS を返す（chunked） |
| GET | `/denpa/stream/…&after=…` | 先に手放す利用者を指定する（引き継ぎ） |
| GET | `/presets` | 画質プリセットの一覧と「適用中」 |
| GET | `/presets/select?name=…` | プリセットを切り替える（次の選局から反映） |
| GET | `/denpa/card` | カードリーダーの状態（DT295 の内蔵リーダー 1 台） |
| POST | `/denpa/card/confirm` | B-CAS 契約確認（未対応。501 と理由を返す） |
| PUT | `/denpa/card/readers` | 使わないリーダーの指定（内蔵リーダーは外せないので 400） |
| GET | `/denpa/about` | バージョンなどの情報 |

- DT295 の B-CAS は**機材の内蔵リーダー**です。denpa の設定「受信」の
  カードリーダーには `DT295 内蔵 (<シリアル>)` の 1 台として出ます。カードの
  番号は読まないので `ids` は空です。内蔵で外せないため、画面の「使う」を
  外そうとすると 400 で断ります (400 の理由は画面に出ます)。
- **B-CAS のカード番号と契約確認 (0x90 0x3C) には対応していません。** 復号は
  `pix-ts-legacy` (機材のファームウェア) に任せていて、カードへ APDU を送る口が
  ないためです。denpa v2.32 の契約確認は「対応していません」という理由を返します
  (`/denpa/card` の `message`、`POST /denpa/card/confirm` は 501)。

- `/denpa/tuners` の `signal` に**受信品質（C/N、dB）**を返します。`pix-ts-legacy` が
  5 秒ごとに stderr へ出す通知から拾い、使用中のチューナーだけ返します。換算式は Linux の
  `tc90522.c` と同じで、[smpn2/pix-dt295](https://github.com/smpn2/pix-dt295)（MIT）の
  `signal.rs` を参照しました。
- 配信は、先頭の SPS/PPS/IDR がそろうまで保持してから始めます（最大 2.5 秒、
  passthrough は 0.8 秒）。途中から接続した視聴者にも同じ先頭を渡すので、
  壊れた先頭を掴ませません。
- 選局が間に合わずデータが無いときは、空の 200 ではなく **409** を返します。
  denpa は「チューナー使用中」として掛け直します。

### チャンネル名

| 種別 | 例 | 備考 |
|---|---|---|
| GR | `T21` / `21` | `T` は補われる |
| BS / CS | `BS15:16625` / `CS08:24704` | **中継器:TSID** |
| BS / CS（旧） | `BS15_0` / `BS15` | スロット指定。互換のため受理する |

衛星の `中継器:TSID` は、`pix-ts-legacy` に `-c <中継器> --tsid <TSID>` として渡します。
復調スロットは実機の TSID 表から解決します。denpa v2.26 以降は `BS01:16402` の
形式で届きます。

## 画質プリセット

チューナー側の変換プロファイルを選びます。切り替えは次の選局から反映されます。

| 名前 | 内容 |
|---|---|
| `240p03` | 320x240 / 300 kbps |
| `480p15` | 720x480 / 1500 kbps |
| `720p4m` | 1280x720 / 4000 kbps |
| `720p8m` | 1280x720 / 8000 kbps |
| `passthrough` | 変換しない（放送の MPEG-2 をそのまま出力する） |
| `testpid` | 検証用（`V_PID_OUT=0x1FFD`, `PMT_MODE=7`） |

子プロセスに渡す環境変数は、GR/BS が `--transcode <file>`、CS が
`DT295_CS_PROFILE` です。

## 運用上の注意

- 同時に使えるセッションは 1 つです。同じチャンネルは共有し、別のチャンネルを
  選ぶと優先度の高い利用者が勝ちます。
- 利用者のいない状態が 600 秒続くと、子プロセスに `quit` を送って終了します。
- 停止は `docker compose stop tuner-agent`（grace 付き）で行ってください。
  `kill -9` や `docker rm -f` を使うと、デバイスが `0x0002` に残ることがあります。
- `pix-ts-legacy` とこのエージェント以外から、同じ USB 機器を開かないでください。
- CS の既定は passthrough です。CS を H.264 に変換する場合は `DT295_CS_PROFILE`
  を別のプロファイルに向けます（負荷と安定性は未測定）。

## ライセンス

GPL-2.0-or-later。`pix-ts-legacy`（GPL-2.0-or-later）を同梱して動作させるため、
配布物も同じ条件になります。
