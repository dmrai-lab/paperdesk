"""The terminal side of paperdesk: what the person editing the source reads and answers.

    python desk.py watch               KEEP THIS RUNNING when the editor is an agent: one line per new comment, per reply
                                       by the reviewer, and per voice note whose words land; its heartbeat is what the
                                       page and the server call "watched"
    python desk.py [desk/paperdesk.toml] list [--all]   the open comments (or every one), each with its anchor and quote
    python desk.py show ID             one comment in full, with the source lines around its anchor
    python desk.py reply ID "text"     answer it (as the editor named in paperdesk.toml)
    python desk.py resolve ID          mark it done
    python desk.py transcribe [ID]     the words of the voice notes still without any (or one comment's), once a model is installed

Review agents comment from the source, not the page (LaTeX desks):

    python desk.py add --file sections/theory.tex --quote "words copied verbatim from the file" [--line N]
                       [--severity error|major|minor|style] [--category C] [--suggest "replacement"] [--author ai:ROLE] "the comment"
    python desk.py add --jsonl comments.jsonl     the same, one JSON object per line with those keys (and "text")
    python desk.py list --proposed [--author ai:ROLE]   the agents' comments waiting for triage
    python desk.py accept ID [ID ...]  a proposed comment becomes open work
    python desk.py dismiss ID [ID ...] ["why"]   kept as dismissed, so each reviewer's hit rate can be counted

The quote must occur in the file verbatim (whitespace aside); --line picks among several occurrences. A quote that is
not there is refused, so an agent cannot comment on words the paper does not contain. SyncTeX places the comment on
the built PDF, where the reviewer sees it beside their own.
"""
import json
import sys
import time
from pathlib import Path

import store

try:
    import tomllib
except ModuleNotFoundError:                                # Python 3.10: the same parser as the tomli backport
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        raise SystemExit("paperdesk reads its config with tomllib (Python 3.11+); on an older Python run: pip install tomli")
HERE = Path(__file__).resolve().parent
_ARGS = [a for a in sys.argv[1:] if a.endswith(".toml")]
CONFIG = Path(_ARGS[0]).resolve() if _ARGS else HERE / "paperdesk.toml"
sys.argv = [a for a in sys.argv if not a.endswith(".toml")]
STORE = CONFIG.parent / "comments.jsonl"
_CFG = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
REVIEWER = _CFG.get("people", {}).get("reviewer", "reviewer")
EDITOR = _CFG.get("people", {}).get("editor", "editor")
_PAPER_CFG = _CFG.get("paper", {})
PAPER = (CONFIG.parent / _PAPER_CFG.get("dir", ".")).resolve()
PDF = PAPER / (Path(_PAPER_CFG.get("main", "main.tex")).stem + ".pdf")


def load():
    return store.load(STORE)


def change(ids, fn):
    """Apply ``fn`` to each comment whose id is in ``ids``, under the record's lock; the ids not found are named."""
    ids = {int(i) for i in ids}
    with store.locked(STORE) as items:
        hit = [c for c in items if c["id"] in ids]
        for c in hit:
            fn(c)
        store.save(STORE, items)
    missing = ids - {c["id"] for c in hit}
    if missing:
        print(f"no comment {', '.join(map(str, sorted(missing)))}", file=sys.stderr)
    return hit


def add(d):
    """One source-anchored comment into the record: its id and where it landed, or why it was refused."""
    import anchor
    try:
        c = anchor.comment_from_source(PAPER, PDF, d, d.get("author") or "agent")
    except (LookupError, ValueError) as e:
        return None, str(e)
    with store.locked(STORE) as items:
        c = dict(id=store.next_id(items), **c)
        items.append(c); store.save(STORE, items)
    return c, None


def _opt(argv, name):
    if name in argv:
        i = argv.index(name); v = argv[i + 1]; del argv[i:i + 2]; return v
    return None


def line_of(c):
    a = c.get("anchor") or {}
    tags = " ".join(x for x in (c.get("author") if c.get("author") != REVIEWER else None, c.get("severity"), c.get("category")) if x)
    where = f"{a.get('file')}:{'¶' if a.get('unit') == 'paragraph' else ''}{a.get('line')}" if a.get("file") and a.get("line") else f"page {c['page']} (unresolved)"
    sec = a.get("section") or ""
    lab = f" [{a['float']} {a.get('label')}]" if a.get("float") else ""
    q = (c.get("quote") or "").strip().replace("\n", " ")
    q = (q[:90] + "…") if len(q) > 90 else q
    sug = f"\n    suggest: {c['suggestion']}" if c.get("suggestion") else ""
    return (f"#{c['id']} {c['status']:8s} {where}  {sec}{lab}" + (f"  [{tags}]" if tags else "")
            + f"\n    quote: \"{q}\"\n    {c['text']}{sug}")


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        argv = list(argv); who = _opt(argv, "--author")
        want = None if "--all" in argv else ("proposed" if "--proposed" in argv else "open")
        for c in load():
            if (want is None or c["status"] == want) and (who is None or c.get("author") == who):
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
        change([argv[1]], lambda c: c["replies"].append(dict(author=EDITOR, text=" ".join(argv[2:]),
                                                              created=time.strftime("%Y-%m-%d %H:%M:%S"))))
        print("replied")
    elif cmd in ("resolve", "accept", "dismiss"):
        ids = [a for a in argv[1:] if a.isdigit()]; why = " ".join(a for a in argv[1:] if not a.isdigit())
        status = {"resolve": "resolved", "accept": "open", "dismiss": "dismissed"}[cmd]

        def set_status(c):
            c["status"] = status
            if cmd == "accept":
                c["accepted_by"] = EDITOR
            if why:
                c["replies"].append(dict(author=EDITOR, text=why, created=time.strftime("%Y-%m-%d %H:%M:%S")))
        hit = change(ids, set_status)
        print(f"{status}: {' '.join(str(c['id']) for c in hit)}")
    elif cmd == "add":
        argv = list(argv[1:]); src = _opt(argv, "--jsonl")
        if src:
            rows = [json.loads(l) for l in (sys.stdin if src == "-" else open(src)).read().splitlines() if l.strip()]
        else:
            d = {k: _opt(argv, "--" + f) for k, f in (("file", "file"), ("quote", "quote"), ("line", "line"),
                                                        ("severity", "severity"), ("category", "category"),
                                                        ("suggestion", "suggest"), ("author", "author"))}
            d["text"] = " ".join(argv)
            rows = [{k: v for k, v in d.items() if v is not None}]
        refused = 0
        for d in rows:
            if d.get("line") is not None:
                d["line"] = int(d["line"])
            c, err = add(d)
            if c is None:
                refused += 1; print(f"REFUSED {d.get('file')}: {err}")
            else:
                a = c["anchor"]
                print(f"#{c['id']} {a['file']}:{a['line']} page {c['page']}" + (f"  ({a['error']})" if a.get("error") else ""))
        if refused:
            raise SystemExit(f"{refused} of {len(rows)} refused")
    elif cmd == "transcribe":
        # the words of the voice notes that have none (all of them, or one comment's), once a model is installed
        import voice
        models = CONFIG.parent / "models"; st = voice.status(_CFG, models)
        if not st["ok"]:
            raise SystemExit(st["reason"])
        items = load(); todo = voice.pending(items)
        if len(argv) > 1:
            todo = [(c, i) for c, i in todo if c["id"] == int(argv[1])]
        if not todo:
            print("nothing to transcribe"); return
        audio = CONFIG.parent / "audio"
        for c, i in todo:
            target = c if i is None else c["replies"][i]
            text = voice.transcribe(audio / target["audio"], _CFG, models)
            voice.words_into(target, text, st["model"])
            store.save(STORE, items)
            print(f"#{c['id']}{'' if i is None else f' reply {i}'}: {target['text']}")
    elif cmd == "watch":
        # a new comment, a new reply by the author, or a voice note whose words landed: one line each
        def state(items):
            out = {}
            for c in items:
                if (c.get("author") or REVIEWER) != REVIEWER and c.get("accepted_by") != REVIEWER:
                    continue                                    # an agent's comment is news once the reviewer accepts it
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
            try:
                (CONFIG.parent / "watch.heartbeat").touch()      # the server and the page read this as "watched"
            except OSError:
                pass
            time.sleep(2)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
