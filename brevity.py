"""Where the paper could say the same in fewer words, where a few words buy a line, and a gate that keeps a shorter
version from saying less.

Recall, without a model, at three scales.

Sites: every place a copy-editor would cut a word or three, each with the characters it roughly saves: the noun
behind "of" (the colour of the cat: the cat's colour), "all of the", a relative clause that only carries "is" (the
walkers that are near the wall), an opener that delays the subject (it is ... that, there are ... that), the stock
phrases with a short form (in order to, in the case of, is given by, a total of), a weak verb before a noun made of a
verb (perform an evaluation of), signposting, stacked hedges, a passive that names its agent. Every site is listed,
not a top few: one site saves little, and many of them save pages.

Lines: a paragraph whose last line holds a word or two loses that line to a trim of as many characters anywhere in
it. The built PDF's lines (``pdftotext -bbox-layout``) give each paragraph's last line, SyncTeX the source it came
from, and the paragraph's sites tell whether they cover it. ``budget`` adds up the lines of text per section, so the
page count has an address.

Sentences: those of 40 words or more, which often carry two claims; adjacent sentences that say much the same, and a
paragraph's last sentence close to its first (embeddings, centred on the paper's mean as in :mod:`dedup`).

The gate, for a rewrite an agent proposes: everything the original carries that a reader can check must survive it,
the citation keys, the cross-references, the numbers with their units, the inline maths, the emphasised terms, the
negations and the quantifiers and hedges (only, every, at most, approximately, may: the words a shortening drops and
the meaning needs), and it must be shorter. Whether the claims are the same both ways is the agent's judgement; the
gate is the part no judgement should be trusted with.
"""
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import check
import citations
import prose

W = r"[A-Za-z][\w'-]*"
# (name, pattern on the sentence's words, the move, characters it roughly saves)
SITES = [
    ("of-the", rf"\b(?:the|a|an)\s+(?:{W}\s+)?{W}\s+of\s+(?:the|a|an|its|their|each|every)\s+{W}",
     "the noun behind 'of' as a possessive or modifier (the colour of the cat: the cat's colour)", 6),
    ("all-of-the", r"\b(?:all|both|each|none|most|many|some|any|half) of the\b", "'all the', 'both', 'each'", 4),
    ("whiz", rf"\b(?:that|which|who) (?:is|are|was|were) (?!not\b)(?:{W}ed|{W}en|in|on|at|from|of|with|near|between|"
     rf"for|by|under|over|within|above|below|available|able|possible|needed|necessary|due)\b",
     "the relative clause that only carries 'is' (the walkers that are near: the walkers near)", 9),
    ("delayed-subject", rf"^(?:it|there)\s+(?:is|are|was|were)\b|\bthere\s+(?:is|are|was|were)\s+(?:{W}\s+){{1,3}}(?:that|which|who)\b"
     rf"|\bit\s+(?:is|was)\s+{W}\s+(?:that|to)\b", "the subject first (there are walkers that cross: some walkers cross)", 9),
    ("stock", r"\b(?:in order to|so as to|in the case of|in the presence of|in the absence of|in the form of|in the context "
     r"of|with respect to|with regard to|in terms of|due to the fact that|the fact that|a total of|a number of|the use of|"
     r"by means of|by the use of|with the help of|for the purpose of|is able to|are able to|has the ability to|is given by|"
     r"are given by|is equal to|are equal to|is shown in|are shown in|is defined as|as well as|in addition to|in addition|"
     r"at the same time|in this case|in each case|on the other hand|prior to|subsequent to|it can be seen that|note that|"
     r"it is worth noting that|it should be noted that|as a result|for this reason|in particular|the value of|the set of|"
     r"one another|is in agreement with|are in agreement with|is consistent with|make use of|makes use of|"
     r"take into account|takes into account|in which|whether or not|the same as|respectively)\b",
     "the stock phrase's short form (in order to: to; is given by: is; in the case of: for)", 7),
    ("weak-verb", r"\b(?:perform|performs|performed|performing|make|makes|made|carry out|carries out|carried out|conduct|"
     r"conducts|conducted|provide|provides|provided|give|gives|gave|achieve|achieves|undertake|undertakes|undertaken)\s+"
     rf"(?:a|an|the)?\s*{W}(?:tion|ment|ance|ence|sis|ison|al)s?\s+(?:of|on|for|to|between)\b",
     "the verb inside the noun (perform an evaluation of: evaluate)", 10),
    ("nominal", rf"\b(?:the|a|an)\s+{W}(?:tion|ment|ance|ence)\s+of\b", "the verb inside the noun (the estimation of X: estimating X)", 5),
    ("be-adjective", r"\b(?:is|are|was|were|be|being|been)\s+(?:dependent|independent|indicative|reflective|suggestive|"
     r"capable|in agreement|responsible)\s+(?:on|of|with|for|to)\b", "a verb for the state (is dependent on: depends on)", 6),
    ("agent-passive", rf"\b(?:is|are|was|were|be|been|being)\s+(?:{W}ly\s+)?{W}(?:ed|en)\s+by\s+(?:the|a|an|its|each)\b",
     "the agent as the subject", 4),
    ("hedge-stack", r"\b(?:may|might|could|possibly|potentially|perhaps|likely|seems?|appears?|somewhat|suggests?)\b"
     r"(?:\s+\S+){0,3}\s+\b(?:may|might|could|possibly|potentially|perhaps|likely|seem|appear|somewhat|suggest)\w*\b",
     "one hedge where two stand", 8),
    ("signpost", r"\b(?:in this (?:section|paper|work|subsection|appendix|chapter)|as (?:mentioned|discussed|noted|shown|"
     r"described|stated) (?:above|earlier|before|previously)|in the following|below we|here we|we now turn)\b",
     "the signpost cut: the heading or the reference already says it", 12),
]
LONG = 40                            # words: a sentence this long often carries two claims
_COMPILED = [(n, re.compile(p, re.I), why, gain) for n, p, why, gain in SITES]


@dataclass
class Site:
    name: str
    words: str                       # the matched words
    move: str
    saves: int                       # characters, roughly
    sentence: prose.Sentence


@dataclass
class Candidate:
    kind: str                        # "neighbours" or "summary"
    score: float
    sentences: list                  # two prose.Sentence


def sites(sents):
    """Every site in every sentence, in reading order."""
    return [Site(n, m.group(0), why, gain, s) for s in sents for n, rx, why, gain in _COMPILED for m in rx.finditer(s.text)]


def long_sentences(sents):
    return [s for s in sents if len(s.text.split()) >= LONG]


# ----------------------------------------------------------------- the built page
@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def pdf_blocks(pdf: Path):
    """``[[Line, ...], ...]``: the PDF's text blocks (paragraph-like) with their lines, read by ``pdftotext -bbox-layout``
    line by line (a PDF's text can carry characters that make the XML ill-formed)."""
    import html
    out = subprocess.run(["pdftotext", "-bbox-layout", str(pdf), "-"], capture_output=True, text=True, timeout=300).stdout
    box = r'xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)"'
    blocks, page, line, words = [], 0, None, []
    for raw in out.splitlines():
        if "<page " in raw:
            page += 1
        elif "<block " in raw:
            blocks.append([])
        elif m := re.search(r"<line " + box, raw):
            line = [float(g) for g in m.groups()]; words = []
        elif m := re.search(r"<word [^>]*>(.*?)</word>", raw):
            words.append(html.unescape(m.group(1)))
        elif "</line>" in raw and line and blocks:
            blocks[-1].append(Line(page, *line, " ".join(words)))
    return [b for b in blocks if b]


def _columns(blocks):
    """``{page: (left, right)}``: the text column, from the lines of five words or more."""
    by = {}
    for b in blocks:
        for l in b:
            if len(l.text.split()) >= 5:
                by.setdefault(l.page, []).append(l)
    out = {}
    for p, ls in by.items():
        xs0, xs1 = sorted(l.x0 for l in ls), sorted(l.x1 for l in ls)
        out[p] = (xs0[len(xs0) // 10], xs1[len(xs1) * 9 // 10])
    return out


def _paragraph_of(sents):
    """A function from a source ``(file, line)`` to the paragraph ``(file, para)`` whose lines hold it, or ``None``
    (a display, a float, a table: not prose)."""
    span = {}
    for s in sents:
        a, b = span.get((s.file, s.para), (s.line, s.line))
        span[(s.file, s.para)] = (min(a, s.line), max(b, s.line + s.src.strip().count("\n")))
    by = {}
    for (f, para), (a, b) in span.items():
        by.setdefault(f, []).append((a, b, para))

    def find(file, line):
        return next(((file, para) for a, b, para in by.get(file, []) if a <= line <= b), None)
    return find


def _words_of(text):
    return re.findall(r"[a-z]{3,}", text.lower())


def _holds(paragraph_words, line_text, share=0.7):
    """Whether a printed line's words stand in the paragraph: SyncTeX sometimes traces a line to a neighbouring
    float or paragraph, and the words decide."""
    w = _words_of(line_text)
    return not w or sum(x in paragraph_words for x in w) >= share * len(w)


def _prose_like(text):
    letters = sum(c.isalnum() for c in text)
    return letters >= 0.6 * len(text.replace(" ", "")) and not re.search(r"[=|{}∑∫]", text)


@dataclass
class Overhang:
    doc: str
    page: int
    file: str
    para: int
    last: str                        # the words on the paragraph's last line
    needs: int                       # characters to cut for the line to go
    fill: float                      # the last line's share of the column


def overhangs(paper: Path, docs, sents, fill=0.95, workers=4):
    """Every paragraph's last line, when it fills less than ``fill`` of the column: a paragraph loses that line to a
    trim of as many characters as the line holds. ``docs``: ``[(name, pdf), ...]``. Every line of the text column is
    traced to its source by SyncTeX and kept only when its words stand in the paragraph it traces to, so a
    paragraph's last line is the one whose next traced line belongs to another paragraph (margin line numbers,
    displays, floats and page furniture trace to none and are skipped). Also
    ``(lines, per_page)``: ``{(file, para): lines}`` and the prose lines a full page holds."""
    import anchor
    from concurrent.futures import ThreadPoolExecutor
    find = _paragraph_of(sents)
    words = {}
    for s in sents:
        words.setdefault((s.file, s.para), set()).update(_words_of(s.text))
    out, lines, counts, pages = [], {}, [], {}
    for name, pdf in docs:
        if not pdf.exists():
            continue
        cols = _columns(blocks := pdf_blocks(pdf))
        pages[name] = [max(l.page for b in blocks for l in b), 0]
        col = sorted((l for b in blocks for l in b if l.page in cols and l.x0 >= cols[l.page][0] - 3),
                     key=lambda l: (l.page, round(l.y0), l.x0))
        trace = lambda l: anchor.synctex_edit(paper, pdf, l.page, l.x0 + 1, (l.y0 + l.y1) / 2)
        with ThreadPoolExecutor(workers) as ex:
            src = list(ex.map(trace, col))
        paras = [find(f, n) if f else None for f, n, _ in src]
        paras = [p if p and _holds(words.get(p, set()), l.text) else None for p, l in zip(paras, col)]
        per = {}
        for i, (l, par) in enumerate(zip(col, paras)):
            if not par:
                continue
            per[l.page] = per.get(l.page, 0) + 1
            lines[par] = lines.get(par, 0) + 1
            pages[name][1] += 1
            left, right = cols[l.page]
            share = (l.x1 - l.x0) / max(1.0, right - left)
            nxt = next((q for q in paras[i + 1:] if q), None)
            if nxt != par and share < fill and abs(l.x0 - left) <= 0.02 * (right - left) and _prose_like(l.text):
                out.append(Overhang(name, l.page, par[0], par[1], l.text, len(l.text) + 1, round(share, 2)))
        counts += list(per.values())
    per_page = sorted(counts)[len(counts) * 9 // 10] if counts else None
    return out, (lines, per_page, {k: (n, round(m / per_page, 1) if per_page else None) for k, (n, m) in pages.items()})


def budget(sents, lines):
    """``{class: lines}``: the lines of text each section class takes on the page."""
    cls = {}
    for s in sents:
        cls.setdefault((s.file, s.para), citations.section_class(s))
    out = {}
    for par, n in lines.items():
        c = cls.get(par, "other")
        out[c] = out.get(c, 0) + n
    return out


def neighbour_candidates(sents, emb, top=30):
    """Adjacent sentences of one paragraph that say much the same, and a paragraph's last sentence close to its first;
    ``emb`` one unit-norm row per sentence. The cosine is taken after removing the paper's mean, as in :mod:`dedup`."""
    import numpy as np
    e = emb - emb.mean(0)
    e /= np.linalg.norm(e, axis=1, keepdims=True)
    out = []
    paras = {}
    for i, s in enumerate(sents):
        paras.setdefault((s.file, s.para), []).append(i)
    for idx in paras.values():
        for a, b in zip(idx, idx[1:]):
            out.append(Candidate("neighbours", float(e[a] @ e[b]), [sents[a], sents[b]]))
        if len(idx) >= 3:
            out.append(Candidate("summary", float(e[idx[0]] @ e[idx[-1]]), [sents[idx[0]], sents[idx[-1]]]))
    return sorted(out, key=lambda c: -c.score)[:top]


def words_per_section(sents, budget=None):
    """``{class: (words, budget or None)}`` in reading order of the classes."""
    out = {}
    for s in sents:
        c = citations.section_class(s)
        out[c] = out.get(c, 0) + len(s.text.split())
    budget = budget or {}
    return {c: (n, budget.get(c)) for c, n in out.items()}


# ----------------------------------------------------------------- the gate
NEGATIONS = r"\b(?:not|no|never|neither|nor|none|nothing|without|cannot|can't|don't|doesn't|isn't|aren't|won't|unless)\b"
SCOPE = (r"\b(?:only|every|each|all|any|always|at most|at least|up to|below|above|within|exactly|approximately|about|"
         r"roughly|nearly|almost|may|might|could|must|should|per|both|either|unless|except|until|once)\b")


def _keys(rx, src):
    return sorted(k.strip() for m in rx.finditer(src) for k in (m.group(1) or m.group(2) or "").split(",") if k.strip())


def _maths(src):
    return sorted(re.sub(r"\s+", "", m.group(1)) for m in re.finditer(r"(?<!\\)\$([^$]+)\$", src))


def _count(rx, text):
    return {w.lower(): len(re.findall(r"\b" + re.escape(w) + r"\b", text, re.I))
            for w in set(x.lower() for x in re.findall(rx, text, re.I))}


def gate(original, rewrite, strict=True):
    """What the rewrite loses of the original (both LaTeX source), as a list of reasons; empty when it may stand.
    The citations, cross-references, numbers, inline maths and emphasised terms are refused mechanically; with
    ``strict`` so are a negation and a quantifier or hedge fewer, which otherwise are :func:`flags` for a reader to
    rule on (a sentence that restates another may carry its "only" away)."""
    lost = []
    for name, rx in (("citation", check.CITE_RE), ("cross-reference", check.REF_RE)):
        a, b = _keys(rx, original), _keys(rx, rewrite)
        if sorted(set(a)) != sorted(set(b)):
            lost.append(f"{name}s differ: {sorted(set(a) - set(b))} dropped, {sorted(set(b) - set(a))} added")
    na, nb = check.numbers(original), check.numbers(rewrite)
    gone = [n.raw for n in na if not any(abs(x.value - n.value) <= 1e-12 * max(1, abs(n.value)) and (x.unit == n.unit or not n.unit) for x in nb)]
    if gone:
        lost.append(f"numbers dropped: {', '.join(gone)}")
    ma, mb = _maths(original), _maths(rewrite)
    if [m for m in ma if m not in mb]:
        lost.append(f"inline maths dropped: {', '.join('$' + m + '$' for m in ma if m not in mb)}")
    emph = re.findall(r"\\(?:emph|textit|textbf)\{([^}]*)\}", original)
    if [t for t in emph if t not in rewrite]:
        lost.append(f"emphasised terms dropped: {', '.join(t for t in emph if t not in rewrite)}")
    ta, tb = prose.clean(original), prose.clean(rewrite)
    if len(tb) >= len(ta):
        lost.append(f"not shorter ({len(ta)} characters to {len(tb)})")
    return lost + (flags(original, rewrite) if strict else [])


def flags(original, rewrite):
    """The negations, quantifiers and hedges ``rewrite`` has fewer of than ``original``: what a cut or a rewrite may
    drop with its meaning intact (a repeated "only") or change it with (a lost "only"); a reader decides."""
    lost = []
    ta, tb = prose.clean(original), prose.clean(rewrite)
    na_, nb_ = sum(_count(NEGATIONS, ta).values()), sum(_count(NEGATIONS, tb).values())
    if nb_ < na_ - len(re.findall(r"\bnor\b", ta, re.I)):          # neither ... nor may become not ... or
        lost.append(f"negation dropped ({na_} in the original, {nb_} in the rewrite)")
    ca, cb = _count(SCOPE, ta), _count(SCOPE, tb)
    short = [w for w, n in ca.items() if cb.get(w, 0) < n]
    if short:
        lost.append(f"quantifier or hedge dropped: {', '.join(short)}")
    return lost


def _balanced(s):
    """Whether a span closes every brace group and inline maths it opens: an edit that cuts through one breaks the
    build whatever it says."""
    depth = 0
    for ch in re.sub(r"\\[{}$%]", "", s):
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False
    return depth == 0 and re.sub(r"\\\$", "", s).count("$") % 2 == 0


def apply(original, edits):
    """The original with each ``(old, new)`` replaced once, or ``None`` and the edit whose ``old`` is not in it."""
    out = original
    for old, new in edits:
        k = re.sub(r"\s+", " ", out).find(re.sub(r"\s+", " ", old))
        if k < 0 or old not in out:
            return None, old
        out = out.replace(old, new, 1)
    return out, None


def verify(rows, strict=True):
    """Each proposed shortening through :func:`gate`: ``(kept, dropped)``. A row carries ``original`` (LaTeX source)
    and either ``suggestion`` (the rewrite) or ``edits`` (``[[old, new], ...]``, each ``old`` verbatim in the
    original, applied in turn); a kept row gets ``saved`` (characters) and its ``text`` ends with what it saves,
    with the edits listed when there are edits (after the row's ``note``, so a kept row verified again reads the same),
    and a dropped one gets ``lost``. Not ``strict``, a negation or a quantifier fewer does not refuse a row: each
    edit that drops one is listed in the row's ``flags`` for a checker to rule on, and its comment says so."""
    kept, dropped = [], []
    for d in rows:
        rewrite = d.get("suggestion")
        if d.get("edits"):
            torn = [o for o, w in d["edits"] if not (_balanced(o) and _balanced(w))]
            if torn:
                dropped.append(dict(d, lost=[f"edit cuts through a brace group or maths: '{torn[0]}'"])); continue
            rewrite, bad = apply(d["original"], d["edits"])
            if rewrite is None:
                dropped.append(dict(d, lost=[f"edit not in the source: '{bad}'"])); continue
        lost = gate(d["original"], rewrite, strict=strict)
        if lost:
            dropped.append(dict(d, lost=lost)); continue
        if not strict and d.get("edits"):
            d = dict(d, flags=[[o, f] for o, w in d["edits"] if (f := flags(o, w))])
        n = len(prose.clean(d["original"])) - len(prose.clean(rewrite))
        line = (f"; the last line holds {d['needs']}" if d.get("needs") else "")
        if d.get("edits"):
            flat = lambda s: re.sub(r"\s+", " ", s).strip()
            flagged = {o: f for o, f in d.get("flags") or []}
            body = "; ".join((f"'{flat(o)}' → '{flat(w)}'" if w.strip() else f"cut '{flat(o)}'")
                             + (f" [{'; '.join(flagged[o])}: checked]" if o in flagged else "") for o, w in d["edits"])
            text = ((d.get("note") or "").rstrip() + " " if d.get("note") else "") + f"{body} ({n} characters fewer{line})"
            kept.append({**{k: v for k, v in d.items() if k not in ("suggestion",)}, "saved": n, "text": text})
        else:
            kept.append(dict(d, saved=n, text=(d.get("text") or "Shorter, the same claims.").rstrip() + f" ({n} characters fewer{line})"))
    return kept, dropped


def _span(original, old):
    """Where ``old`` stands in the original, as ``(start, end)``, or None."""
    k = original.find(old)
    return (k, k + len(old)) if k >= 0 else None


def blind(trimmed, checked, share=0.1, seed=0, name=None):
    """The rows to post: each paragraph's edits the checker kept, and among them, unmarked, a random sample of the ones
    it dropped, about ``share`` of all the edits posted. The author's decisions on the dropped ones are the only way
    to learn whether the checker is too strict. ``trimmed`` are the trimmer's verified rows, ``checked`` the checker's,
    whose ``judged`` holds ``{old, verdict, why}`` per edit and whose ``edits`` are the ones it kept (a paragraph
    the checker left out lost every edit). Each row's edits stand in reading order with ``edit_meta`` beside them,
    ``{checker: keep|drop, why}``, which the page never shows; ``name`` labels the pass."""
    import random
    key = lambda r: (r["file"], r.get("line"), r["quote"])
    judged = {key(r): r for r in checked}
    rows, pool, kept_n = [], [], 0
    for r in trimmed:
        c = judged.get(key(r)) or {}
        why = {j["old"]: j for j in c.get("judged") or []}
        keep = {o for o, _ in c.get("edits") or []}
        edits = [(o, w, dict(checker="keep", why=(why.get(o) or {}).get("why"))) for o, w in r["edits"] if o in keep]
        kept_n += len(edits)
        row = dict({k: v for k, v in r.items() if k not in ("judged", "edit_meta", "lost")}, edits=edits)
        rows.append(row)
        pool += [(row, (o, w, dict(checker="drop", why=(why.get(o) or {}).get("why")))) for o, w in r["edits"] if o not in keep]
    want = round(share * kept_n / (1 - share)) if share < 1 else len(pool)
    random.Random(seed).shuffle(pool)
    taken = 0
    for row, e in pool:
        if taken >= want:
            break
        spans = [_span(row["original"], o) for o, _, _ in row["edits"]]
        mine = _span(row["original"], e[0])
        if mine and all(x and (x[1] <= mine[0] or mine[1] <= x[0]) for x in spans):
            row["edits"].append(e); taken += 1
    out = []
    for row in rows:
        if not row["edits"]:
            continue
        row["edits"].sort(key=lambda e: _span(row["original"], e[0])[0])
        out.append(dict(row, edits=[[o, w] for o, w, _ in row["edits"]], edit_meta=[m for _, _, m in row["edits"]],
                        **({"pass": name} if name else {})))
    kept, dropped = verify(out, strict=False)
    return kept, dropped, taken


# ----------------------------------------------------------------- measuring: the trimmed paper, built
MACROS = (r"\providecommand{\brevcut}[1]{{\ifdefined\color\color{red!75!black}\fi[#1]}\,}"
          r"\providecommand{\brevnew}[1]{{\ifdefined\color\color{blue!70!black}\fi#1}}" "\n")   # after \documentclass:
#                                                                  a preamble may hold caption text already
COLOUR = "\\usepackage{xcolor}\n"                                  # before \begin{document}, after the paper's own


def build_inputs(paper: Path, roots):
    """The files a build of ``roots`` reads that live in the paper's folder (the ``.fls`` latexmk leaves), with the
    bibliographies and the roots themselves: what a scratch copy needs, not the folder's experiments and notes."""
    want = set(roots)
    for r in roots:
        fls = paper / (Path(r).stem + ".fls")
        for line in (fls.read_text(errors="replace").splitlines() if fls.exists() else []):
            if line.startswith("INPUT "):
                f = Path(line[6:].strip())
                f = f if f.is_absolute() else paper / f
                try:
                    want.add(str(f.resolve().relative_to(paper.resolve())))
                except ValueError:
                    pass                                     # the TeX distribution's own files
    want |= {f.name for f in paper.glob("*.bib")} | {f.name for f in paper.glob("*.bbl")}
    return sorted(w for w in want if (paper / w).is_file())


def edit_source(text, row, mark=False):
    """``text`` (a source file) with the row's edits applied where they stand, each ``old`` matched whitespace-blind
    and, of several matches, the one nearest the row's line. Returns ``(text, applied, missed)``; with ``mark`` the
    cut words stay, red, and the new ones are blue."""
    applied, missed = 0, []
    for old, new in row.get("edits") or []:
        rx = re.compile(r"\s+".join(re.escape(w) for w in old.split()))
        hits = list(rx.finditer(text))
        if not old.split() or not hits:
            missed.append(old); continue
        m = min(hits, key=lambda h: abs(text.count("\n", 0, h.start()) + 1 - int(row.get("line") or 0)))
        rep_ = (r"\brevcut{" + m.group(0) + "}" + (r"\brevnew{" + new + "}" if new.strip() else "")) if mark else new
        text = text[:m.start()] + rep_ + text[m.end():]
        applied += 1
    return text, applied, missed


def _near(text, words, line, reach):
    """How often ``words`` stand in ``text`` (whitespace-blind) within ``reach`` lines of ``line``."""
    if not words.split():
        return 0
    rx = re.compile(r"\s+".join(re.escape(w) for w in words.split()))
    return sum(abs(text.count("\n", 0, h.start()) + 1 - int(line or 0)) <= reach for h in rx.finditer(text))


def undone(text, line, old, new, original=None, reach=40):
    """Whether an applied edit has been undone in the source: its old words stand near ``line`` as often as they did in
    the paragraph before (``original``; once, without it), not counting those inside its new words. An edit the
    author accepted and later reverted by hand is a rejection the page never saw."""
    flat = lambda s: " ".join(s.split())
    before = max(1, flat(original).count(flat(old))) if original else 1
    inside = _near(text, new, line, reach) * flat(new).count(flat(old)) if flat(old) in flat(new) else 0
    return _near(text, old, line, reach) - inside >= before


def pdf_pages(pdf: Path):
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def measure(paper: Path, docs, rows, out: Path, log=print):
    """Build the paper three ways in scratch copies under ``out`` (``docs``: ``[(name, root .tex, build command),
    ...]``, built in order): as it stands, with every row's edits applied, and marked (cut words
    red, new ones blue). The source is never touched. Returns ``{variant: {name: pages}}``, the edits applied and
    those whose words were not found, and leaves ``out/marked/<root>.pdf`` to read."""
    import shutil
    roots = [r for _, r, _ in docs]
    files = build_inputs(paper, roots)
    by_file = {}
    for d in rows:
        by_file.setdefault(d["file"], []).append(d)
    pages, applied, missed = {}, 0, []
    for variant in ("as-is", "trimmed", "marked"):
        dst = out / variant
        if dst.exists():
            shutil.rmtree(dst)
        for f in files:
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paper / f, dst / f)
        if variant != "as-is":
            for f, rs in by_file.items():
                text = (dst / f).read_text()
                for d in rs:
                    text, n, miss = edit_source(text, d, mark=variant == "marked")
                    if variant == "trimmed":
                        applied += n; missed += [(f, d.get("line"), m) for m in miss]
                (dst / f).write_text(text)
            if variant == "marked":
                for r in roots:
                    src = (dst / r).read_text()
                    src = re.sub(r"(\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[^\n]*\n)", lambda m: m.group(1) + MACROS, src, count=1)
                    (dst / r).write_text(src.replace(r"\begin{document}", COLOUR + r"\begin{document}", 1))
        pages[variant] = {}
        for name, r, build in docs:
            res = subprocess.run(build, shell=True, cwd=dst, capture_output=True, text=True)
            pdf = dst / (Path(r).stem + ".pdf")
            pages[variant][name] = pdf_pages(pdf) if res.returncode == 0 and pdf.exists() else None
            if res.returncode != 0:
                log(f"{variant} {r}: the build failed; its log is {dst / (Path(r).stem + '.log')}")
        log(f"{variant}: " + ", ".join(f"{k} {v} pages" for k, v in pages[variant].items()))
    return pages, applied, missed


# ----------------------------------------------------------------- output
def paragraphs(sents, found, holes):
    """Every paragraph of the paper in reading order, each a candidate however its last line falls: its source, its
    section, its sites (hints, not the limit), and ``needs``, the characters its last line holds where that was
    measured (a trim of that many pulls the paragraph up by a line)."""
    by = {}
    for s in sents:
        by.setdefault((s.file, s.para), []).append(s)
    sites_of = {}
    for x in found:
        sites_of.setdefault((x.sentence.file, x.sentence.para), []).append(x)
    hole = {(h.file, h.para): h for h in holes}
    out = []
    for key in by:                                           # insertion order: the reading order of the sentences
        ss = by[key]
        xs, h = sites_of.get(key, []), hole.get(key)
        est = sum(x.saves for x in xs)
        out.append(dict(file=key[0], para=key[1], line=ss[0].line, quote=ss[0].quote, section=ss[0].section,
                        words=sum(len(s.text.split()) for s in ss),
                        original=" ".join(s.src.strip() for s in ss),
                        page=h.page if h else None, doc=h.doc if h else None, last=h.last if h else None,
                        needs=h.needs if h else None, estimate=est,
                        sites=[dict(line=x.sentence.line, name=x.name, words=x.words, move=x.move, saves=x.saves) for x in xs]))
    return out


def to_json(paras, found, longs, pairs, lines_by, words):
    return dict(budget=dict(lines=lines_by, words={k: dict(words=v[0], budget=v[1]) for k, v in words.items()}),
                paragraphs=paras,
                long=[dict(file=s.file, line=s.line, quote=s.quote, words=len(s.text.split()), original=s.src.strip()) for s in longs],
                pairs=[dict(kind=c.kind, cosine=round(c.score, 3), a=_side(c.sentences[0]), b=_side(c.sentences[1])) for c in pairs])


def _side(s):
    return dict(file=s.file, line=s.line, quote=s.quote, text=s.text, original=s.src.strip())


def report(paras, found, longs, pairs, lines_by, words, per_page=None, pages=None, top=20, width=90):
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    out = []
    total = sum(lines_by.values())
    for name, (n, prose_pages) in (pages or {}).items():
        out.append(f"{name}: {n} pages, of which prose about {prose_pages}; the rest is figures, tables, displays and references")
    out.append(f"\nlines of prose per section ({total} in all, {per_page or '?'} to a full page):")
    for c, n in sorted(lines_by.items(), key=lambda kv: -kv[1]):
        w = words.get(c, (None, None))
        out.append(f"  {c:13s}{n:6d} lines" + (f"  {w[0]:6d} words" if w[0] else "") + (f"  budget {w[1]}" if w[1] else ""))
    holes = sorted((d for d in paras if d["needs"]), key=lambda d: d["needs"])
    for k in (15, 30, 50):
        out.append(f"  a line for at most {k:2d} characters cut: {sum(d['needs'] <= k for d in holes):4d} paragraphs")
    kinds = {}
    for x in found:
        kinds[x.name] = kinds.get(x.name, 0) + 1
    out.append(f"{len(paras)} paragraphs ({sum(d['words'] for d in paras)} words), {len(holes)} ends measured; {len(found)} sites: "
               + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
               + f"; {len(longs)} sentences of {LONG} words or more")
    out.append(f"\nthe cheapest lines (of {len(holes)}):")
    for d in holes[:top]:
        out.append(f"  {d['needs']:3d} chars  {d['file']}:{d['line']}  {d['doc']} p{d['page']}  last line '{cut(d['last'])}'"
                   + (f"  sites: {', '.join(sorted({x['name'] for x in d['sites']}))}" if d["sites"] else ""))
    if pairs:
        out.append(f"\nneighbours that read alike (top {min(5, len(pairs))} of {len(pairs)}):")
        for c in pairs[:5]:
            a, b = c.sentences
            out.append(f"  {c.kind:10s} cos {c.score:.2f}  {a.file}:{a.line} / {b.line}")
    return "\n".join(out)
