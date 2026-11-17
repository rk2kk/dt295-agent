# CFW への MULTI2 復号実装 — 作業ログ（2026-11-17）

最終確認: 2026-11-17

## 目的

CFW は生 TS（スクランブル済み）を出すだけなので、denpa での復号視聴ができない。
CFW ファームウェア自体に MULTI2 復号を実装し、DT295 内で復号して
denpa に復号済み TS を渡す。

## 完了

1. **参考リポジトリ調査**
   - CFW (`soralis0912/MB8AC031-CFW`): TS は DMA リングからそのまま送信、
     復号機能なし。カードは APDU 中継のみ
   - `libaribb25` (`tkmsst/libaribb25`): `multi2.c` (527行の純粋 C) と
     ECM 処理 (B25Decoder.cpp) がある
   - `smpn2/pix-dt295`: raw-TS はホスト側 b25 に任せる設計（参考）
   - denpa 公式エージェント: `BCas.cs` (0x90 0x30 INT / 0x90 0x32 IDI /
     0x90 0x34 ECM / 0x90 0x3C 契約), `Cas.cs` (MULTI2 鍵)

2. **multi2.c の CFW 移植**（`/home/k-rp5/libaribb25-src/multi2_cfw.c`）
   - `__inline` → `inline`
   - `intptr_t` → `int32_t`（専用ヘッダ `multi2_fr30.h`）
   - `calloc` → 静的インスタンス（CFW は malloc 不使用）
   - `memset`/`memcpy` → `fw_memset`/`fw_memcpy`（CFW の freestanding 関数）
   - 型キャスト追加
   - **ホストでビルド・スモークテスト成功**（`multi2 host smoke OK`）

3. **ビルド環境整備**
   - 依存パッケージはすべてインストール済み
   - 初回ツールチェインビルドは `/tmp`（tmpfs 4GB）の容量不足で失敗
   - `WORK=$HOME/toolchain-work`（ディスク）で再ビルド中

## 実行中

- ⏳ **fr30-elf ツールチェインのビルド**（binutils 完了、GCC コンパイル中）

## 残作業

1. fr30 ツールチェイン完成待ち
2. multi2_cfw.c を fr30 でビルド検証
3. CFW の `card.c` に INT/IDI/ECM APDU 追加
   （SystemKey 32B / InitCbc 8B と ECM 鍵を取得）
4. FRQ1 に復号コマンド追加（ECM をホストから受け取る）
5. `CMD_STREAM` の TS を MULTI2 で復号してホストへ渡す
6. ホスト側 (mb8cfw-rec / dt295-agent) の対応
7. 実機フラッシュ・検証

## リスク

- FR30 (低速 32bit MCU) で 5MB/s のリアルタイム MULTI2 復号が
  性能的に可能か未検証。公式 C# でも「いちばん重い処理」とされている
- multi2.c は 527行、B25 全体は 1427行。ECM 抽出・鍵取得の
  実装も必要で、作業量が大きい
- 実機へのファームウェア書き換えは不可逆（復元は `frc1_load restore` で可能）

## 参照

- CFW: <https://github.com/soralis0912/MB8AC031-CFW>
- libaribb25: <https://github.com/tkmsst/libaribb25>
- smpn2: <https://github.com/smpn2/pix-dt295>
- denpa: `agent/Denpa.Agent/BCas.cs`, `Cas.cs`, `Multi2.cs`
