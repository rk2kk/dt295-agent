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

## 作業リスト（未着手）

### 1. リポジトリの精査
- [ ] README / [DESIGN.md](https://github.com/tatsuya0902/mb8cfwd/blob/main/DESIGN.md) を読む
- [ ] `src/` の構成（broker / card / keys / multi2 / pipeline）を確認
- [ ] 必要な依存（libusb-1.0、C11、libarib25 は不要と記載）を確認
- [ ] `tools/` の libarib25 相互検証の内容を確認

### 2. ビルド・検証（ローカル）
- [ ] リポジトリを clone（Pi 上 `/home/k-rp5/mb8cfwd`）
- [ ] `make test`（オフライン15スイート）を実行
- [ ] `make daemon` で `mb8cfwd` / `mb8cfw-adapter` をビルド
- [ ] カード・CFW 無しで模擬テストが通ることを確認

### 3. denpa との連携設計
- [ ] mb8cfwd の復号済み TS を denpa エージェントから読む方法を設計
  - Unix ソケットを `dt295-agent` のバックエンドにする案
  - または denpa の Mirakurun 互換 API 経由で mb8cfwd をチューナーとして登録する案
- [ ] `mb8cfw-adapter` を pix-ts 形式の引数で呼べるか確認
  （README に pix-ts 形式対応の記載あり）

### 4. 実機検証（要・許可）
- [ ] CFW を再書き込み（`frc1_load install`、純正へは `restore` で戻せる）
- [ ] mb8cfwd で CS06 を選局・復号
- [ ] denpa で CS06 が暗号解除されて再生されるか確認
- [ ] GR/BS の回帰確認

### 5. 判断ポイント
- [ ] 純正ファーム（現状・CS06 不安定） vs CFW + mb8cfwd（CS 安定・復号可）の比較
- [ ] 運用の複雑さ（デーモン常駐、CFW 書き込み、カード契約）を許容するか

## 前提・制約

- ファームウェア `.frc` はリポジトリに含まない（CFW 側でビルド／リリースから取得）
- B-CAS カードはチューナー内蔵スロットのものを使う（契約は利用者自身）
- LNB 給電は DT295 本体からは行われない（衛星は別途給電が必要）
- CFW は RAM ロード方式で、電源を完全に切れば純正に戻る（可逆）

## 参考

- <https://github.com/tatsuya0902/mb8cfwd>
- <https://github.com/soralis0912/MB8AC031-CFW>
- 自作の過去検討: `MULTI2-PERF-WALL.md`, `MULTI2-IMPL-LOG.md`, `CFW-DECODE-ANALYSIS.md`
