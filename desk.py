"""The terminal side of paperdesk: what the person editing the source reads and answers.

    python desk.py [desk/paperdesk.toml] list [--all]   the open comments (or every one), each with its anchor and quote
    python desk.py show ID             one comment in full, with the source lines around its anchor
    python desk.py reply ID "text"     answer it (as the editor named in paperdesk.toml)
    python desk.py resolve ID          mark it done
    python desk.py watch               print each new comment as it arrives (one line per event; for a monitor)
"""
import json
import os
import sys
import time
from pathlib import Path

import tomllib
HERE = Path(__file__).resolve().parent
_ARGS = [a for a in sys.argv[1:] if a.endswith(".toml")]
CONFIG = Path(_ARGS[0]).resolve() if _ARGS else HERE / "paperdesk.toml"
sys.argv = [a for a in sys.argv if not a.endswith(".toml")]
STORE = CONFIG.parent / "comments.jsonl"
_CFG = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
REVIEWER = _CFG.get("people", {}).get("reviewer", "reviewer")
EDITOR = _CFG.get("people", {}).get("editor", "editor")


def load():
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()] if STORE.exists() else []


def save(items):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in items))
    os.replace(tmp, STORE)


def line_of(c):
    a = c.get("anchor") or {}
    where = f"{a.get('file')}:{'¶' if a.get('unit') == 'paragraph' else ''}{a.get('line')}" if a.get("file") and a.get("line") else f"page {c['page']} (unresolved)"
    sec = a.get("section") or ""
    lab = f" [{a['float']} {a.get('label')}]" if a.get("float") else ""
    q = (c.get("quote") or "").strip().replace("\n", " ")
    q = (q[:90] + "…") if len(q) > 90 else q
    return f"#{c['id']} {c['status']:8s} {where}  {sec}{lab}\n    quote: \"{q}\"\n    {c['text']}"


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        items = load()
        for c in items:
            if "--all" in argv or c["status"] == "open":
                print(line_of(c))
                for r in c.get("replies", []):
                    print(f"    ↳ {r['author']}: {r['text']}")
    elif cmd == "show":
        c = next(x for x in load() if x["id"] == int(argv[1]))
        print(json.dumps({k: v for k, v in c.items() if k != "anchor"}, indent=1, ensure_ascii=False))
        a = c.get("anchor") or {}
        unit = "¶" if a.get("unit") == "paragraph" else ""
        print(f"anchor: {a.get('file')}:{unit}{a.get('line')}  section: {a.get('section')}  float: {a.get('float')} {a.get('label')}"
              + (f"  matched: '{a.get('matched')}' at offset {a.get('offset')}" if a.get("matched") else ""))
        for s in a.get("source", []):
            print(f"  {unit}{s['line']:5d} | {s['text']}")
    elif cmd == "reply":
        items = load()
        for c in items:
            if c["id"] == int(argv[1]):
                c["replies"].append(dict(author=EDITOR, text=" ".join(argv[2:]), created=time.strftime("%Y-%m-%d %H:%M:%S")))
        save(items); print("replied")
    elif cmd == "resolve":
        items = load()
        for c in items:
            if c["id"] == int(argv[1]):
                c["status"] = "resolved"
        save(items); print("resolved")
    elif cmd == "watch":
        # a new comment, a new reply by the author, or a voice note whose words landed: one line each
        def state(items):
            out = {}
            for c in items:
                out[("c", c["id"])] = c["text"]
                for i, r in enumerate(c.get("replies", [])):
                    if r.get("author") != EDITOR:
                        out[("r", c["id"], i)] = r["text"]
            return out
        seen = state(load())
        while True:
            items = load(); now = state(items)
            for k, text in now.items():
                if k not in seen:
                    if k[0] == "c":
                        c = next(x for x in items if x["id"] == k[1]); print(line_of(c).replace("\n", " | "), flush=True)
                    else:
                        print(f"#{k[1]} reply by {REVIEWER}: {text}", flush=True)
                elif seen[k] != text and not text.startswith("(voice note"):
                    print(f"#{k[1]} {'voice note transcribed' if k[0] == 'c' else 'reply transcribed'}: {text}", flush=True)
            seen = now
            time.sleep(2)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
