"""The terminal side of paperdesk: what the person editing the source reads and answers.

    python desk.py watch               KEEP THIS RUNNING when the editor is an agent: one line per new comment, per reply
                                       by the reviewer, per voice note whose words land, and per live comment whose
                                       words a rebuild no longer finds; its heartbeat is what the page and the server
                                       call "watched"
    python desk.py [desk/paperdesk.toml] list [--all]   the open comments (or every one), each with its anchor and quote
    python desk.py list --stale        the live comments whose words are gone from the source, with the commit that took
                                       them out: resolve each, or reply where its point now applies
    python desk.py show ID             one comment in full, with the source lines around its anchor
    python desk.py reply ID "text"     answer it (as the editor named in paperdesk.toml)
    python desk.py resolve ID          mark it done
    python desk.py apply ID [ID ...]   a shortening's edits the author accepted on the page, written into the source
                                       where they stand (each old verbatim, whitespace aside); the rest are left
    python desk.py reanchor            every live comment moved to where its words are in the PDF built now (the server
                                       does this by itself whenever the PDF changes)
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

Whole-paper checks (LaTeX desks):

    python desk.py dedup [--top N]     the claims the paper makes more than once: the N (60) most alike sentences from
                                       different places, and the claims made three times or more, for a reviewer to
                                       judge; also written to dedup.json beside the config, with each side's quote
    python desk.py citestats           citation keys per 1000 words per section against the papers of the field named
                                       in [citations] corpus (arXiv ids; their sources are fetched once)
    python desk.py uncited [--top N]   the uncited sentences the field would have cited, by a classifier learnt from
                                       those papers; also written to uncited.json, with each sentence's quote
    python desk.py check               mechanical consistency: undefined or duplicate labels, unreferenced floats, cite
                                       keys missing from or unused in the .bib, one work under two keys, a symbol given
                                       two values, abstract numbers the body never states, near-duplicate claims with
                                       different numbers, acronyms and spellings; also written to check.json, with
                                       each float's caption beside the sentences that refer to it
    python desk.py refcheck            every .bib entry against the record its DOI (doi.org), arXiv id or title (Crossref)
                                       finds: an unregistered DOI, a work not found, a title, first author or year that
                                       is not the record's; also written to refcheck.json, placed on the first citing line
    python desk.py brevity [--top N]   every site a word or three could go (the colour of the cat, which is, in order
                                       to, ...), the paragraphs whose short last line those sites would pull up (from
                                       the built PDF), and the lines of text per section; all in brevity.json, one
                                       entry per paragraph with its source and sites
    python desk.py brevity --measure F.jsonl  the paper built as it stands and with every edit of F (a --verify'd
                                       file) applied, in scratch copies (the source is never touched): the pages
                                       each way, and a marked copy with the cut words red and the new ones blue
    python desk.py brevity --verify F.jsonl [--flag]   proposed shortenings through the gate: a row's "edits" ([old, new] pairs,
                                       old verbatim) or "suggestion" against its "original"; what drops a citation,
                                       reference, number, maths, negation, quantifier or hedge is refused; the rest go
                                       to F.kept.jsonl, one comment per paragraph listing its edits. --flag: a
                                       negation, quantifier or hedge fewer is not refused but listed in the row's
                                       "flags", edit by edit, for a checker to rule on
    python desk.py readinglist [--min N]   the works N (3) or more of those papers cite that the paper does not
                                       (references identified by DOI, through Crossref when they carry none)
"""
import json
import sys
import time
from pathlib import Path

import config
import store

HERE = Path(__file__).resolve().parent
_ARGS = [a for a in sys.argv[1:] if a.endswith(".toml")]
CONFIG = Path(_ARGS[0]).resolve() if _ARGS else HERE / "paperdesk.toml"
sys.argv = [a for a in sys.argv if not a.endswith(".toml")]
STORE = CONFIG.parent / "comments.jsonl"
_CFG, PAPER, DOCS = config.load(CONFIG)                      # the desk's documents in tab order (one, as before, when [paper] names main alone)
REVIEWER = _CFG.get("people", {}).get("reviewer", "reviewer")
EDITOR = _CFG.get("people", {}).get("editor", "editor")
PDF = DOCS[0].pdf


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
        c = anchor.comment_from_source(PAPER, [(x.name, x.pdf) for x in DOCS], d, d.get("author") or "agent")
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
    if len(DOCS) > 1:                                        # which document the pin is on, when the desk has several
        where = f"[{config.doc_of(DOCS, c).name}] " + where
    sec = a.get("section") or ""
    lab = f" [{a['float']} {a.get('label')}]" if a.get("float") else ""
    q = (c.get("quote") or "").strip().replace("\n", " ")
    q = (q[:90] + "…") if len(q) > 90 else q
    sug = f"\n    suggest: {c['suggestion']}" if c.get("suggestion") else ""
    if c.get("edits"):                                       # a shortening: each edit with what the author decided
        st = c.get("edit_state") or [None] * len(c["edits"])
        flat = lambda s: " ".join(s.split())
        sug = "".join(f"\n    [{s or 'undecided'}] '{flat(o)}' → '{flat(w)}'" for (o, w), s in zip(c["edits"], st))
        return (f"#{c['id']} {c['status']:8s} {where}  {sec}{lab}" + (f"  [{tags}]" if tags else "")
                + f"\n    quote: \"{q}\"" + (f"\n    {c['note']}" if c.get("note") else "") + sug)
    return (f"#{c['id']} {c['status']:8s} {where}  {sec}{lab}" + (f"  [{tags}]" if tags else "")
            + f"\n    quote: \"{q}\"\n    {c['text']}{sug}")


def _cap_threads(n):
    """At most ``n`` threads for the numerical libraries, set before numpy loads: a BLAS left alone starts one thread
    per core, which on a shared machine is everyone's cores."""
    import os
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(k, str(n))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # the tokenizer's own pool, one thread per core otherwise


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd in ("dedup", "citestats", "uncited", "readinglist", "brevity"):
        _cap_threads(int(_CFG.get("citations" if cmd not in ("dedup", "brevity") else "dedup", {}).get("threads", 8)))
    if cmd == "list" and "--stale" in argv:
        import anchor
        for c in load():
            a = c.get("anchor") or {}
            if a.get("stale") and c["status"] in ("open", "proposed"):
                gone = anchor.removed_by(PAPER, a.get("file"), c.get("quote"))
                since = f" since the build of {a['stale']}" if isinstance(a["stale"], str) else ""
                print(line_of(c) + f"\n    stale: {gone or 'its words are gone' + since + '; the commit that cut them was not found'}")
    elif cmd == "list":
        argv = list(argv); who = _opt(argv, "--author")
        want = None if "--all" in argv else ("proposed" if "--proposed" in argv else "open")
        for c in load():
            if (want is None or c["status"] == want) and (who is None or c.get("author") == who):
                print(line_of(c))
                for r in c.get("replies", []):
                    print(f"    ↳ {r['author']}: {r['text']}")
    elif cmd == "apply":
        # the edits the author accepted, and only those, into the source
        import brevity
        for cid in [int(a) for a in argv[1:] if a.isdigit()]:
            with store.locked(STORE) as items:
                c = next((x for x in items if x["id"] == cid), None)
                if c is None or not c.get("edits"):
                    print(f"#{cid}: no shortening"); continue
                st = c.setdefault("edit_state", [None] * len(c["edits"]))
                todo = [k for k, s in enumerate(st) if s == "accept"]
                if not todo:
                    print(f"#{cid}: no accepted edit waiting"); continue
                f = PAPER / c["anchor"]["file"]
                text = f.read_text()
                for k in todo:
                    text, n, miss = brevity.edit_source(text, dict(line=c["anchor"]["line"], edits=[c["edits"][k]]))
                    if n:
                        st[k] = "applied"
                    else:
                        print(f"#{cid} edit {k}: its words are no longer in {c['anchor']['file']}: '{' '.join(miss[0].split())[:80]}'")
                f.write_text(text)
                store.save(STORE, items)
            print(f"#{cid}: {sum(s == 'applied' for s in st)} of {len(st)} edits applied, "
                  f"{sum(s == 'reject' for s in st)} rejected, {sum(s is None for s in st)} undecided")
    elif cmd == "show":
        c = next(x for x in load() if x["id"] == int(argv[1]))
        print(json.dumps({k: v for k, v in c.items() if k != "anchor"}, indent=1, ensure_ascii=False))
        a = c.get("anchor") or {}
        unit = "¶" if a.get("unit") == "paragraph" else ""
        print(f"document: {config.doc_of(DOCS, c).name} page {c.get('page')}")
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
                print(f"#{c['id']} {a['file']}:{a['line']} {c.get('doc', '')} page {c['page']}".replace("  ", " ") + (f"  ({a['error']})" if a.get("error") else ""))
        if refused:
            raise SystemExit(f"{refused} of {len(rows)} refused")
    elif cmd == "reanchor":                                  # every live comment to where its words are in the PDFs built now
        import anchor
        done = {}
        with store.locked(STORE) as items:
            for d in DOCS:
                if not d.pdf.exists():
                    print(f"{d.name}: no PDF built"); continue
                mine = [c for c in items if config.doc_of(DOCS, c) is d]
                moved, stale = anchor.reanchor(PAPER, d.pdf, mine, docs=[(x.name, x.pdf) for x in DOCS if x.pdf.exists()])
                done[d.name] = d.pdf.stat().st_mtime
                print(f"{d.name}: {moved} moved, {stale} newly stale (their words were rewritten; marked on the page)")
            store.save(STORE, items)
        (CONFIG.parent / ".anchored_mtime").write_text(json.dumps(done))
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
        # a new comment, a new reply by the author, a voice note whose words landed, or a live comment whose words a
        # rebuild no longer finds: one line each
        def state(items):
            out = {}
            for c in items:
                if c.get("edits"):                              # a shortening is news once the author accepts an edit
                    acc = [k for k, s in enumerate(c.get("edit_state") or []) if s == "accept"]
                    if acc:
                        out[("e", c["id"])] = ",".join(map(str, acc))
                    continue
                if (c.get("anchor") or {}).get("stale") and c["status"] in ("open", "proposed"):
                    out[("s", c["id"])] = c.get("quote") or ""
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
                    if k[0] == "e":
                        c = next(x for x in items if x["id"] == k[1])
                        print(f"#{k[1]} edits accepted, apply them with desk.py apply {k[1]}: " + line_of(c).replace("\n", " | "), flush=True)
                    elif k[0] == "s":
                        c = next(x for x in items if x["id"] == k[1]); a = c.get("anchor") or {}
                        print(f"#{k[1]} stale: its words are no longer in {a.get('file')} (or anywhere in the paper); "
                              f"resolve it, or reply where its point now applies (desk.py list --stale) | quote: "
                              f"\"{' '.join(text.split())[:90]}\"", flush=True)
                    elif k[0] == "c":
                        c = next(x for x in items if x["id"] == k[1]); print(line_of(c).replace("\n", " | "), flush=True)
                    else:
                        print(f"#{k[1]} reply by {REVIEWER}: {text}", flush=True)
                elif k[0] == "e" and set(text.split(",")) - set(seen[k].split(",")):   # newly accepted, not newly applied
                    c = next(x for x in items if x["id"] == k[1])
                    print(f"#{k[1]} more edits accepted, apply them with desk.py apply {k[1]}: " + line_of(c).replace("\n", " | "), flush=True)
                elif k[0] != "s" and seen[k] != text and not text.startswith("(voice note"):
                    print(f"#{k[1]} {'voice note transcribed' if k[0] == 'c' else 'reply transcribed'}: {text}", flush=True)
            seen = now
            try:
                (CONFIG.parent / "watch.heartbeat").touch()      # the server and the page read this as "watched"
            except OSError:
                pass
            time.sleep(2)
    elif cmd == "dedup":
        # near-duplicate claims, ranked for judgement; dedup.py says how
        import dedup
        argv = list(argv[1:]); top = int(_opt(argv, "--top") or 60)
        opts = _CFG.get("dedup", {})
        res = dedup.find(PAPER, [d.file for d in DOCS if d.file.endswith(".tex")], opts.get("model", dedup.MODEL),
                         CONFIG.parent / ".dedup_cache.npz", top, int(opts.get("threads", 8)))
        dedup.save(res, CONFIG.parent / "dedup.json")
        print(dedup.report(res))
    elif cmd == "check":
        # mechanical consistency; check.py says what is checked
        import check
        found, caps, notes = check.run(PAPER, [d.file for d in DOCS if d.file.endswith(".tex")], CONFIG.parent / "dedup.json")
        check.save(found, caps, CONFIG.parent / "check.json")
        print(check.report(found, caps, notes))
    elif cmd == "brevity":
        # where the paper could be shorter, and the gate for a shorter version; brevity.py says how
        import brevity
        argv = list(argv[1:]); src = _opt(argv, "--verify"); trims = _opt(argv, "--measure")
        if trims:
            rows = [json.loads(l) for l in Path(trims).read_text().splitlines() if l.strip()]
            docs = [(d.name, d.file, d.build) for d in DOCS if d.file.endswith(".tex")]
            if not all(b for _, _, b in docs):
                raise SystemExit("--measure builds the paper: the config needs a build command")
            out = CONFIG.parent / "brevity_build"
            pages, applied, missed = brevity.measure(PAPER, docs, rows, out)
            for f, line, old in missed:
                print(f"NOT APPLIED {f}:{line}  '{' '.join(old.split())[:80]}' (not found in the source as it stands)")
            a, b = pages["as-is"], pages["trimmed"]
            print(f"{applied} edits in {len(rows)} paragraphs applied to a copy; "
                  + "; ".join(f"{k}: {a[k]} -> {b[k]} pages" for k in a)
                  + f". The marked copy (cut red, new blue): {out / 'marked'}")
            return
        if src:
            rows = [json.loads(l) for l in Path(src).read_text().splitlines() if l.strip()]
            kept, dropped = brevity.verify(rows, strict="--flag" not in argv)
            out = Path(src).with_suffix(".kept.jsonl")
            out.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in kept))   # original kept: a second
            #                                                                    pass (a checker, --measure) reads it
            for d in dropped:
                print(f"REFUSED {d.get('file')}:{d.get('line')}  " + "; ".join(d["lost"]))
            print(f"{len(kept)} of {len(rows)} kept ({sum(d['saved'] for d in kept)} characters saved) in {out}")
            return
        import dedup
        import prose
        top = int(_opt(argv, "--top") or 20)
        opts = _CFG.get("brevity", {}); dopts = _CFG.get("dedup", {})
        sents = prose.paper_sentences(PAPER, [d.file for d in DOCS if d.file.endswith(".tex")])
        try:
            emb = dedup.embed([s.text for s in sents], dopts.get("model", dedup.MODEL), CONFIG.parent / ".brevity_cache.npz",
                              int(dopts.get("threads", 8)))
            pairs = brevity.neighbour_candidates(sents, emb, 30)
        except SystemExit as e:
            print(f"neighbours not compared: {e}", file=sys.stderr); pairs = []
        found = brevity.sites(sents)
        holes, (lines, per_page, pages) = brevity.overhangs(PAPER, [(d.name, d.pdf) for d in DOCS if d.file.endswith(".tex")], sents)
        paras = brevity.paragraphs(sents, found, holes)
        lines_by = brevity.budget(sents, lines)
        words = brevity.words_per_section(sents, opts.get("budget"))
        longs = brevity.long_sentences(sents)
        (CONFIG.parent / "brevity.json").write_text(json.dumps(brevity.to_json(paras, found, longs, pairs, lines_by, words),
                                                               indent=1, ensure_ascii=False))
        print(brevity.report(paras, found, longs, pairs, lines_by, words, opts.get("lines_per_page") or per_page, pages, top))
    elif cmd == "refcheck":
        # the bibliography against the records; refcheck.py says how
        import check
        import refcheck
        log = lambda m: print(m, file=sys.stderr)
        roots = [d.file for d in DOCS if d.file.endswith(".tex")]
        found, counts = refcheck.run(PAPER, roots, refcheck.Lookup(CONFIG.parent / ".refcheck.json", log), log)
        (CONFIG.parent / "refcheck.json").write_text(json.dumps([check.asdict(f) for f in found], indent=1, ensure_ascii=False))
        print(refcheck.report(found, counts, len(check.bib_entries(check.Paper(PAPER, roots))[1])))
    elif cmd in ("citestats", "uncited", "readinglist"):
        # how the paper cites against its field; citations.py says how
        import citations
        import prose
        argv = list(argv[1:]); opts = _CFG.get("citations", {})
        ids = (_opt(argv, "--corpus") or "").split(",") if "--corpus" in argv else opts.get("corpus", [])
        if not ids:
            raise SystemExit("name the field's papers: [citations] corpus = [\"arXiv id\", ...] in the config, or --corpus id,id")
        cache = CONFIG.parent / ".citecorpus"
        print(f"the field: {len(ids)} arXiv papers", file=sys.stderr)
        log = lambda m: print(m, file=sys.stderr)
        srcs = citations.sources(ids, cache, log=log)
        field = {k: v[2] for k, v in srcs.items()}
        roots = [d.file for d in DOCS if d.file.endswith(".tex")]
        mine = prose.paper_sentences(PAPER, roots)
        if cmd == "readinglist":
            import references
            res = references.Resolver(cache / "crossref.json", opts.get("mailto"), log)
            refs = {k: references.cited_refs(d, [r]) for k, (d, r, _) in srcs.items()}
            own = references.cited_refs(PAPER, roots)
            log(f"resolving {sum(map(len, refs.values())) + len(own)} references (cached after the first run)")
            try:
                fw = {k: [res(x) for x in v] for k, v in refs.items()}
                mw = [res(x) for x in own]
            finally:
                res.save()
            unresolved = sum(w is None for v in fw.values() for w in v)
            stats = (f"{len(own)} references in the paper ({sum(w is None for w in mw)} unresolved); "
                     f"{sum(map(len, refs.values()))} in {len(refs)} field papers ({unresolved} unresolved, left out)")
            rows, common = references.gap(fw, mw, int(_opt(argv, "--min") or 3))
            in_bib = references.bib_keys(PAPER)
            (CONFIG.parent / "readinglist.json").write_text(json.dumps(references.to_json(rows, common, in_bib), indent=1, ensure_ascii=False))
            print(references.report(rows, common, len(refs), stats, in_bib=in_bib))
        elif cmd == "citestats":
            print(citations.stats_report(citations.profile(mine), citations.norm([citations.profile(v) for v in field.values()]),
                                         len(field)))
        else:
            import dedup
            top = int(_opt(argv, "--top") or 80)
            emb = lambda texts: dedup.embed(texts, opts.get("model", citations.MODEL), cache / "embeddings.npz",
                                            int(opts.get("threads", 8)))
            flagged, auc, thr = citations.uncited(mine, field, emb, top=top)
            (CONFIG.parent / "uncited.json").write_text(json.dumps(citations.uncited_json(flagged), indent=1, ensure_ascii=False))
            print(citations.uncited_report(flagged, auc, thr, sum(bool(s.cites) for s in mine
                                                                 if citations.section_class(s) in citations.SCORED)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
