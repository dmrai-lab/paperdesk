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
