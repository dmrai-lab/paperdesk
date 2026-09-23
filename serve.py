"""paperdesk: a local review desk for one LaTeX paper.

The built PDF is shown as it is (pdf.js); a selection of words or a click on a figure becomes a comment, and every
comment is anchored by SyncTeX to the source -- file, line, the enclosing section and figure -- beside the words
selected, so the person editing the source is told where without searching. Comments and replies live in
``comments.jsonl`` next to this file; ``desk.py`` reads and answers them from the terminal.

    python serve.py            (reads paperdesk.toml; open http://127.0.0.1:8765)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CFG = tomllib.loads((HERE / "paperdesk.toml").read_text())
PAPER = (HERE / CFG["paper"]["dir"]).resolve()
MAIN = CFG["paper"]["main"]
PDF = PAPER / (Path(MAIN).stem + ".pdf")
STORE = HERE / "comments.jsonl"
AUDIO = HERE / "audio"                                     # voice comments, one file per comment or reply
AUDIO.mkdir(exist_ok=True)
AUTH = json.loads((HERE / "auth.json").read_text()) if (HERE / "auth.json").exists() else None   # {"user":..,"password":..}
LOCK = threading.Lock()
BUILD = {"running": False, "log": "", "ok": None, "finished": None}
WHO = CFG.get("people", {})
REVIEWER = WHO.get("reviewer", "reviewer")            # who comments on the page
EDITOR = WHO.get("editor", "editor")                  # who answers from the terminal (a person or an agent)


# ----------------------------------------------------------------- the store
def load_comments():
    if not STORE.exists():
        return []
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]


def save_comments(items):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in items))
    os.replace(tmp, STORE)


# ----------------------------------------------------------------- the anchor
def synctex_edit(page, x_pt, y_pt):
    """``(file, line)`` of the source that typeset the point ``(x, y)`` in TeX points from the page's top-left."""
    try:
        out = subprocess.run(["synctex", "edit", "-o", f"{int(page)}:{float(x_pt):.2f}:{float(y_pt):.2f}:{PDF}"],
                             capture_output=True, text=True, timeout=20, cwd=PAPER).stdout
    except Exception as e:                                   # noqa: BLE001
        return None, None, f"synctex failed: {e}"
    m_in, m_ln = re.search(r"^Input:(.*)$", out, re.M), re.search(r"^Line:(\d+)$", out, re.M)
    if not m_in or not m_ln:
        return None, None, out[-300:]
    f = Path(m_in.group(1).strip())
    try:
        f = f.resolve().relative_to(PAPER)
    except ValueError:
        pass
    return str(f), int(m_ln.group(1)), None


SECTION_RE = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}")


def context_of(file, line):
    """The enclosing section path, figure or table label, and the source lines around ``line``."""
    p = PAPER / file
    if not p.exists():
        return {}
    lines = p.read_text().splitlines()
    i = min(max(int(line) - 1, 0), len(lines) - 1)
    path, env, label = {}, None, None
    for j in range(i, -1, -1):
        for m in SECTION_RE.finditer(lines[j]):
            kind = m.group(1)
            if kind not in path:
                path[kind] = m.group(2)
        if env is None:
            if re.search(r"\\begin\{(figure|table|figure\*|table\*)\}", lines[j]):
                env = re.search(r"\\begin\{(figure|table|figure\*|table\*)\}", lines[j]).group(1)
                for k in range(j, min(j + 40, len(lines))):
                    m = re.search(r"\\label\{([^}]*)\}", lines[k])
                    if m:
                        label = m.group(1); break
                    if re.search(r"\\end\{(figure|table|figure\*|table\*)\}", lines[k]):
                        break
            elif re.search(r"\\end\{(figure|table|figure\*|table\*)\}", lines[j]):
                env = False                                   # the point is after a float, not in it
        if all(k in path for k in ("section",)) and "subsection" in path:
            break
    order = ["section", "subsection", "subsubsection", "paragraph"]
    return dict(section=" > ".join(path[k] for k in order if k in path),
                float=(env if env else None), label=label,
                source=[dict(line=k + 1, text=lines[k]) for k in range(max(i - 2, 0), min(i + 3, len(lines)))])


def resolve(page, x_pt, y_pt):
    file, line, err = synctex_edit(page, x_pt, y_pt)
    if file is None:
        return dict(file=None, line=None, error=err)
    return dict(file=file, line=line, **context_of(file, line))


# ----------------------------------------------------------------- voice
_MODEL = {"m": None}


def transcribe(path):
    """The words in a voice note, by faster-whisper's small model on the CPU (loaded once); ``None`` when the model is
    not installed, in which case the browser's own dictation, if it gave one, is all the text there is."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    voice = CFG.get("voice", {})
    if _MODEL["m"] is None:
        _MODEL["m"] = WhisperModel(voice.get("model", "small"), device="cpu", compute_type="int8", download_root=str(HERE / "models"), cpu_threads=8)
    segs, _info = _MODEL["m"].transcribe(str(path), vad_filter=True, beam_size=5, initial_prompt=voice.get("glossary") or None)
    return " ".join(sg.text.strip() for sg in segs).strip()


def transcribe_later(cid, reply_index, path):
    """Transcribe in the background and write the words into the comment (or its reply) when they land."""
    def run():
        text = transcribe(path)
        with LOCK:
            items = load_comments()
            for c in items:
                if c["id"] == cid:
                    target = c if reply_index is None else c["replies"][reply_index]
                    if text:
                        target["text"] = text + ("" if not target.get("dictation") else "")
                        target["transcribed"] = "faster-whisper " + CFG.get("voice", {}).get("model", "small")
                    else:
                        target["text"] = target.get("dictation") or ("(voice note, no words recognised)" if text == "" else "(voice note, not transcribed: no model)")
                        target["transcribed"] = "none available" if text is None else "empty"
            save_comments(items)
    threading.Thread(target=run, daemon=True).start()


# ----------------------------------------------------------------- the build
def rebuild():
    with LOCK:
        if BUILD["running"]:
            return False
        BUILD.update(running=True, log="", ok=None)
    def run():
        r = subprocess.run(CFG["paper"]["build"], shell=True, cwd=PAPER, capture_output=True, text=True)
        tail = (r.stdout + r.stderr)[-4000:]
        with LOCK:
            BUILD.update(running=False, log=tail, ok=(r.returncode == 0), finished=time.time())
    threading.Thread(target=run, daemon=True).start()
    return True


# ----------------------------------------------------------------- the server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                                  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _authorised(self):
        """HTTP basic auth when auth.json is present (the desk is reached through a tunnel)."""
        if AUTH is None:
            return True
        import base64
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                user, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                if user == AUTH["user"] and pw == AUTH["password"]:
                    return True
            except Exception:                                    # noqa: BLE001
                pass
        self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="paperdesk"')
        self.send_header("Content-Length", "0"); self.end_headers()
        return False

    def do_GET(self):
        if not self._authorised():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/app.js":
            return self._send(200, (HERE / "static" / "app.js").read_bytes(), "application/javascript")
        if u.path == "/pdf":
            if not PDF.exists():
                return self._send(404, {"error": "no PDF built yet"})
            return self._send(200, PDF.read_bytes(), "application/pdf")
        if u.path == "/api/comments":
            return self._send(200, load_comments())
        if u.path.startswith("/audio/"):
            f = AUDIO / Path(u.path).name
            if not f.exists():
                return self._send(404, {"error": "no such note"})
            ctype = {"webm": "audio/webm", "m4a": "audio/mp4", "mp3": "audio/mpeg", "ogg": "audio/ogg", "wav": "audio/wav"}.get(f.suffix[1:], "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype)
        if u.path == "/api/status":
            with LOCK:
                b = dict(BUILD)
            return self._send(200, dict(paper=str(PAPER), reviewer=REVIEWER, editor=EDITOR, pdf_mtime=(PDF.stat().st_mtime if PDF.exists() else None), build=b,
                                        n_open=sum(1 for c in load_comments() if c.get("status") == "open")))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorised():
            return
        u = urlparse(self.path)
        if u.path == "/api/comments":
            d = self._json()
            anchor = resolve(d["page"], d["x_pt"], d["y_pt"])
            with LOCK:
                items = load_comments()
                cid = 1 + max([c["id"] for c in items], default=0)
                c = dict(id=cid, created=time.strftime("%Y-%m-%d %H:%M:%S"), status="open", author=REVIEWER,
                         text=d.get("text", ""), quote=d.get("quote", ""), page=int(d["page"]),
                         x_pt=float(d["x_pt"]), y_pt=float(d["y_pt"]), rect=d.get("rect"), anchor=anchor, replies=[])
                items.append(c); save_comments(items)
            return self._send(200, c)
        m = re.match(r"^/api/comments/(\d+)/(reply|resolve|reopen|delete)$", u.path)
        if m:
            cid, action = int(m.group(1)), m.group(2)
            d = self._json()
            with LOCK:
                items = load_comments()
                for c in items:
                    if c["id"] == cid:
                        if action == "reply":
                            c["replies"].append(dict(author=d.get("author", REVIEWER), text=d.get("text", ""),
                                                     created=time.strftime("%Y-%m-%d %H:%M:%S")))
                        elif action == "resolve":
                            c["status"] = "resolved"
                        elif action == "reopen":
                            c["status"] = "open"
                        elif action == "delete":
                            items = [x for x in items if x["id"] != cid]
                        break
                save_comments(items)
            return self._send(200, {"ok": True})
        if u.path == "/api/voice":
            # the audio as the body, the comment's fields in the query string; the text is the browser's dictation until
            # the server's transcription lands, and "(voice note, transcribing)" when there was none
            from urllib.parse import parse_qs
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            n = int(self.headers.get("Content-Length") or 0); blob = self.rfile.read(n)
            ext = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/wav": "wav"}.get(
                (self.headers.get("Content-Type") or "").split(";")[0].strip(), "bin")
            dictation = q.get("dictation", "").strip()
            with LOCK:
                items = load_comments()
                if q.get("reply_to"):
                    cid = int(q["reply_to"]); c = next(x for x in items if x["id"] == cid)
                    fname = f"{cid}-r{len(c['replies'])}.{ext}"
                    c["replies"].append(dict(author=q.get("author", REVIEWER), text=dictation or "(voice note, transcribing)",
                                             dictation=dictation, audio=fname, created=time.strftime("%Y-%m-%d %H:%M:%S")))
                    reply_index = len(c["replies"]) - 1
                else:
                    anchor = resolve(q["page"], q["x_pt"], q["y_pt"])
                    cid = 1 + max([x["id"] for x in items], default=0); fname = f"{cid}.{ext}"
                    c = dict(id=cid, created=time.strftime("%Y-%m-%d %H:%M:%S"), status="open", author=REVIEWER,
                             text=dictation or "(voice note, transcribing)", dictation=dictation, audio=fname, quote=q.get("quote", ""),
                             page=int(q["page"]), x_pt=float(q["x_pt"]), y_pt=float(q["y_pt"]),
                             rect=(json.loads(q["rect"]) if q.get("rect") else None), anchor=anchor, replies=[])
                    items.append(c); reply_index = None
                (AUDIO / fname).write_bytes(blob)
                save_comments(items)
            transcribe_later(cid, reply_index, AUDIO / fname)
            return self._send(200, c)
        if u.path == "/api/rebuild":
            return self._send(200, {"started": rebuild()})
        if u.path == "/api/resolve":                             # a dry resolution, for the popup's preview
            d = self._json()
            return self._send(200, resolve(d["page"], d["x_pt"], d["y_pt"]))
        return self._send(404, {"error": "not found"})


def main():
    host, port = CFG["server"]["host"], int(CFG["server"]["port"])
    if len(sys.argv) > 1 and sys.argv[1] == "--host":
        host = sys.argv[2]
    print(f"paperdesk on http://{host}:{port}  paper {PAPER}  pdf {PDF.name}  comments {STORE}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
