"""Anchors of a LaTeX paper both ways through SyncTeX.

From the page (a reviewer's click): ``synctex edit`` gives the source file and line of a point. From the source (an
agent reading the ``.tex``): the quoted words are found in the file, and ``synctex view`` gives the page and the
point where their line was typeset. Either way the anchor is the same record -- file, line, section, float, label and
the source lines around it -- so a comment reads the same whoever made it.
"""
import bisect
import re
import subprocess
import time
from pathlib import Path

import store


PROBES = (0.0, 12.0, 24.0)           # points to the right of the one asked about, along the same line


def _synctex_point(paper: Path, pdf: Path, page, x_pt, y_pt):
    try:
        out = subprocess.run(["synctex", "edit", "-o", f"{int(page)}:{float(x_pt):.2f}:{float(y_pt):.2f}:{pdf}"],
                             capture_output=True, text=True, timeout=20, cwd=paper).stdout
    except Exception as e:                                   # noqa: BLE001
        return None, None, f"synctex failed: {e}"
    m_in, m_ln = re.search(r"^Input:(.*)$", out, re.M), re.search(r"^Line:(\d+)$", out, re.M)
    if not m_in or not m_ln:
        return None, None, out[-300:]
    f = (paper / m_in.group(1).strip()).resolve() if not Path(m_in.group(1).strip()).is_absolute() else Path(m_in.group(1).strip()).resolve()
    try:
        f = f.relative_to(paper.resolve())
    except ValueError:
        pass
    return str(f), int(m_ln.group(1)), None


def synctex_edit(paper: Path, pdf: Path, page, x_pt, y_pt):
    """``(file, line, error)`` of the source that typeset the point ``(x, y)``, in TeX points from the page's
    top-left. The file is the one most of three probes along the line agree on (the point and two to its right): the
    record at a line's left margin carries the file being read when the page was shipped out, which after a float is
    the float's file, while the glyphs carry their own; the line comes from the first probe in that file."""
    got = [_synctex_point(paper, pdf, page, x_pt + dx, y_pt) for dx in PROBES]
    files = [f for f, _, _ in got if f is not None]
    if not files:
        return got[0]
    best = max(dict.fromkeys(files), key=files.count)        # most votes; the leftmost probe's file on a tie
    return next(r for r in got if r[0] == best)


def synctex_view(paper: Path, pdf: Path, file, line):
    """``(page, x_pt, y_pt, rect)`` where source ``line`` of ``file`` was typeset, in the same coordinates as
    :func:`synctex_edit`; ``rect`` is the box of the first record (``x, y, w, h``, top-left origin). ``None`` when
    the line typeset nothing (a comment line, a preamble line), in which case the lines below it are tried."""
    for ln in range(int(line), int(line) + 6):
        try:
            out = subprocess.run(["synctex", "view", "-i", f"{ln}:0:{file}", "-o", str(pdf)],
                                 capture_output=True, text=True, timeout=20, cwd=paper).stdout
        except Exception:                                    # noqa: BLE001
            return None
        rec = {}
        for key in ("Page", "x", "y", "h", "v", "W", "H"):
            m = re.search(rf"^{key}:([-\d.]+)$", out, re.M)
            if m:
                rec[key] = float(m.group(1))
        if "Page" in rec and "x" in rec and "y" in rec:
            rect = (dict(x=rec["h"], y=rec["v"] - rec["H"], w=rec["W"], h=rec["H"])
                    if all(k in rec for k in ("h", "v", "W", "H")) and rec["W"] > 0 and rec["H"] > 0 else None)
            return int(rec["Page"]), rec["x"], rec["y"], on_page(rect, page_size(pdf, int(rec["Page"])))
    return None


_SIZES = {}


def page_size(pdf: Path, page):
    """``(width, height)`` of a page in points (``pdfinfo``), None when it cannot be read."""
    key = (str(pdf), pdf.stat().st_mtime if pdf.exists() else None)
    if key not in _SIZES:
        try:
            out = subprocess.run(["pdfinfo", "-f", "1", "-l", "100000", str(pdf)], capture_output=True, text=True, timeout=30).stdout
        except Exception:                                    # noqa: BLE001
            out = ""
        _SIZES[key] = {int(n): (float(w), float(h)) for n, w, h in re.findall(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out, re.M)}
    return _SIZES[key].get(int(page))


def on_page(rect, size):
    """``rect`` cut to the page: the box SyncTeX gives a float's line is the float's, which a full-page float
    extends above the page and a wide one past its edge. None when nothing of it is on the page."""
    if rect is None or size is None:
        return rect
    x0, y0 = max(rect["x"], 0.0), max(rect["y"], 0.0)
    x1, y1 = min(rect["x"] + rect["w"], size[0]), min(rect["y"] + rect["h"], size[1])
    return dict(x=x0, y=y0, w=x1 - x0, h=y1 - y0) if x1 > x0 and y1 > y0 else None


SECTION_RE = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}")
FLOAT_BEGIN = re.compile(r"\\begin\{(figure|table|figure\*|table\*)\}")
FLOAT_END = re.compile(r"\\end\{(figure|table|figure\*|table\*)\}")


def context_of(paper: Path, file, line):
    """The enclosing section path, figure or table label, and the source lines around ``line``."""
    p = paper / file
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
            if FLOAT_BEGIN.search(lines[j]):
                env = FLOAT_BEGIN.search(lines[j]).group(1)
                for k in range(j, min(j + 40, len(lines))):
                    m = re.search(r"\\label\{([^}]*)\}", lines[k])
                    if m:
                        label = m.group(1); break
                    if FLOAT_END.search(lines[k]):
                        break
            elif FLOAT_END.search(lines[j]):
                env = False                                   # the point is after a float, not in it
        if "section" in path and "subsection" in path:
            break
    order = ["section", "subsection", "subsubsection", "paragraph"]
    return dict(section=" > ".join(path[k] for k in order if k in path),
                float=(env if env else None), label=label,
                source=[dict(line=k + 1, text=lines[k]) for k in range(max(i - 2, 0), min(i + 3, len(lines)))])


def find_quote(paper: Path, file, quote, near=None):
    """The line on which ``quote`` starts in ``file``, whitespace and line breaks ignored; of several occurrences
    the one nearest ``near``. Raises ``LookupError`` naming why when the words are not in the file verbatim."""
    p = paper / file
    if not p.is_file():
        raise LookupError(f"no file {file} in {paper}")
    words = quote.split()
    if not words:
        raise LookupError("an empty quote anchors nothing")
    text = p.read_text()
    # the file as one string of single-spaced words, each word's start mapped back to its line
    flat, line_at = [], []
    for n, l in enumerate(text.splitlines(), 1):
        for w in l.split():
            flat.append(w); line_at.append(n)
    joined = " ".join(flat); starts = []
    pos = 0
    for w in flat:
        starts.append(pos); pos += len(w) + 1
    probe = " ".join(words)
    hits = [m.start() for m in re.finditer(re.escape(probe), joined)]
    if not hits:
        raise LookupError(f"quote not found verbatim in {file}: '{probe[:80]}'")
    lines = [line_at[bisect.bisect_right(starts, h) - 1] for h in hits]
    return min(lines, key=lambda l: abs(l - near)) if near else lines[0]


def from_source(paper: Path, pdf: Path, file, quote, line=None):
    """The full anchor of quoted source words: the line they start on (nearest ``line`` when they occur more than
    once), its context, and the page point and box where that line was typeset.
    Returns ``(anchor, page, x_pt, y_pt, rect)``; raises ``LookupError`` when the quote is not in the file."""
    file = str(Path(file))
    at = find_quote(paper, file, quote, line)
    anchor = dict(file=file, line=at, source_anchored=True, **context_of(paper, file, at))
    view = synctex_view(paper, pdf, file, at)
    if view is None:
        anchor["error"] = f"synctex view found no page for {file}:{at} (is {pdf.name} built from this source?)"
        return anchor, None, None, None, None
    page, x, y, rect = view
    return anchor, page, x, y, rect


SEVERITIES = ("error", "major", "minor", "style")


def comment_from_source(paper: Path, pdf, d, author):
    """A comment made from the source, not the page (a review agent's): ``d`` holds ``file``, ``quote`` (the source
    words verbatim), ``text`` and optionally ``line`` (which occurrence), ``severity``, ``category``, ``suggestion``,
    ``edits`` (``[[old, new], ...]``, each decided on its own, with ``note``, ``saved``, ``flags``, the paragraph as
    ``original``, the ``pass`` that proposed them and per edit ``edit_meta``, which the page never shows)
    and ``author``. It is ``proposed``, not ``open``, until accepted. Raises ``LookupError`` or ``ValueError`` when
    it cannot be anchored or is malformed: an agent's comment that names no real words is refused, not kept.

    ``pdf`` is the one document's PDF, or the desk's documents as ``[(name, pdf), ...]`` in tab order: the first
    whose PDF typeset the quoted line is the one the comment lands on, and the record says so in ``doc``."""
    for k in ("file", "quote", "text"):
        if not str(d.get(k) or "").strip():
            raise ValueError(f"a source comment needs '{k}'")
    sev = d.get("severity")
    if sev is not None and sev not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}, not '{sev}'")
    docs = pdf if isinstance(pdf, (list, tuple)) else [(None, pdf)]
    found = None
    for name, one in docs:
        anchor, page, x, y, rect = from_source(paper, one, d["file"], d["quote"], d.get("line"))
        if found is None or page is not None:
            found = (name, anchor, page, x, y, rect)
        if page is not None:
            break
    name, anchor, page, x, y, rect = found
    c = dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), status="proposed", author=d.get("author") or author,
             severity=sev, category=d.get("category"), text=d["text"].strip(), quote=d["quote"].strip(),
             suggestion=d.get("suggestion"), page=page, x_pt=x, y_pt=y, rect=rect, anchor=anchor, replies=[])
    if name is not None:
        c["doc"] = name
    if d.get("edits"):                                       # a shortening: decided edit by edit on the page
        c["edits"] = [[str(o), str(w)] for o, w in d["edits"]]
        for k in ("note", "saved", "flags", "original", "pass", "edit_meta"):
            if d.get(k) is not None:
                c[k] = d[k]
        store.per_edit(c)
    return c


# ----------------------------------------------------------------- re-anchoring after the source changed
def pdf_words(pdf: Path):
    """Every word of the built PDF with its box, in points from the page's top-left (``pdftotext -bbox``):
    ``{page: [(x0, y0, x1, y1, word), ...]}`` in reading order."""
    import html
    out = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, timeout=120).stdout
    # read line by line, not as XML: a PDF's text can carry control characters that make the document ill-formed
    pages, n = {}, 0
    word = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>')
    for line in out.splitlines():
        if "<page " in line:
            n += 1; pages[n] = []
        m = word.search(line)
        if m and n:
            pages[n].append((float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), html.unescape(m.group(5))))
    return pages


def _letters(s):
    """Letters only, lower case: what a selected quote and the PDF's text have in common whatever the line numbers,
    hyphenation, ligature splits and spacing that a selection drags along."""
    return "".join(ch for ch in s.lower() if ch.isalpha())


class _PageText:
    """A page's words as one letters-only string, each letter mapped back to its word."""
    def __init__(self, words):
        self.words = words; chars, owner = [], []
        for k, w in enumerate(words):
            for ch in _letters(w[4]):
                chars.append(ch); owner.append(k)
        self.text = "".join(chars); self.owner = owner

    def hits(self, probe):
        i = self.text.find(probe)
        while i >= 0:
            yield self.owner[i], self.owner[i + len(probe) - 1]
            i = self.text.find(probe, i + 1)


def _place(words, k0, k1):
    """``(x_pt, y_pt, rect)`` of the matched words ``k0..k1``: the pin at the first word's left, mid-height, the mark
    over the matched words of the first line."""
    w0 = words[k0]
    line = [w for w in words[k0:k1 + 1] if abs(w[1] - w0[1]) < 0.6 * (w0[3] - w0[1]) + 1]
    x0 = min(w[0] for w in line); x1 = max(w[2] for w in line); y0 = min(w[1] for w in line); y1 = max(w[3] for w in line)
    return w0[0], (w0[1] + w0[3]) / 2, dict(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def _source_line_now(paper: Path, a):
    """The line the comment's old source line sits on now: its text (``anchor.source``) found in the file, of
    several the nearest the old line number; None when the line itself was rewritten."""
    old = next((s["text"] for s in a.get("source") or [] if s.get("line") == a.get("line")), None)
    p = paper / str(a.get("file") or "")
    if not old or not old.strip() or not p.is_file():
        return None
    lines = p.read_text().splitlines()
    cand = [i + 1 for i, l in enumerate(lines) if l.strip() == old.strip()]
    return min(cand, key=lambda n: abs(n - int(a["line"]))) if cand else None


def _detex(q):
    """A source quote as the page shows it, near enough to search: math, references and citations dropped (the page
    prints a number or a symbol there), a macro's argument kept, the macros themselves and their braces removed."""
    q = re.sub(r"\$[^$]*\$", " ", q)
    q = re.sub(r"\\(?:ref|eqref|cref|Cref|cite[pt]?|citealp|label|url|href)\*?(?:\[[^\]]*\])?\{[^}]*\}", " ", q)
    for _ in range(3):
        q = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", q)
    q = re.sub(r"\\[a-zA-Z]+\*?", " ", q)
    return q.replace("~", " ").replace("{", " ").replace("}", " ")


def _fuzzy_line(paper: Path, file, quote, near, reach=200, accept=0.72):
    """The line a source quote starts on when its words were edited since: of the stretches of the file aligned on
    the quote's longest exact run (difflib), within ``reach`` lines of ``near``, the one most like the quote, if its
    ratio reaches ``accept``; None otherwise. The quote may start anywhere in a line."""
    import difflib
    p = paper / file
    if not p.is_file():
        return None
    lines = p.read_text().splitlines()
    q = " ".join(quote.split())
    flat, line_of = [], []                                   # the file's words single-spaced, each char's line
    lo, hi = max(0, int(near) - reach), min(len(lines), int(near) + reach)
    for n in range(lo, hi):
        t = " ".join(lines[n].split())
        if not t:
            continue
        if flat:
            flat.append(" "); line_of.append(n + 1)
        flat.append(t); line_of.extend([n + 1] * len(t))
    text = "".join(flat)
    if not text:
        return None
    best = (0.0, None)
    step = max(8, len(q) // 3)
    for k in range(0, max(1, len(q) - 12), step):            # anchor on each third of the quote in turn
        piece = q[k:k + max(12, len(q) // 3)]
        at = text.find(piece)
        while at >= 0:
            start = max(0, at - k)
            ratio = difflib.SequenceMatcher(None, q, text[start:start + len(q) + 10], autojunk=False).ratio()
            if ratio > best[0]:
                best = (ratio, line_of[min(start, len(line_of) - 1)])
            at = text.find(piece, at + 1)
    return best[1] if best[0] >= accept else None


def removed_by(paper: Path, file, quote):
    """``"hash date subject"`` of the commit that took ``quote``'s words out of ``file``, when the paper is a git
    repository: tried on windows of six words of its longest plain stretch, since a stretch that crosses a line
    break of the source is not found whole. None when no window is long enough to be told apart or none is found."""
    run = max(re.split(r"\\[a-zA-Z]+|[$~{}\\]", quote or ""), key=len).split()
    windows = [" ".join(run[k:k + 6]) for k in range(0, max(1, len(run) - 5), 3)]
    for w in [x for x in windows if len(x) >= 24][:6]:
        try:
            out = subprocess.run(["git", "log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M", "-S", w,
                                  "--", str(file)], capture_output=True, text=True, timeout=30, cwd=paper).stdout.strip()
        except Exception:                                    # noqa: BLE001
            return None
        if out:
            return out
    return None


def _elsewhere(paper: Path, c, docs, words_of):
    """Where a comment's words are when its own file and document no longer hold them: the quote verbatim in another
    source file, typeset in one of ``docs`` (``[(name, pdf), ...]``), else its letters in another document's PDF.
    ``(name, page, x, y, rect, file, line)`` or None."""
    q = c.get("quote") or ""
    if len(" ".join(q.split())) >= 30:              # long enough not to be a phrase the paper repeats
        for f in sorted(paper.rglob("*.tex")):
            rel = str(f.relative_to(paper))
            try:
                line = find_quote(paper, rel, q)
            except LookupError:
                continue
            for name, pdf in docs:
                view = synctex_view(paper, pdf, rel, line)
                if view:
                    return (name, view[0], view[1], view[2], view[3], rel, line)
    probe = _letters(q)
    if len(probe) >= 24:
        for name, pdf in docs:
            if name == (c.get("doc") or docs[0][0]):
                continue                                     # its own document was searched already
            words = words_of(name, pdf)
            for n, w in words.items():
                hit = next(iter(_PageText(w).hits(probe)), None)
                if hit:
                    x, y, rect = _place(w, *hit)
                    f, line, _ = synctex_edit(paper, pdf, n, x + 1, y)
                    return (name, n, x, y, rect, f, line)
    return None


def reanchor(paper: Path, pdf: Path, comments, words=None, docs=None):
    """Move every live comment (open or proposed) to where its words are in the PDF built now, and refresh its
    source anchor. Two routes, the source one authoritative where it answers:

    * the source: a quote that occurs verbatim in the anchored file (an agent's, copied from the source) gives its
      line directly, nearest the old line; without a quote, the old line's own text found again;
    * the PDF: the quote letters-only in the new PDF (a reviewer's, copied from the page with its line numbers and
      hyphens), of several hits the one nearest where the source route or the old position puts it; when the whole
      quote is gone, its first or last 24 letters within two pages of there.

    The pin goes where the PDF route finds the words, else where the source line is typeset; the anchor is the
    source route's line, else what SyncTeX says of the new point. A comment neither route finds is looked for in every
    source file and in the desk's other documents (``docs``, ``[(name, pdf), ...]``), and moves there with its
    ``doc``; one found nowhere keeps its place and is marked ``anchor["stale"]`` with the time of the build its words
    were last missing from: they were rewritten or cut. Returns ``(moved, stale)``."""
    words = words or pdf_words(pdf)
    docs = docs or []
    loaded = {str(pdf): words}

    def words_of(name, one):
        if str(one) not in loaded:
            loaded[str(one)] = pdf_words(one)
        return loaded[str(one)]
    pages = {n: _PageText(w) for n, w in words.items()}
    moved = stale = 0

    def nearest(probe, ref_page, ref_y, within=None):
        best = None
        for n, pt in pages.items():
            if within is not None and abs(n - ref_page) > within:
                continue
            for k0, k1 in pt.hits(probe):
                d = abs(n - ref_page) * 2000 + abs(pt.words[k0][1] - ref_y)
                if best is None or d < best[0]:
                    best = (d, n, k0, k1)
        return best

    for c in comments:
        if c.get("status") not in ("open", "proposed") or c.get("page") is None:
            continue
        a = dict(c.get("anchor") or {})
        old = (int(c["page"]), float(c.get("x_pt") or 0), float(c.get("y_pt") or 0))
        src_line = None
        if a.get("file") and a.get("line"):
            if c.get("quote"):
                try:
                    src_line = find_quote(paper, a["file"], c["quote"], int(a["line"]))
                except LookupError:
                    src_line = _fuzzy_line(paper, a["file"], c["quote"], int(a["line"])) if len(c["quote"]) >= 30 else None
            if src_line is None and not c.get("quote"):
                src_line = _source_line_now(paper, a)
        view = synctex_view(paper, pdf, a["file"], src_line) if src_line else None
        ref_page, ref_y = (view[0], view[2]) if view else (old[0], old[2])
        target = None
        q = c.get("quote") or ""
        probe = _letters(q)
        if len(probe) >= 3:
            best = nearest(probe, ref_page, ref_y)
            # a source quote: as the page prints it, then its longest stretch of plain words
            chunks = [_letters(x) for x in re.split(r"\$[^$]*\$|\\(?:ref|eqref|cite[pt]?|citealp)\{[^}]*\}", q)]
            for cand in ([_letters(_detex(q))] + sorted(chunks, key=len, reverse=True)[:1]) if best is None and ("\\" in q or "$" in q) else []:
                if best is None and len(cand) >= 12:
                    best = nearest(cand, ref_page, ref_y, within=None if view else 2)
            if best is None and len(probe) >= 40:              # an edited sentence: its opening words, else its source line
                for part in (probe[:24], _letters(_detex(q))[:24]):
                    if best is None and len(part) >= 16:
                        best = nearest(part, ref_page, ref_y, within=2)
                if best is None and not view and len(probe[-24:]) >= 16:
                    best = nearest(probe[-24:], ref_page, ref_y, within=2)
            if best:
                _, n, k0, k1 = best
                x, y, rect = _place(words[n], k0, k1)
                target = (n, x, y, rect)
        if target is None and view:
            target = (view[0], view[1], view[2], view[3])
        if target is None and docs:
            found = _elsewhere(paper, c, docs, words_of)
            if found:
                name, n, x, y, rect, f, ln = found
                c["doc"], c["page"], c["x_pt"], c["y_pt"] = name, n, x, y
                c["rect"] = rect
                a.pop("stale", None)
                if f is not None:
                    a.update(file=f, line=ln, **context_of(paper, f, ln))
                c["anchor"] = a; moved += 1
                continue
        if target is None:
            if not a.get("stale"):
                a["stale"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(pdf.stat().st_mtime)) if pdf.exists() else True
                stale += 1
            c["anchor"] = a
            continue
        n, x, y, rect = target
        moved += (n != old[0]) or abs(y - old[2]) > 0.5 or abs(x - old[1]) > 0.5
        c["page"], c["x_pt"], c["y_pt"] = n, x, y
        if rect is not None:
            c["rect"] = rect
        a.pop("stale", None)
        if src_line:
            a.update(file=a["file"], line=src_line, **context_of(paper, a["file"], src_line))
        else:
            f, ln, err = synctex_edit(paper, pdf, n, x + 1, y)
            if f is not None:
                a.update(file=f, line=ln, **context_of(paper, f, ln))
        c["anchor"] = a
    return moved, stale
