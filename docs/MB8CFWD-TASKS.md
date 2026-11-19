# 作業リスト：mb8cfwd の取り込み検討（2026-11-19）

最終確認: 2026-11-19

## 背景

`tatsuya0902/mb8cfwd` <https://github.com/tatsuya0902/mb8cfwd> を発見。
2026-11-19 更新。PIX-DT295 + MB8AC031-CFW 用のホスト側チューナーデーモン。

**私たちが性能壁で断念した課題を直接解決するリポジトリ**:
- CFW にはデスクランブラが無い → **ホスト側で MULTI2 復号**
- 内蔵 B-CAS カードとの ECM/EMM 交換を実装
- **復号済み TS を Unix ソケットで配る**
- Mirakurun 互換アダプタ (`mb8cfw-adapter`) 同梱

私たちの状況:
- 純正ファームでは denpa での復号視聴はできるが **CS06 が不安定**
- CFW では CS は安定するが **復号が性能的に不可能**（`MULTI2-PERF-WALL.md`）
- mb8cfwd は **CFW + ホスト復号** でこの両立を実現している

## 作業リスト（進捗）

### 1. リポジトリの精査 ✅
- [x] README / [DESIGN.md](https://github.com/tatsuya0902/mb8cfwd/blob/main/DESIGN.md) を読む
- [x] `src/` の構成（broker / card / keys / multi2 / pipeline）を確認
- [x] 必要な依存（libusb-1.0、C11、libarib25 は不要と記載）を確認
- [x] `tools/` の libarib25 相互検証の内容を確認

### 2. ビルド・検証（ローカル） ✅
- [x] リポジトリを clone（Pi 上 `/home/k-rp5/mb8cfwd`、HEAD `5d6e287`）
- [x] `make test`（オフライン15スイート）を実行
- [x] `make daemon` で `mb8cfwd` / `mb8cfw-adapter` をビルド
- [x] カード・CFW 無しで模擬テストが通ることを確認
  - 上流バグ 1 件を発見・ローカル修正（下記「精査・検証の結果」参照）

### 3. denpa との連携設計 ✅
- [x] mb8cfwd の復号済み TS を denpa エージェントから読む方法を設計
  - **結論: 案 A（エージェント側のバックエンド差し替え）を採用**（下記「連携設計の結論」）
- [x] `mb8cfw-adapter` を pix-ts 形式の引数で呼べるか確認
  - README で明示: `--instance dt295-1 --receiver 0 --channel <channel> --time -` をそのまま受理
  - ただし `--device` / `--channel` が必須で、現在の `dt295-agent` が渡す
    `-d` / `-c` / `--assets` は受理しない（要エージェント改修）
  - 既定ソケット `/run/user/<uid>/mb8cfw-<device>.sock`、`--socket` / `MB8CFW_SOCKET` で変更可
  - Mirakurun 側に decoder は指定しない（二重復号回避）

### 4. 実機検証（要・許可）
- [ ] CFW を再書き込み（`frc1_load install`、純正へは `restore` で戻せる）
- [ ] mb8cfwd で CS06 を選局・復号
- [ ] denpa で CS06 が暗号解除されて再生されるか確認
- [ ] GR/BS の回帰確認

### 5. 判断ポイント
- [ ] 純正ファーム（現状・CS06 不安定） vs CFW + mb8cfwd（CS 安定・復号可）の比較
- [ ] 運用の複雑さ（デーモン常駐、CFW 書き込み、カード契約）を許容するか

## 精査・検証の結果（2026-11-19）

### DESIGN.md の要点
- **broker を制御用とカード用に分離**: EPG スキャンと視聴が競合する
  私たちの問題に対する設計示唆がある。
- **BS/CS チャンネル形式の変換**: denpa 側の `BS15_1` をデーモン内で
  `BS15/TS1`（libmb8cfw 形式）へ変換（`src/channel.c`）。GR は物理番号そのまま。
- **鍵管理に世代トークン**: 私たちの CFW 側で起きた鍵世代の問題も
  ここに起因していた可能性がある。
- **v2.31 の相互 TLS 問題を回避**: 平文 HTTP のエージェント構成と相性がよい。
- **実機スモーク済み**（2026-11-19、作者側）: カード・CFW 実機で動作確認。

### 上流バグ（ローカル修正、push せず）
- **箇所**: `tests/mock_card.h` の `mock_card_transport()`
- **症状**: `make test` が `test_card` で Segfault（-O2 時のみ再現、
  ASAN -O0 では ALL PASS）
- **原因**: `struct mb8d_card_transport t` の `last_error` を初期化していない。
  `card.h` には "May be NULL" とあるが、呼び出し側が NULL を設定する契約。
  `power_on` 失敗時に `note_error()` が未初期化の関数ポインタ（実測
  `0x7ffffffff310`）を呼び出して落ちる。
- **修正**: `t.last_error = NULL;` の 1 行（`/home/k-rp5/mb8cfwd` に適用済み、
  git commit せず・push せず）
- **補足**: Makefile にヘッダ依存が無いため、修正後は `make -B test` か
  直接 cc で再ビルドが必要（通常の `make test` は古いバイナリを実行する）。

### ビルド結果
- `make -B test`: 全 15 スイート ALL PASS
  （`test_t1` / `test_broker_free` は個別に PASS を出力しないが exit 0）
- `make daemon`: 成功。バイナリは `build/mb8cfwd` と
  `build/mb8cfw-adapter`（repo 直下ではない）
- `tools/xcheck_multi2.cc`: libarib25 と同一入力でバイト単位一致を検証する
  相互検証ツール（`make xcheck ARIB25_DIR=<arib25/src>`、任意）

### 連携設計の結論（2026-11-19）

**案 A（エージェント側のバックエンド差し替え）を採用する。**

- denpa の `TunerConfig` は `name` / `types` / `device` / `lnb` / `disabled`
  のみ（`denpa-fork/src/lib/server/tuner.ts`）。**`command` フィールドは無く**、
  `device` 文字列をエージェントが解釈する構成。
  → **案 B（denpa に command 直接指定）はスキーマ上不可**。
- `mb8cfw-adapter` は `--device`（または `--instance`）と `--channel` を必須とし、
  `--time -` / `--receiver` は受理するが無視する。
  現在の `dt295-agent.py _command()` が渡す
  `--model` / `--assets` / `-d` / `-c` / `--tsid` / `-t` は受理しない。
  → `dt295-agent.py` に `--mb8cfwd` モードを追加し、
  `--device <id> --channel <ch> --time -` を組み立てる必要がある。
- denpa 側の変更は `config/tuners.json` の `device` 文字列のみ
  （現在 `serial:VA29037030`、`disabled: true`。例: `mb8cfwd:dt295-1`）。
- ホスト側で `mb8cfwd` を systemd ユーザーサービスとして常駐させ、
  Unix ソケットを denpa コンテナへ bind mount する。
- チャンネル変換: BS/CS はデーモン側（`src/channel.c`）で
  `BS15_1` → `BS15/TS1` に変換されるためそのまま渡す。GR は
  `T21` → `21` が必要（要実機確認）。
- 復号はデーモン側で済むため、denpa / Mirakurun 側の decoder は指定しない
  （現状も未指定）。

#### 実装に必要な作業（未着手・実機検証は要許可）
1. `dt295-agent.py` に `--mb8cfwd` モードと `--mb8cfw-adapter` 引数を追加
2. `Dockerfile` に `mb8cfw-adapter` バイナリを同梱
3. `compose.override.yml` の command と `tuners.json` の `device` を差し替え
4. CFW 書き込み（`frc1_load install`）→ デーモン起動 → CS06 視聴確認
5. 純正への復元手順は `CFW-FLASH.md` に既に記載済み

## 前提・制約

- ファームウェア `.frc` はリポジトリに含まない（CFW 側でビルド／リリースから取得）
- B-CAS カードはチューナー内蔵スロットのものを使う（契約は利用者自身）
- LNB 給電は DT295 本体からは行われない（衛星は別途給電が必要）
- CFW は RAM ロード方式で、電源を完全に切れば純正に戻る（可逆）

## 参考

- <https://github.com/tatsuya0902/mb8cfwd>
- <https://github.com/soralis0912/MB8AC031-CFW>
- 自作の過去検討: `MULTI2-PERF-WALL.md`, `MULTI2-IMPL-LOG.md`, `CFW-DECODE-ANALYSIS.md`
