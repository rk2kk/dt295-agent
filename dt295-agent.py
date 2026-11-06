#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""denpa のチューナー API を実装した、DT295 用のエージェント。

denpa はチューナーを「エージェント」経由でしか扱いません。このスクリプトは
denpa が要求する最小限の HTTP API（/denpa/tuners、/denpa/stream など）を実装し、
選局と TS 出力は参照実装（pix-ts-legacy）に任せます。

    python3 dt295-agent.py --device serial:XXXXXXXX

前提:
  - ~/pix-userland/pix-ts-legacy をビルド済み（-DDT295_CAPTURE_TESTING）
  - デバイスがコールド（状態 0）または未認証 ready（0x10）
  - 1 台につき同時 1 選局。同じ局は共有し、別局は優先度で決まる

GR はチャンネル番号（T21 など）、BS/CS は「中継器:TSID」（BS15:16625）を
受け付けます。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_VERSION = "0.3.4-dt295"
TYPES = ["GR", "BS", "CS"]

IDLE_QUIT_SECONDS = 600.0     # 誰も見ていないままこれだけ経ったら quit する
MAX_SINK_CHUNKS = 512         # 1 利用者あたりの溜め (64 KiB * 512 = 32 MiB)
FIRST_DATA_SECONDS = 18.0     # 選局の初データ待ち (無信号の局は諦める)
STALE_SINK_SECONDS = 4.0      # 書き込みが止まった利用者は居ないものとみなす


def log(message: str) -> None:
    sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), message))
    sys.stderr.flush()


class Sink:
    """1 本の /denpa/stream 接続。"""

    def __init__(self, use: str, priority: int):
        self.use = use
        self.priority = priority
        self.chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=MAX_SINK_CHUNKS)
        self.dropped = 0
        self.aborted = False
        self.last_write = time.time()
        self.conn = None
        self.lease = None

    def dead(self) -> bool:
        """相手が切れているか。切れていれば True。

        接続が既に閉じられていると fileno() が -1 になり、そのまま
        select() に渡すと ValueError でハンドラごと落ちる (実際に踏んだ)。
        ここで必ず「死んでいる」と扱う。
        """
        import select
        if self.conn is None:
            return False
        try:
            if self.conn.fileno() < 0:
                return True
            ready, _, _ = select.select([self.conn], [], [], 0)
            if not ready:
                return False
            return self.conn.recv(1, 0x2) == b""
        except (OSError, ValueError):
            return True

    def push(self, data: bytes) -> None:
        while True:
            try:
                self.chunks.put_nowait(data)
                return
            except queue.Full:
                try:
                    self.chunks.get_nowait()
                except queue.Empty:
                    pass
                self.dropped += 1

    def close(self) -> None:
        try:
            self.chunks.put_nowait(None)
        except queue.Full:
            pass


def head_is_ready(blob: bytes) -> bool:
    """先頭に SPS / PPS / IDR がそろっているか (H.264 のとき)。"""
    has_sps = has_pps = has_idr = False
    i = 0
    while True:
        idx = blob.find(b"\x00\x00\x01", i)
        if idx < 0:
            break
        nal = idx + 3
        if nal < len(blob):
            nal_type = blob[nal] & 0x1F
            if nal_type == 7:
                has_sps = True
            elif nal_type == 8:
                has_pps = True
            elif nal_type == 5:
                has_idr = True
        i = nal
    return has_sps and has_pps and has_idr


class Lease:
    """1 回の選局 (デバイスのセッション)。複数の Sink がぶら下がる。"""

    def __init__(self, kind: str, channel: str, proc: subprocess.Popen,
                 profile: str | None = None):
        self.kind = kind
        self.channel = channel
        self.proc = proc
        # このセッションが実際に使っているトランスコード設定。denpa は
        # /presets の「適用中」でコピー可否を決めるので、CS のように
        # チャンネル別の設定を使うときはこちらを答える必要がある。
        self.profile = profile
        self.sinks: list[Sink] = []
        self.idle_since: float | None = time.time()
        self.bytes = 0
        # 先頭は SPS/PPS/IDR がそろうまで送らない。選局直後や、走査中の
        # セッションに後から乗ったときの壊れた先頭を掴ませないため。
        self.head = bytearray()
        self.ready = False
        self.started = time.time()
        self.passthrough = bool(profile and "passthrough" in profile)
        self.switching = False

    def users(self) -> list[dict]:
        return [{"use": s.use, "priority": s.priority} for s in self.sinks]


class Tuner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.lease: Lease | None = None
        self.stopping = False
        self.thread: threading.Thread | None = None
        threading.Thread(target=self._housekeeping, daemon=True).start()
        self.idr_cache: dict[str, bytes] = {}

    # --- 状態復旧 ---------------------------------------------------------

    def _recover(self) -> None:
        if not self.args.recover:
            return
        serial = self.args.device.split(":", 1)[-1]
        try:
            done = subprocess.run([self.args.recover, serial],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log("recover failed: %s" % exc)
            return
        out = (done.stdout or b"").decode("utf-8", "replace").strip()
        if out:
            log("recover: %s" % out.replace("\n", " / "))

    # --- 常駐プロセス -----------------------------------------------------

    def _profile_env(self, path=None) -> dict:
        """DT295_* overrides read from a transcode profile file (KEY=VALUE lines)."""
        if path is None:
            path = getattr(self.args, "transcode", None)
        if not path:
            return {}
        out = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    out[key.strip()] = value.strip()
        except OSError as exc:
            log("transcode profile unreadable (%s): %s" % (path, exc))
            return {}
        log("transcode profile %s -> %s" %
            (path, ", ".join(sorted(out)) or "(empty)"))
        return out

    @staticmethod
    def _split(channel: str) -> tuple[str, int | None]:
        """`BS15:16625` (中継器:TSID) を pix-ts-legacy の引数に分ける。

        denpa v2.26 以降、衛星のチャンネル名には TSID が付く。pix-ts-legacy は
        `--tsid` だけで復調器のスロットを引けるので、そちらへ渡す。
        `BS15_0` / `BS15` はこれまでどおり `-c` にそのまま渡す。
        """
        base, sep, tail = channel.partition(":")
        if not sep or not tail.isdigit():
            return channel, None
        tsid = int(tail)
        return (base, tsid) if 1 <= tsid <= 65535 else (channel, None)

    def _command(self, channel: str) -> list[str]:
        base, tsid = self._split(channel)
        cmd = [
            self.args.binary,
            "--model", "dt295",
            "--assets", self.args.assets,
            "--detach-kernel-driver",
            "-d", self.args.device,
            "-c", base,
        ]
        if tsid is not None:
            cmd += ["--tsid", str(tsid)]
        cmd += ["-t", "-"]
        return cmd

    def _spawn(self, channel: str) -> tuple[subprocess.Popen, str | None]:
        self._recover()
        cmd = self._command(channel)
        log("spawn %s: %s" % (channel, " ".join(cmd)))
        env = dict(os.environ)
        env["DT295_CONTROL"] = "1"
        env.setdefault("DT295_ALLOW_ECM_CONFLICT", "1")
        # CS channels: default to the passthrough profile (H.264 transcode is
        # still unstable on CS). DT295_CS_PROFILE overrides the choice without
        # a rebuild, e.g. DT295_CS_PROFILE=/config/dt295-transcode.env.
        if channel.upper().startswith("CS"):
            profile_path = os.environ.get(
                "DT295_CS_PROFILE",
                os.path.join("/config/dt295-presets", "dt295-passthrough.env"))
        else:
            profile_path = getattr(self.args, "transcode", None)
        env.pop("DT295_CS_PROFILE", None)
        env.update(self._profile_env(profile_path))
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            start_new_session=True,
            stderr=open(self.args.agent_log, "ab", buffering=0)
            if self.args.agent_log else subprocess.DEVNULL,
            cwd=self.args.workdir,
            bufsize=0,
        )
        return proc, profile_path

    def _pump(self, lease: Lease) -> None:
        assert lease.proc.stdout is not None
        stream = lease.proc.stdout
        while True:
            data = stream.read(65536)
            if not data:
                break
            if not lease.ready:
                lease.head.extend(data)
                limit = 0.8 if lease.passthrough else 2.5
                if (not head_is_ready(lease.head)
                        and len(lease.head) < 4 * 1024 * 1024
                        and time.time() - lease.started < limit):
                    continue
                blob = bytes(lease.head)
                lease.head = bytearray()
                with self.lock:
                    lease.ready = True
                    lease.bytes += len(blob)
                    if lease.channel not in self.idr_cache:
                        self.idr_cache[lease.channel] = blob
                    sinks = list(lease.sinks)
                for sink in sinks:
                    if not sink.aborted:
                        sink.push(blob)
                continue
            with self.lock:
                lease.bytes += len(data)
                sinks = list(lease.sinks)
            for sink in sinks:
                if sink.aborted:
                    continue
                sink.push(data)
        rc = lease.proc.wait()
        log("capture ended channel=%s rc=%s bytes=%d" % (lease.channel, rc, lease.bytes))
        with self.lock:
            if self.lease is lease:
                for sink in lease.sinks:
                    sink.close()
                lease.sinks = []
                self.lease = None

    def _stop_locked(self, lease: Lease) -> None:
        if lease.proc.poll() is None and lease.proc.stdin is not None:
            try:
                lease.proc.stdin.write(b"quit\n")
                lease.proc.stdin.flush()
                lease.proc.wait(timeout=12)
            except Exception:
                pass
        if lease.proc.poll() is None:
            try:
                os.killpg(os.getpgid(lease.proc.pid), signal.SIGTERM)
                lease.proc.wait(timeout=8)
            except Exception:
                try:
                    lease.proc.kill()
                except Exception:
                    pass
        for sink in lease.sinks:
            sink.close()
        lease.sinks = []

    def _housekeeping(self) -> None:
        while not self.stopping:
            time.sleep(1.0)
            with self.lock:
                lease = self.lease
                if lease is None or lease.sinks:
                    continue
                if lease.idle_since is None:
                    lease.idle_since = time.time()
                elif time.time() - lease.idle_since >= IDLE_QUIT_SECONDS:
                    log("idle timeout, stopping %s" % lease.channel)
                    self._stop_locked(lease)
                    self.lease = None

    # --- 利用者の付け外し -------------------------------------------------

    def open_stream(self, kind: str, channel: str, use: str, priority: int,
                    after: list[str]) -> tuple[Sink | None, int, str]:
        with self.lock:
            lease = self.lease
            if lease is not None:
                now = time.time()
                stale = [s for s in lease.sinks
                         if s.dead() or now - s.last_write > STALE_SINK_SECONDS]
                for s in stale:
                    s.aborted = True
                    s.close()
                    lease.sinks.remove(s)
                if stale and not lease.sinks:
                    lease.idle_since = now
                if after:
                    for name in after:
                        lease.sinks = [s for s in lease.sinks if s.use != name]

            if lease is not None and lease.proc.poll() is not None:
                self.lease = lease = None

            # CS は SPS/PPS をストリーム開始時のみ送るため、共有セッションだと
            # 後続視聴者が mid-GOP から始まり途切れる。常に新規 lease で開始する。
            if kind == "CS" and lease is not None and lease.kind == kind and lease.channel == channel:
                self._stop_locked(lease)
                self.lease = None
                lease = None

            if lease is not None and lease.kind == kind and lease.channel == channel:
                sink = Sink(use, priority)
                sink.lease = lease
                cached = self.idr_cache.get(channel)
                if cached:
                    sink.push(cached)
                lease.sinks.append(sink)
                lease.idle_since = None
                return sink, 200, ""

            if lease is not None and lease.sinks:
                top = max(s.priority for s in lease.sinks)
                if top >= priority:
                    return None, 409, "ほかの利用者が使用中です (%s)" % lease.channel
                for old in lease.sinks:
                    old.aborted = True
                lease.sinks = []

            if lease is not None:
                # チャンネルが変わったら、セッションを **きれいに畳んでから**
                # 作り直す。セッション内で再選局すると、AES 連鎖と PSI が
                # 前の局のぶん混ざって "invalid DT295 TS packet" や
                # "incomplete initial PAT/PMT/CAT" になる。
                # quit で終われば状態 0x10 に戻り、次の認証は通る。
                self._stop_locked(lease)
                self.lease = None
                lease = None

            if lease is None:
                proc, profile = self._spawn(channel)
                lease = Lease(kind, channel, proc, profile)
                self.lease = lease
                self.thread = threading.Thread(target=self._pump, args=(lease,), daemon=True)
                self.thread.start()

            sink = Sink(use, priority)
            sink.lease = lease
            lease.sinks.append(sink)
            lease.idle_since = None
            return sink, 200, ""

    def abandon_if_idle(self) -> None:
        """利用者が居ない選局を、linger を待たずに止める (スキャンを速くする)。"""
        with self.lock:
            lease = self.lease
            if lease is not None and not lease.sinks:
                self._stop_locked(lease)
                self.lease = None

    def release(self, sink: Sink) -> None:
        with self.lock:
            lease = self.lease
            if lease is None:
                return
            if sink in lease.sinks:
                lease.sinks.remove(sink)
            if not lease.sinks:
                lease.idle_since = time.time()

    def shutdown(self) -> None:
        with self.lock:
            if self.lease is not None:
                self._stop_locked(self.lease)
                self.lease = None

    def status(self) -> dict:
        with self.lock:
            lease = self.lease
            channel = None
            users = []
            if lease is not None and lease.proc.poll() is None:
                channel = {"type": lease.kind, "channel": lease.channel}
                users = lease.users()
            return {
                "index": 0,
                "name": "DT295 (%s)" % self.args.label,
                "types": list(TYPES),
                "disabled": False,
                "rank": 0,
                # denpa v2.31 はチューナーを pin で見分ける (1 台なので個体名)
                "pin": self.args.label,
                "device": self.args.device,
                "lnb": None,
                "channel": channel,
                "users": users,
                "pid": None,
                "error": None,
            }


    def card_status(self) -> dict:
        """denpa の設定「受信」のカードリーダーに出す、機材の内蔵リーダー。

        DT295 は B-CAS の差し込み口が機材に付いていて、復号は pix-ts-legacy の
        中で行う。denpa からは 1 台のリーダーに見えるので、選局が動いている間を
        「使用中」として答える。カードの番号は読まない (ids は空)。
        """
        with self.lock:
            lease = self.lease
            active = lease is not None and lease.proc.poll() is None
        name = "DT295 内蔵 (%s)" % self.args.label
        return {
            "ok": True,
            "message": "",
            "source": "local",
            "readers": [{
                "name": name,
                "card": True,
                "ids": [],
                "active": active,
                "tuners": [name] if active else [],
            }],
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    tuner: Tuner

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log("%s %s" % (self.address_string(), fmt % args))

    def _json(self, status: int, body) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # --- 画質プリセット -----------------------------------------------------

    PRESET_DIR = "/config/dt295-presets"
    PRESET_CURRENT = "/config/dt295-transcode.env"

    def _preset_entries(self):
        import glob
        out = []
        for path in sorted(glob.glob(os.path.join(self.PRESET_DIR, "*.env"))):
            name = os.path.basename(path)[:-4]
            if name.startswith("dt295-"):
                name = name[6:]
            desc = ""
            try:
                with open(path, encoding="utf-8") as fh:
                    first = fh.readline().strip()
                if first.startswith("# DT295 on-board transcode: "):
                    desc = first[len("# DT295 on-board transcode: "):]
            except OSError:
                pass
            out.append((name, path, desc))
        return out

    def _preset_active(self):
        # 選局中は、そのセッションが実際に読んだ設定を答える。CS は
        # DT295_CS_PROFILE (パススルー) と GR 用の設定が別ファイルなので、
        # PRESET_CURRENT だけを見ると denpa に「変換中」と誤って伝わり、
        # MPEG-2 をコピーして何も映らなくなる。
        tuner = getattr(self, "tuner", None)
        lease = getattr(tuner, "lease", None) if tuner is not None else None
        if lease is not None and lease.proc.poll() is None and lease.profile:
            return self._profile_name(lease.profile)
        return self._profile_name(self.PRESET_CURRENT)

    def _profile_name(self, path: str | None) -> str | None:
        """プリセットのファイル名 (拡張子と dt295- を除いたもの) を返す。"""
        if not path:
            return None
        try:
            with open(path, "rb") as fh:
                cur = fh.read()
        except OSError:
            return None
        for name, candidate, _desc in self._preset_entries():
            try:
                with open(candidate, "rb") as fh:
                    if fh.read() == cur:
                        return name
            except OSError:
                pass
        return None

    def _presets_page(self):
        active = self._preset_active()
        # CS は DT295_CS_PROFILE で固定できる。GR と別ファイルになるので、
        # 一覧とは別に「CS はどれか」も出す (denpa は上の 適用中 だけを見る)。
        cs = None
        if os.environ.get("DT295_CS_PROFILE"):
            cs = self._profile_name(os.environ["DT295_CS_PROFILE"])
        rows = []
        for name, _path, desc in self._preset_entries():
            mark = "&#9679;" if name == active else "&#9675;"
            rows.append(
                "<tr><td>%s</td><td><code>%s</code></td><td>%s</td>"
                "<td><a href=\"/presets/select?name=%s\">使う</a></td></tr>"
                % (mark, name, desc, urllib.parse.quote(name)))
        html = (
            "<!doctype html><meta charset=utf-8>"
            "<title>DT295 画質プリセット</title>"
            "<style>body{font-family:sans-serif;margin:2rem;line-height:1.6}"
            "table{border-collapse:collapse}td,th{padding:.35rem .9rem;"
            "border-bottom:1px solid #ddd;text-align:left}</style>"
            "<h1>DT295 画質プリセット</h1>"
            "<p>適用中: <b>%s</b>　（切り替えは次の選局から反映）</p>"
            "<p>CS: <b>%s</b>　（DT295_CS_PROFILE で固定）</p>"
            "<table><tr><th></th><th>名前</th><th>内容</th><th></th></tr>%s</table>"
            "<p><a href=\"/denpa/tuners\">チューナー状態</a></p>"
            % (active or "(不明)", cs or "(GR と同じ)", "".join(rows)))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _preset_select(self, query):
        name = (query.get("name") or [""])[0]
        name = name[:-4] if name.endswith(".env") else name
        if name.startswith("dt295-"):
            name = name[6:]
        src = os.path.join(self.PRESET_DIR, "dt295-%s.env" % name)
        if not name or not os.path.isfile(src):
            self._json(400, {"message": "no such preset: %s" % name})
            return
        try:
            with open(src, "rb") as fh:
                data = fh.read()
            tmp = self.PRESET_CURRENT + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, self.PRESET_CURRENT)
        except OSError as exc:
            self._json(500, {"message": "cannot switch: %s" % exc})
            return
        log("transcode preset -> %s" % name)
        # 選局中のセッションは古い設定のまま動き続ける。畳んで、
        # 次の再生で新しいプロファイルを読ませる (局を変えたのと同じ扱い)
        tuner = getattr(self, "tuner", None)
        if tuner is not None and getattr(tuner, "lease", None) is not None:
            try:
                with tuner.lock:
                    if tuner.lease is not None:
                        tuner._stop_locked(tuner.lease)
                        tuner.lease = None
                log("transcode preset %s: stopped the running lease" % name)
            except Exception as exc:  # noqa: BLE001
                log("transcode preset %s: lease stop failed: %s" % (name, exc))
        self.send_response(303)
        self.send_header("Location", "/presets")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/denpa/tuners":
            self._json(200, {"tuners": [self.tuner.status()]})
        elif path == "/denpa/about":
            self._json(200, {"name": "denpa-agent-dt295", "version": AGENT_VERSION})
        elif path == "/denpa/channels":
            self._json(200, self._channels_stored())
        elif path == "/denpa/logs":
            self._json(200, {"logs": []})
        elif path == "/denpa/card":
            self._json(200, self.tuner.card_status())
        elif path == "/denpa/events":
            self._events()
        elif path == "/denpa/stream":
            self._stream(query)
        elif path in ("/", "/presets"):
            self._presets_page()
        elif path == "/presets/select":
            self._preset_select(query)
        else:
            self._json(404, {"message": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path = urllib.parse.urlparse(self.path).path
        if path == "/denpa/tuners":
            try:
                with open("/config/tuners.json", "wb") as handle:
                    handle.write(body)
            except OSError:
                pass
            self._json(200, self._echo(body))
        elif path == "/denpa/channels":
            self._json(200, self._channels_merge(body))
        elif path == "/denpa/card/readers":
            try:
                request = json.loads(body or b"{}")
            except ValueError:
                request = {}
            disabled = request.get("disabled") if isinstance(request, dict) else None
            if isinstance(disabled, list) and disabled:
                self._json(400, {"message": "DT295 のカードリーダーは機材に内蔵で、外せません"})
            else:
                self._json(200, {"ok": True})
        else:
            self._json(404, {"message": "not found"})

    # --- channels の保存 --------------------------------------------------

    @staticmethod
    def _channels_stored():
        try:
            with open("/config/channels.json", "rb") as handle:
                value = json.loads(handle.read() or b"null")
        except (OSError, ValueError):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("channels"), list):
            return value["channels"]
        return []

    @staticmethod
    def _channels_incoming(body: bytes):
        try:
            value = json.loads(body or b"null")
        except ValueError:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("channels"), list):
            return value["channels"]
        return []

    def _channels_merge(self, body: bytes):
        table = {}
        for entry in self._channels_stored():
            if isinstance(entry, dict):
                table[(entry.get("type"), entry.get("channel"))] = entry
        for entry in self._channels_incoming(body):
            if isinstance(entry, dict):
                table[(entry.get("type"), entry.get("channel"))] = entry
        merged = list(table.values())
        try:
            with open("/config/channels.json", "wb") as handle:
                handle.write(json.dumps({"channels": merged}, ensure_ascii=False).encode())
        except OSError as exc:
            log("store channels failed: %s" % exc)
        return merged

    @staticmethod
    def _echo(body: bytes):
        try:
            return json.loads(body or b"null")
        except ValueError:
            return {}

    # --- SSE / TS ---------------------------------------------------------

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        def put(payload: bytes) -> None:
            self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")

        try:
            while True:
                put(b"event: ping\ndata: 1\n\n")
                self.wfile.flush()
                time.sleep(10)
        except Exception:
            return

    def _stream(self, query: dict) -> None:
        def one(name: str, default: str = "") -> str:
            values = query.get(name) or []
            return values[0] if values else default

        kind = one("type", "GR").upper()
        channel = one("channel").upper()
        use = one("use", "unknown")
        after = query.get("after") or []
        try:
            priority = int(one("priority", "1"))
        except ValueError:
            self._json(400, {"message": "priority が数値ではありません"})
            return

        if not channel:
            self._json(400, {"message": "channel が要ります"})
            return
        if kind not in TYPES:
            self._json(400, {"message": "%s は未対応です" % kind})
            return
        if kind == "GR":
            if not re.fullmatch(r"(T)?\d{1,2}", channel):
                self._json(400, {"message": "channel の形式が違います: %s" % channel})
                return
            if not channel.startswith("T"):
                channel = "T" + channel
        else:
            # 中継器 (BS15) に、TSID (:16625) か古い形のスロット (_0) が付く。
            # denpa v2.26 以降は TSID 付きの名前を送ってくる。
            sat = re.fullmatch(r"%s\d{1,2}(?::(\d{1,5})|_\d)?" % kind, channel)
            if not sat:
                self._json(400, {"message": "channel の形式が違います: %s" % channel})
                return
            if sat.group(1) is not None and not 1 <= int(sat.group(1)) <= 65535:
                self._json(400, {"message": "TSID が範囲外です: %s" % channel})
                return
            if "_" not in channel and ":" not in channel:
                channel = channel + "_0"

        sink, status, message = self.tuner.open_stream(kind, channel, use, priority, after)
        if sink is None:
            self._json(status, {"message": message or "開けませんでした"})
            return

        sink.conn = self.connection
        deadline = time.time() + FIRST_DATA_SECONDS
        first = None
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                first = sink.chunks.get(timeout=min(1.0, remaining))
                break
            except queue.Empty:
                continue

        if first is None or sink.aborted:
            # 空の 200 を返すと denpa は ffmpeg を起動して即座に諦める。
            # 「空き待ち」として 409 を返し、掛け直してもらう。
            self.tuner.release(sink)
            self.tuner.abandon_if_idle()
            self._json(409, {"message": "選局が間に合いませんでした"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.connection.settimeout(3.0)
        except OSError:
            pass

        def put(data: bytes) -> None:
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")

        try:
            put(first)
            self.wfile.flush()
            sink.last_write = time.time()
            while True:
                chunk = sink.chunks.get()
                if chunk is None or sink.aborted:
                    break
                put(chunk)
                self.wfile.flush()
                sink.last_write = time.time()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        finally:
            self.tuner.release(sink)


def main() -> int:
    default_repo = os.path.expanduser("~/pix-userland")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=25252)
    ap.add_argument("--device", default="", help="USB selector, e.g. serial:XXXXXXXX")
    ap.add_argument("--label", default=None)
    ap.add_argument("--binary", default=os.path.join(default_repo, "pix-ts-legacy"))
    ap.add_argument("--assets", default=os.path.join(default_repo, "assets"))
    ap.add_argument("--workdir", default=default_repo)
    ap.add_argument("--agent-log", default="/tmp/dt295-agent-capture.log")
    ap.add_argument("--transcode", default=None,
                    help="KEY=VALUE file merged into the child environment")
    ap.add_argument("--recover", default="/opt/dt295-recover")
    args = ap.parse_args()
    if not args.device:
        ap.error("--device is required (e.g. serial:XXXXXXXX)")
    if args.label is None:
        args.label = args.device.split(":", 1)[-1]

    tuner = Tuner(args)
    Handler.tuner = tuner
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    def stop(_signum, _frame):
        tuner.stopping = True
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log("dt295-agent %s listening on %s:%d device=%s"
        % (AGENT_VERSION, args.host, args.port, args.device))
    try:
        server.serve_forever()
    finally:
        tuner.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
