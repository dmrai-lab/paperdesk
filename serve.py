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
try:
    import tomllib
except ModuleNotFoundError:                                # Python 3.10: the same parser as the tomli backport
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        raise SystemExit("paperdesk reads its config with tomllib (Python 3.11+); on an older Python run: pip install tomli")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CONFIG = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".toml") else HERE / "paperdesk.toml").resolve()
DESK = CONFIG.parent                                       # one desk per paper: its config, comments, audio and auth live together
CFG = tomllib.loads(CONFIG.read_text())
PAPER = (DESK / CFG["paper"]["dir"]).resolve()
MAIN = CFG["paper"]["main"]
KIND = CFG["paper"].get("kind") or ("docx" if MAIN.lower().endswith(".docx") else "latex")
PDF = PAPER / (Path(MAIN).stem + ".pdf")
STORE = DESK / "comments.jsonl"
AUDIO = DESK / "audio"                                     # voice comments, one file per comment or reply
AUDIO.mkdir(exist_ok=True)
AUTH = json.loads((DESK / "auth.json").read_text()) if (DESK / "auth.json").exists() else None   # {"user":..,"password":..}
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


# ----------------------------------------------------------------- Word
_DOCX = {"mtime": None, "paras": None}
_WORDS = {"mtime": None, "pages": None}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_paragraphs():
    """The document's paragraphs in body order, cached by the file's mtime: index, text, style, heading level,
    the table they sit in, and whether they carry an image. A table's cells contribute their paragraphs in order."""
    import zipfile
    import xml.etree.ElementTree as ET
    p = PAPER / MAIN; mt = p.stat().st_mtime
    if _DOCX["mtime"] == mt:
        return _DOCX["paras"]
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body"); paras = []; n_tables = 0

    def add(el, table):
        style = el.find(f"{W}pPr/{W}pStyle"); style = style.get(W + "val") if style is not None else ""
        text = "".join(t.text or "" for t in el.iter(W + "t"))
        m = re.match(r"(?i)(heading|title)\s*(\d*)", style or "")
        level = (int(m.group(2)) if m and m.group(2) else (0 if m else None)) if m else None
        has_image = any(True for _ in el.iter(W + "drawing")) or any(True for _ in el.iter(W + "pict"))
        paras.append(dict(index=len(paras), text=text, style=style, level=level, table=table, has_image=has_image))

    for el in body:
        if el.tag == W + "p":
            add(el, None)
        elif el.tag == W + "tbl":
            n_tables += 1
            for pe in el.iter(W + "p"):
                add(pe, n_tables)
    _DOCX.update(mtime=mt, paras=paras)
    return paras


def pdf_lines():
    """Every line of text on every page of the built PDF with its box in points from the page's top-left
    (``pdftotext -bbox-layout``), cached by the PDF's mtime: ``{page: [(x0, y0, x1, y1, text), ...]}``."""
    import xml.etree.ElementTree as ET
    mt = PDF.stat().st_mtime
    if _WORDS["mtime"] == mt:
        return _WORDS["pages"]
    out = subprocess.run(["pdftotext", "-bbox-layout", str(PDF), "-"], capture_output=True, text=True, timeout=120).stdout
    root = ET.fromstring(out)
    ns = "{http://www.w3.org/1999/xhtml}"
    pages = {}
    for n, pg in enumerate(root.iter(ns + "page"), 1):
        lines = []
        for ln in pg.iter(ns + "line"):
            words = [w.text or "" for w in ln.iter(ns + "word")]
            lines.append((float(ln.get("xMin")), float(ln.get("yMin")), float(ln.get("xMax")), float(ln.get("yMax")), " ".join(words)))
        pages[n] = lines
    _WORDS.update(mtime=mt, pages=pages)
    return pages


def _norm(t):
    return re.sub(r"\s+", " ", t or "").strip().lower()


def _find_run(words, texts):
    """The longest run of ``words`` from their start that occurs in one of ``texts``, as ``(probe, hits)``, the
    start moved along the first six words when nothing from an earlier one occurs; a run of two words counts
    only where it occurs once, a single word only when it is a whole text (a table cell), and a selection across
    cells anchors to its first cell."""
    for start in range(0, min(len(words), 6)):
        for n in range(min(len(words) - start, 14), 1, -1):
            probe = " ".join(words[start:start + n])
            hits = [i for i, t in enumerate(texts) if probe in t]
            if hits and (n > 2 or len(hits) == 1):                # two words anchor only where they occur once
                return probe, hits
        hits = [i for i, t in enumerate(texts) if t == words[start]]
        if hits:
            return words[start], hits
    return "", []


def resolve_docx(page, x_pt, y_pt, quote=""):
    """The paragraph a point on a page comes from: the words selected, or failing a selection the line of text
    nearest the click (its neighbours too when it alone is not found), searched in the document's paragraphs as
    the longest run of those words that occurs. When the run occurs in several paragraphs, the one taken is the
    occurrence whose rank in the document equals the rank of the clicked line among the PDF's lines holding the
    same run, so identical paragraphs resolve to the right one. ``line`` is the paragraph's number, ``section`` its
    heading path, ``float`` a table or an image beside it."""
    pages = pdf_lines(); lines = pages.get(int(page), [])
    paras = docx_paragraphs()
    if not paras:
        return dict(file=MAIN, line=None, error="no paragraphs")
    texts = [_norm(pp["text"]) for pp in paras]
    candidates = [_norm(quote)] if _norm(quote) else []
    k = None
    if lines:
        def dist(l):
            dx = max(l[0] - x_pt, 0, x_pt - l[2]); dy = max(l[1] - y_pt, 0, y_pt - l[3]); return (dx * dx + dy * dy) ** 0.5
        k = min(range(len(lines)), key=lambda i: dist(lines[i]))
        candidates += [_norm(lines[k][4]), _norm(" ".join(l[4] for l in lines[max(k - 1, 0):k + 2]))]
    used, hits, snippet = "", [], ""
    for snippet in candidates:
        used, hits = _find_run(snippet.split(), texts)
        if hits:
            break
    if not hits:
        return dict(file=MAIN, line=None, error=f"'{(candidates or [''])[0][:60]}' not found in the document", matched=None)
    if len(hits) > 1 and k is not None:                              # rank the click among the PDF lines holding the run
        before = [l[4] for pg in sorted(pages) if pg < int(page) for l in pages[pg]] + [l[4] for l in lines[:k]]
        rank = sum(used in _norm(t) for t in before)
        i = hits[min(rank, len(hits) - 1)]
    else:
        i = hits[0]
    off = texts[i].find(used)
    heads = {}
    for j in range(i, -1, -1):
        lv = paras[j]["level"]
        if lv is not None and lv not in heads and all(k > lv for k in heads):
            heads[lv] = paras[j]["text"]
    section = " > ".join(heads[k] for k in sorted(heads))
    near_img = paras[i]["has_image"] or any(paras[j]["has_image"] for j in range(max(i - 1, 0), min(i + 2, len(paras))))
    flt = "table" if paras[i]["table"] else ("figure" if near_img else None)
    label = (f"table {paras[i]['table']}" if paras[i]["table"] else (f"image at paragraph {i + 1}" if near_img else None))
    return dict(file=MAIN, line=i + 1, unit="paragraph", offset=off, matched=used, style=paras[i]["style"], section=section,
                float=flt, label=label,
                source=[dict(line=j + 1, text=paras[j]["text"][:300]) for j in range(max(i - 1, 0), min(i + 2, len(paras)))])


def resolve(page, x_pt, y_pt, quote=""):
    """The anchor of a point on a page, or an unanchored record naming why: a comment is never lost to its anchor."""
    try:
        return _resolve(int(page), float(x_pt), float(y_pt), quote or "")
    except Exception as e:                                      # noqa: BLE001 -- whatever the resolver raised, the comment is kept
        return dict(file=None, line=None, error=f"{type(e).__name__}: {e}")


def _resolve(page, x_pt, y_pt, quote):
    if KIND == "docx":
        return resolve_docx(page, x_pt, y_pt, quote)
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
            return self._send(200, dict(paper=str(PAPER), kind=KIND, reviewer=REVIEWER, editor=EDITOR, pdf_mtime=(PDF.stat().st_mtime if PDF.exists() else None), build=b,
                                        n_open=sum(1 for c in load_comments() if c.get("status") == "open")))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorised():
            return
        try:
            self._post()
        except Exception as e:                                  # noqa: BLE001 -- the page shows the reason instead of a silent failure
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _post(self):
        u = urlparse(self.path)
        if u.path == "/api/comments":
            d = self._json()
            anchor = resolve(d["page"], d["x_pt"], d["y_pt"], d.get("quote", ""))
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
            stash = AUDIO / f"incoming-{int(time.time() * 1000)}.{ext}"; stash.write_bytes(blob)   # the recording first
            try:
                c, cid, reply_index, fname = self._voice_record(q, ext, dictation, stash)
            except Exception as e:                              # noqa: BLE001 -- the recording is kept and named
                return self._send(500, {"error": f"{type(e).__name__}: {e}; the recording is kept as audio/{stash.name}"})
            transcribe_later(cid, reply_index, AUDIO / fname)
            return self._send(200, c)
        if u.path == "/api/rebuild":
            return self._send(200, {"started": rebuild()})
        if u.path == "/api/resolve":                             # a dry resolution, for the popup's preview
            d = self._json()
            return self._send(200, resolve(d["page"], d["x_pt"], d["y_pt"], d.get("quote", "")))
        return self._send(404, {"error": "not found"})

    def _voice_record(self, q, ext, dictation, stash):
        """The comment or reply a voice note becomes, saved with the recording moved to its name."""
        with LOCK:
            items = load_comments()
            if q.get("reply_to"):
                cid = int(q["reply_to"]); c = next((x for x in items if x["id"] == cid), None)
                if c is None:
                    raise KeyError(f"no comment {cid} to reply to")
                fname = f"{cid}-r{len(c['replies'])}.{ext}"
                c["replies"].append(dict(author=q.get("author", REVIEWER), text=dictation or "(voice note, transcribing)",
                                         dictation=dictation, audio=fname, created=time.strftime("%Y-%m-%d %H:%M:%S")))
                reply_index = len(c["replies"]) - 1
            else:
                anchor = resolve(q["page"], q["x_pt"], q["y_pt"], q.get("quote", ""))
                cid = 1 + max([x["id"] for x in items], default=0); fname = f"{cid}.{ext}"
                c = dict(id=cid, created=time.strftime("%Y-%m-%d %H:%M:%S"), status="open", author=REVIEWER,
                         text=dictation or "(voice note, transcribing)", dictation=dictation, audio=fname, quote=q.get("quote", ""),
                         page=int(q["page"]), x_pt=float(q["x_pt"]), y_pt=float(q["y_pt"]),
                         rect=(json.loads(q["rect"]) if q.get("rect") else None), anchor=anchor, replies=[])
                items.append(c); reply_index = None
            stash.rename(AUDIO / fname)
            save_comments(items)
        return c, cid, reply_index, fname


def main():
    host, port = CFG["server"]["host"], int(CFG["server"]["port"])
    src = PAPER / MAIN
    if KIND == "docx" and src.exists() and (not PDF.exists() or PDF.stat().st_mtime < src.stat().st_mtime):
        print(f"building {PDF.name} from {MAIN}", flush=True); rebuild()
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    print(f"paperdesk on http://{host}:{port}  {KIND} {PAPER / MAIN}  pdf {PDF.name}  comments {STORE}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
