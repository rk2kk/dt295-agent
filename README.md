# denpa のチューナー API を喋る、DT295 用の薄いエージェント

denpa はチューナーを「エージェント」経由でしか扱わない。このリポジトリは、
その最小の HTTP API を実装し、選局と TS 出力を
[`pix-userland`](https://github.com/Khronos31/pix-userland) の参照実装
`pix-ts-legacy` に任せる層だけを持つ。

- デバイス: PIX-DT295 (`06b8:103b`) / PIX-DT295 系
- 対象: GR / BS / CS
- 実体: Python 1 ファイル + 復帰ツール + Dockerfile

## 構成

| ファイル | 内容 |
|---|---|
| `dt295-agent.py` | denpa のエージェント API 本体（stdlib のみ） |
| `dt295-recover.c` | ASSP が `0x0002` などで残ったときの復帰ツール |
| `Dockerfile` | `pix-ts-legacy` とアセットを焼き込む |
| `build.sh` | pix-userland からバイナリとアセットを用意してビルド |

`pix-ts-legacy`、DT295 のアセット、ログ、`__pycache__` は**追跡しない**。
どれもビルド時に入力として渡す。

## ビルド

```sh
git clone https://github.com/Khronos31/pix-userland.git ~/pix-userland
./build.sh --userland ~/pix-userland --tag dt295-agent:local
```

- `pix-ts-legacy` は `-DDT295_CAPTURE_TESTING` 付きで作り直す。
  既定の `make` は Linux ではスタブ（DT295 capture 非対応）になるため。
- アセットは既定で `~/pix-userland/firmware` を使い、`dt295.bin` を
  `dt295.assets` として複製する（同じバンドル）。
- 生成物 `pix-ts-legacy` と `assets/` は `.gitignore` 済み。

## 起動（docker compose の override 例）

```yaml
services:
  tuner-agent:
    image: dt295-agent:local
    environment:
      # CS はパススルー (MPEG-2) を既定にする
      DT295_CS_PROFILE: /config/dt295-presets/dt295-passthrough.env
    # 停止時にセッションを畳む時間。短いと SIGKILL され、デバイスが 0x02 に残る
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
| GET | `/denpa/tuners` | チューナー状態（選局中のチャンネル、利用者） |
| GET | `/denpa/stream?type=…&channel=…&priority=…&use=…` | TS を返す（chunked） |
| GET | `/denpa/stream/…&after=…` | 手放す利用者を先に指定（引き継ぎ） |
| GET | `/presets` | 画質プリセットの一覧と「適用中」 |
| GET | `/presets/select?name=…` | プリセット切替（次の選局から） |
| GET | `/denpa/about` | バージョンなどの情報 |

### チャンネル名

| 種別 | 例 | 備考 |
|---|---|---|
| GR | `T21` / `21` | `T` は補われる |
| BS / CS | `BS15:16625` / `CS08:24704` | **中継器:TSID** |
| BS / CS（旧） | `BS15_0` / `BS15` | スロット指定。互換のため受理 |

衛星の `中継器:TSID` は `pix-ts-legacy` へ `-c <中継器> --tsid <TSID>` として渡し、
復調スロットは実機の TSID 表から解決する。denpa v2.26 以降のチャンネル名
（`BS01:16402` 形式）はこの形で届く。

## 画質プリセット

チューナー側の変換プロファイルを選ぶ。反映は**次の選局から**。

| 名前 | 内容 |
|---|---|
| `240p03` | 320x240 / 300 kbps |
| `480p15` | 720x480 / 1500 kbps |
| `720p4m` | 1280x720 / 4000 kbps |
| `720p8m` | 1280x720 / 8000 kbps |
| `passthrough` | 変換なし（放送の MPEG-2 をそのまま） |
| `testpid` | 検証用（`V_PID_OUT=0x1FFD`, `PMT_MODE=7`） |

子プロセスへ渡す環境は `--transcode <file>`（GR/BS の既定）と
`DT295_CS_PROFILE`（CS の既定）で切り替える。

## 運用上の注意

- **同時に 1 セッション**。同じチャンネルは共有、別チャンネルは優先度で奪い合う。
- 誰も見ていない状態が 600 秒続くと子プロセスを `quit` で畳む。
- 停止は必ず `docker compose stop tuner-agent`（grace 付き）。`kill -9` や
  `docker rm -f` はデバイスが `0x0002` に残る原因になる。
- `pix-ts-legacy` とこのエージェント以外から同じ USB 機器を開かない。
- CS の既定は passthrough。CS を H.264 に変換する場合は `DT295_CS_PROFILE` を
  別プロファイルへ向ける（負荷と安定性は未測定）。

## ライセンス

GPL-2.0-or-later。`pix-ts-legacy`（GPL-2.0-or-later）を同梱して動かすため、
配布物も同じ条件にする。
