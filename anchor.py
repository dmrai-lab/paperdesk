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


def synctex_edit(paper: Path, pdf: Path, page, x_pt, y_pt):
    """``(file, line, error)`` of the source that typeset the point ``(x, y)``, in TeX points from the page's
    top-left."""
    try:
        out = subprocess.run(["synctex", "edit", "-o", f"{int(page)}:{float(x_pt):.2f}:{float(y_pt):.2f}:{pdf}"],
                             capture_output=True, text=True, timeout=20, cwd=paper).stdout
    except Exception as e:                                   # noqa: BLE001
        return None, None, f"synctex failed: {e}"
    m_in, m_ln = re.search(r"^Input:(.*)$", out, re.M), re.search(r"^Line:(\d+)$", out, re.M)
    if not m_in or not m_ln:
        return None, None, out[-300:]
    f = Path(m_in.group(1).strip())
    try:
        f = f.resolve().relative_to(paper)
    except ValueError:
        pass
    return str(f), int(m_ln.group(1)), None


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
            return int(rec["Page"]), rec["x"], rec["y"], rect
    return None


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


def comment_from_source(paper: Path, pdf: Path, d, author):
    """A comment made from the source, not the page (a review agent's): ``d`` holds ``file``, ``quote`` (the source
    words verbatim), ``text`` and optionally ``line`` (which occurrence), ``severity``, ``category``, ``suggestion``
    and ``author``. It is ``proposed``, not ``open``, until accepted. Raises ``LookupError`` or ``ValueError`` when
    it cannot be anchored or is malformed: an agent's comment that names no real words is refused, not kept."""
    for k in ("file", "quote", "text"):
        if not str(d.get(k) or "").strip():
            raise ValueError(f"a source comment needs '{k}'")
    sev = d.get("severity")
    if sev is not None and sev not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}, not '{sev}'")
    anchor, page, x, y, rect = from_source(paper, pdf, d["file"], d["quote"], d.get("line"))
    return dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), status="proposed", author=d.get("author") or author,
                severity=sev, category=d.get("category"), text=d["text"].strip(), quote=d["quote"].strip(),
                suggestion=d.get("suggestion"), page=page, x_pt=x, y_pt=y, rect=rect, anchor=anchor, replies=[])


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


def reanchor(paper: Path, pdf: Path, comments, words=None):
    """Move every live comment (open or proposed) to where its words are in the PDF built now, and refresh its
    source anchor. Two routes, the source one authoritative where it answers:

    * the source: a quote that occurs verbatim in the anchored file (an agent's, copied from the source) gives its
      line directly, nearest the old line; without a quote, the old line's own text found again;
    * the PDF: the quote letters-only in the new PDF (a reviewer's, copied from the page with its line numbers and
      hyphens), of several hits the one nearest where the source route or the old position puts it; when the whole
      quote is gone, its first or last 24 letters within two pages of there.

    The pin goes where the PDF route finds the words, else where the source line is typeset; the anchor is the
    source route's line, else what SyncTeX says of the new point. A comment neither route finds keeps its place and
    is marked ``anchor["stale"]``: the words it commented on were rewritten. Returns ``(moved, stale)``."""
    words = words or pdf_words(pdf)
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
            for cand in ([_letters(_detex(q))] + sorted(chunks, key=len, reverse=True)[:1]) if best is None and "\\" in q or "$" in q else []:
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
        if target is None:
            if not a.get("stale"):
                a["stale"] = True; stale += 1
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
