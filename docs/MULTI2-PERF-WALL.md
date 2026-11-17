# MULTI2 実装の性能壁（2026-11-17）

最終確認: 2026-11-17

## 判明した決定的な性能制約

CFW の `src/main/tsout_selene.c` のコメントに実測値がある:

> Copying into a 512-byte buffer first, one byte at a time, caps the stream
> at about **1.2 MB/s** on a PIX-DT295 (measured).

つまり、リングから USB FIFO へ**直接**送る今の方式では 5 MB/s が出るが、
復号のために一旦 512 バイトのバッファへコピーすると **1.2 MB/s に落ちる**。

MULTI2 は 188 バイト単位の復号で、パケットごとに CBC を初期化する必要が
あるため、リング→バッファ→FIFO のコピーが必須。したがって:

- **CS (約 5 MB/s): リアルタイム復号は性能的に不可能に近い**
- GR (約 2 MB/s): ギリギリ可能性があるが、MULTI2 自体の処理も必要なので厳しい
- 録画後のオフライン復号: 時間をかければ可能

さらに MULTI2 復号そのものも FR30 (低速 32bit MCU) で 5 MB/s を
さばけるかは未検証（公式 C# でも「いちばん重い処理」とされている）。

## ここまでに実装したもの（ビルド成功）

- fr30-elf ツールチェイン（GCC 14.2.0）
- multi2.c の fr30 移植（静的確保、fw_memset/memcpy）— コンパイル成功
- CFW への組み込み（35 sources、frc1-dt295.frc 26960 バイト）
- FRQ1 コマンド `CMD_M2_INIT` (0x70) / `CMD_M2_KEY` (0x71) / `CMD_M2_STATUS` (0x72)
- `descramble.c`: 鍵設定と `decrypt()` の骨格（odd/even の type 選択は libaribb25 準拠）

## 未実装（性能壁で保留）

- `tsout_push()` への `descramble_packet()` 組み込み
  → これをやると 1.2 MB/s に落ち、CS が壊れる

## 選択肢

1. **GR のみ CFW 復号を試す** — 2 MB/s なら可能性がある。まず GR で実装・検証
2. **オフライン復号に切り替える** — 録画した生 TS をホストの b25 / C# で復号
3. **CFW 復号を断念し純正ファームへ戻す** — CS06 の不安定が戻るが、
   denpa での復号視聴は確実に動く
4. **現状維持** — CFW の生 TS は取れるが、視聴は不可

## 推奨

性能制約を考えると、2 または 3 が現実的。
1 は GR で動いても CS で動かないので、根本解決にならない。
