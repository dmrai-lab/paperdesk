"""Mechanical consistency of a LaTeX paper: what a reader trips on and a build does not always say.

Cross-references: a ``\\ref`` to a label no document defines (a document sees another's labels only when it loads them
with ``\\externaldocument``, as xr does), a label defined twice, a figure or table the text never refers to.
Citations: a key cited that no ``.bib`` defines, an entry defined that nothing cites (often a citation lost in a
rewrite), a key defined twice, one work under two keys (the same DOI or title).
Numbers: a symbol given different values in inline maths (``$K = 256$`` here, ``$K = 128$`` there), a number of the
abstract that the rest of the paper never states (to its rounding), and the near-duplicate claims of ``dedup.json``
whose two sides state different numbers in one unit.
Terms: an acronym defined twice in the body (with different expansions or not), used before its definition or never
after it, and a word spelt two ways (hyphenated and not, -ise and -ize).

All of it is recall for whoever judges: two values of one symbol may belong to two experiments. Each finding names a
place and carries a quote ``desk.py add`` finds there; findings on a ``.bib`` carry none, since a ``.bib`` is not
typeset. For the judgement no rule makes (an abstract claiming more than the results, a caption disagreeing with its
text) ``check.json`` also holds each float's caption beside the sentences that refer to it.
"""
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import prose
import references


@dataclass
class Finding:
    kind: str
    severity: str
    text: str
    file: str = ""
    line: int = 0
    quote: str = ""              # verbatim at file:line, for ``desk.py add``; empty for a .bib
    also: list = field(default_factory=list)     # further places, "file:line"


LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
REF_RE = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref|nameref|vref|Vref|cpageref|Cpageref|labelcref|subref)\*?"
                    r"\{([^}]+)\}|\\hyperref\[([^\]]+)\]")
CITE_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*|nocite)\*?(?:\s*\[[^\]]*\])*\s*\{([^}]*)\}")
EXTERNAL_RE = re.compile(r"\\externaldocument(?:\[([^\]]*)\])?\{([^}]+)\}")
BIBRES_RE = re.compile(r"\\(?:addbibresource(?:\[[^\]]*\])?|bibliography)\{([^}]+)\}")
FLOAT_RE = re.compile(r"\\begin\{((?:sideways|wrap)?(?:figure|table)\*?)\}.*?\\end\{\1\}", re.S)


def _text(paper, f):
    return prose._mask_comments((paper / f).read_text(errors="ignore"))


def _line(t, i):
    return t.count("\n", 0, i) + 1


class Paper:
    """The documents' files and source, read once."""
    def __init__(self, paper: Path, roots):
        self.dir = paper; self.roots = [str(r) for r in roots]
        self.files = {r: prose.tree(paper, [r]) for r in self.roots}
        self.text = {f: _text(paper, f) for fs in self.files.values() for f in fs}

    def scan(self, pattern, files=None):
        for f in files or self.text:
            t = self.text[f]
            for m in pattern.finditer(t):
                yield f, _line(t, m.start()), m


# ----------------------------------------------------------------- cross-references
def _labels(p, files):
    out = {}
    for f, n, m in p.scan(LABEL_RE, files):
        out.setdefault(m.group(1).strip(), []).append((f, n))
    return out


def _aux_labels(path):
    t = path.read_text(errors="ignore") if path.is_file() else ""
    return {m.group(1): [(path.name, 0)] for m in re.finditer(r"\\newlabel\{([^}]+)\}", t)}


def crossrefs(p: Paper):
    out = []
    own = {r: _labels(p, fs) for r, fs in p.files.items()}
    referenced = set()
    for r, fs in p.files.items():
        visible = {k: list(v) for k, v in own[r].items()}
        for f, n, m in p.scan(EXTERNAL_RE, fs):
            prefix, name = m.group(1) or "", m.group(2).strip()
            other = next((x for x in p.roots if Path(x).with_suffix("").as_posix() == Path(name).with_suffix("").as_posix()), None)
            labels = own[other] if other else _aux_labels(p.dir / (Path(name).with_suffix(".aux")))
            for k, v in labels.items():
                visible.setdefault(prefix + k, []).extend(v)
        for k, v in own[r].items():
            if len(v) > 1:
                out.append(Finding("duplicate-label", "major", f"label {k} is defined {len(v)} times; every \\ref to it "
                                   "lands on the last", v[0][0], v[0][1], f"\\label{{{k}}}", [f"{a}:{b}" for a, b in v[1:]]))
        for f, n, m in p.scan(REF_RE, fs):
            for k in (m.group(1) or m.group(2)).split(","):
                k = k.strip(); referenced.add(k)
                if k not in visible:
                    out.append(Finding("undefined-ref", "error", f"\\ref to {k}, which no document this one reads defines "
                                       "(it prints ??)", f, n, m.group(0)))
    for f, n, m in p.scan(FLOAT_RE):
        labels = LABEL_RE.findall(m.group(0))
        if labels and not any(k.strip() in referenced or any(x.endswith(k.strip()) for x in referenced) for k in labels):
            k = labels[-1]
            out.append(Finding("unreferenced-float", "minor", f"{m.group(1).rstrip('*')} {k} is never referred to in "
                               "the text", f, n + m.group(0)[:m.group(0).index("\\label{" + k)].count("\n"), f"\\label{{{k}}}"))
    return out


# ----------------------------------------------------------------- citations
def bib_files(p: Paper):
    out = []
    for r in p.roots:
        for f, n, m in p.scan(BIBRES_RE, p.files[r]):
            for b in m.group(1).split(","):
                b = b.strip(); b = b if b.endswith(".bib") else b + ".bib"
                if (p.dir / b).is_file() and b not in out:
                    out.append(b)
    return out or sorted(x.relative_to(p.dir).as_posix() for x in p.dir.rglob("*.bib"))


def bib_entries(p: Paper):
    """``[(bibfile, line, key)]`` of every entry, and ``{key: Ref}``."""
    pos, refs = [], {}
    for b in bib_files(p):
        t = (p.dir / b).read_text(errors="ignore")
        for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", t):
            if m.group(1).lower() not in ("string", "comment", "preamble"):
                pos.append((b, _line(t, m.start()), m.group(2)))
        refs.update(references._bibfile(t))
    return pos, refs


def citations(p: Paper):
    out = []
    pos, refs = bib_entries(p)
    keys = {}
    for b, n, k in pos:
        keys.setdefault(k, []).append((b, n))
    cited = {}
    for f, n, m in p.scan(CITE_RE):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                cited.setdefault(k, []).append((f, n, m.group(0)))
    everything = "*" in cited
    for k, sites in cited.items():
        if k != "*" and k not in keys:
            f, n, q = sites[0]
            out.append(Finding("missing-key", "error", f"cites {k}, which no .bib defines (it prints as the bare key)",
                               f, n, q, [f"{a}:{b}" for a, b, _ in sites[1:]]))
    for k, v in keys.items():
        if len(v) > 1:
            out.append(Finding("duplicate-key", "major", f".bib key {k} is defined {len(v)} times", v[0][0], v[0][1],
                               also=[f"{a}:{b}" for a, b in v[1:]]))
        elif k not in cited and not everything:
            out.append(Finding("unused-entry", "minor", f"{k} is in the .bib and cited nowhere (a citation lost in a "
                               f"rewrite?): {refs[k].text[:120] if k in refs else ''}", v[0][0], v[0][1]))
    same = {}
    for k, r in refs.items():
        for ident in {"doi:" + r.doi if r.doi else None, references._title_id(r.title)} - {None}:
            same.setdefault(ident, set()).add(k)
    told = set()
    for ident, ks in same.items():
        if len(ks) > 1 and tuple(sorted(ks)) not in told:
            ks = sorted(ks); told.add(tuple(ks)); b, n = keys[ks[0]][0]
            out.append(Finding("same-work", "major", f"one work under {len(ks)} keys ({', '.join(ks)}; the same "
                               f"{'DOI' if ident.startswith('doi:') else 'title'}): the reference list prints it twice "
                               "if both are cited, and splits its citations",
                               b, n, also=[":".join(map(str, keys[k][0])) for k in ks[1:]]))
    return out


# ----------------------------------------------------------------- numbers
_IDENT = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref|label|url|href|includegraphics|input|include|[a-zA-Z]*cite[a-zA-Z]*)"
                    r"\*?(?:\[[^\]]*\])*\{[^}]*\}")
NUM_RE = re.compile(r"(?<![\w.^_{])(\d+(?:\.\d+)?)(?:e(-?\d+))?(?:[\s-]*(%|[A-Za-z][A-Za-z/]*))?")
_WORDS = dict(two=2, three=3, four=4, five=5, six=6, seven=7, eight=8, nine=9, ten=10, eleven=11, twelve=12, twenty=20,
              thirty=30, fifty=50, hundred=100, thousand=1000, million=10**6, billion=10**9)
_NOT_UNITS = set("and or to of on at in the a an as for by with from per than is are was were be it its this that "
                 "these those times x vs which where when while into over under".split())
_UNITS = {"percent": "%", "minute": "min", "second": "s", "millisecond": "ms", "microsecond": "us", "hour": "h"}


def _unit(u):
    u = u.lower()
    u = u[:-1] if len(u) > 3 and u.endswith("s") and not u.endswith("ss") else u
    return "" if u in _NOT_UNITS else _UNITS.get(u, u)


@dataclass(frozen=True)
class Number:
    value: float
    decimals: int        # digits after the point as written: what the number claims
    unit: str
    raw: str


def _plain_numbers(s):
    s = _IDENT.sub(" ", s)
    s = re.sub(r"\\(?:SI|qty)\{([^}]*)\}\{([^}]*)\}", r"\1 \2", s)
    s = re.sub(r"\\num\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:mathrm|text|textrm|mathit|operatorname|unit|si)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\times|\\cdot", " x ", s)
    s = re.sub(r"\\%", "%", s)
    s = re.sub(r"\\[,;:! ]|~|\$", " ", s)
    s = re.sub(r"\\(?:mu|upmu)\b\s*|[µμ]\s*", "u", s)     # 2~$\mu$m is 2 um
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)               # 10,000
    s = re.sub(r"(\d+(?:\.\d+)?)\s*x\s*10\^\{?(-?\d+)\}?", r"\1e\2", s)
    s = re.sub(r"(?<![\w.])10\^\{?(-?\d+)\}?", r"1e\1", s)
    s = re.sub(r"\bone(?=[\s-]+per\s?cent)", "1", s, flags=re.I)  # numbers in words: from two up, and one percent
    s = re.sub(r"\b(" + "|".join(_WORDS) + r")\b", lambda m: str(_WORDS[m.group(1).lower()]), s, flags=re.I)
    s = re.sub(r"per cent", "percent", s)
    return s


def numbers(src):
    """The numbers of a stretch of source, each with the word or symbol after it as its unit; a year, a section or a
    cross-reference is not a number of the paper's."""
    out = []
    for m in NUM_RE.finditer(_plain_numbers(src)):
        v = float(m.group(1)) * (10 ** int(m.group(2)) if m.group(2) else 1)
        unit = _unit(m.group(3) or "")
        if not m.group(2) and "." not in m.group(1) and 1900 <= v <= 2100 and unit in ("", "s", "and", "to"):
            continue                                         # a year
        dec = len(m.group(1).split(".")[1]) if "." in m.group(1) else 0
        out.append(Number(v, dec if not m.group(2) else 99, unit, re.sub(r"\s+", " ", m.group(0).strip())))
    return out


def _states(n: Number, values):
    """Whether one of ``values`` is ``n`` to the precision ``n`` is written with."""
    if n.decimals == 99:
        return any(math.isclose(v, n.value, rel_tol=1e-9) for v in values)
    tol = 0.5 * 10 ** -n.decimals
    return any(abs(v - n.value) <= tol + 1e-12 for v in values)


def abstract_numbers(p: Paper, sents):
    """Numbers of the first document's front matter (its abstract) that nothing else in the paper states."""
    front = [s for s in sents if not s.section and not s.appendix]
    rest = dict(p.text)
    for s in front:
        if s.src.strip():
            rest[s.file] = rest[s.file].replace(s.src.strip(), " ", 1)
    stated = [x for t in rest.values() for x in numbers(t)]
    by_unit = {}
    for x in stated:
        by_unit.setdefault(x.unit, []).append(x.value)
    out = []
    for s in front:
        for n in numbers(s.src):
            pool = by_unit.get(n.unit, []) if n.unit else [x.value for x in stated]
            if (n.unit or n.decimals or n.value >= 10) and not _states(n, pool):
                out.append(Finding("abstract-number", "major", f"the abstract states {n.raw}, which the rest of the "
                                   "paper never does in that unit and to that rounding", s.file, s.line, s.quote))
    return out


MAX_VALUES = 3


def symbol_values(p: Paper, sents):
    """``$SYMBOL = value$`` in prose, grouped by symbol, where one symbol takes two or three values (more is a sweep
    of a parameter, not a drift)."""
    by = {}
    for s in sents:
        for m in re.finditer(r"(?<!\\)\$([^$]+)\$([^$]{0,15})", s.src):
            parts = re.split(r"=|\\approx|\\simeq", m.group(1))
            if len(parts) != 2:
                continue
            sym = re.sub(r"\\(?:mathrm|text|mathit)\{([^}]*)\}", r"\1", parts[0])
            sym = re.sub(r"[\s{}]", "", sym)
            if not sym or len(sym) > 20 or re.search(r"\d\s*$", sym):
                continue
            rhs = _plain_numbers(parts[1]).strip()
            if not re.fullmatch(r"-?\d+(?:\.\d+)?(?:e-?\d+)?\s*(?:%|[A-Za-z]{1,4}(?:/[A-Za-z0-9^]{1,6})?)?", rhs):
                continue                                     # a value, not an expression
            ns = numbers(parts[1] + " " + m.group(2))
            if not ns:
                continue
            by.setdefault(sym, []).append((ns[0], s))
    out = []
    for sym, uses in by.items():
        vals = {}
        for n, s in uses:
            vals.setdefault((n.value, n.unit), []).append(s)
        if 1 < len(vals) <= MAX_VALUES:
            first = uses[0][1]
            listing = "; ".join(f"{k[0]:g}{(' ' + k[1]) if k[1] else ''} at "
                                + ", ".join(f"{x.file}:{x.line}" for x in v) for k, v in vals.items())
            out.append(Finding("symbol-values", "minor", f"${sym}$ takes {len(vals)} values: {listing}", first.file,
                               first.line, first.quote))
    return sorted(out, key=lambda f: f.text.count(";"))


def duplicate_numbers(sents, dedup_json: Path):
    """The pairs of ``dedup.json`` whose two sides state different numbers in a unit they share."""
    if not dedup_json.is_file():
        return None
    at = {(s.file, s.line): i for i, s in enumerate(sents)}

    def side(d):
        i = at.get((d["file"], d["line"]))
        if i is None:
            return []
        span = [sents[i]]
        if i + 1 < len(sents) and len(d["text"]) > len(sents[i].text) + 5:
            span.append(sents[i + 1])
        return [n for s in span for n in numbers(s.src)]
    out = []
    for pair in json.loads(dedup_json.read_text()).get("pairs", []):
        a, b = side(pair["a"]), side(pair["b"])
        differ = []
        for u in sorted(({n.unit for n in a} & {n.unit for n in b}) - {""}):
            va = [n for n in a if n.unit == u]; vb = [n for n in b if n.unit == u]
            only_a = [n for n in va if not _states(n, [x.value for x in vb]) and not any(_states(x, [n.value]) for x in vb)]
            only_b = [n for n in vb if not _states(n, [x.value for x in va]) and not any(_states(x, [n.value]) for x in va)]
            if only_a and only_b:
                differ.append(f"{', '.join(n.raw for n in only_a)} here against {', '.join(n.raw for n in only_b)}")
        if differ:
            out.append(Finding("duplicate-numbers", "minor", f"near-duplicate claims (dedup rank {pair['rank']}) with "
                               f"different numbers: {'; '.join(differ)} at {pair['b']['file']}:{pair['b']['line']}",
                               pair["a"]["file"], pair["a"]["line"], pair["a"]["quote"],
                               [f"{pair['b']['file']}:{pair['b']['line']}"]))
    return out


# ----------------------------------------------------------------- terms
def _expansion(short, words):
    """The words before an acronym that spell it (Schwartz and Hearst's rule), or ``None``."""
    long = " ".join(words)
    s, l = short.lower(), long.lower()
    i, j = len(s) - 1, len(l) - 1
    while i >= 0:
        c = s[i]
        if not c.isalnum():
            i -= 1; continue
        while j >= 0 and (l[j] != c or (i == 0 and j > 0 and l[j - 1].isalnum())):
            j -= 1
        if j < 0:
            return None
        i -= 1; j -= 1
    return long[long.rfind(" ", 0, j + 1) + 1:]


ACRO_RE = re.compile(r"\(([A-Z][A-Za-z0-9-]*[A-Z0-9][A-Za-z0-9-]*?)s?\)")


def acronyms(docs):
    """``docs``: one sentence list per document. In each document's body (front matter defines its own)."""
    out = []
    for sents in docs:
        body = [s for s in sents if s.section]
        defs, uses = {}, {}
        for i, s in enumerate(body):
            for m in ACRO_RE.finditer(s.text):
                a = m.group(1)
                if sum(c.isupper() for c in a) < 2:
                    continue
                words = s.text[:m.start()].split()[-(min(len(a) + 5, 2 * len(a))):]
                e = _expansion(a, words)
                if e:
                    defs.setdefault(a.upper(), []).append((i, e, s, m.start(1)))
        for a in defs:
            spelt = "|".join(sorted({re.escape(d[2].text[d[3]:d[3] + len(a)]) for d in defs[a]}))  # as each definition writes it
            for i, s in enumerate(body):
                for m in re.finditer(r"(?<![\w-])(?:" + spelt + r")s?(?![\w-])", s.text):
                    if not (m.start() > 0 and s.text[m.start() - 1] == "(" and any(d[0] == i for d in defs[a])):
                        uses.setdefault(a, []).append((i, s))
        for a, ds in defs.items():
            i0, e0, s0, _ = ds[0]
            u = uses.get(a, [])
            if len(ds) > 1:
                exps = {re.sub(r"[\s-]+", " ", d[1].lower()) for d in ds}
                out.append(Finding("acronym-twice", "minor", f"{a} is defined {len(ds)} times in the body"
                                   + (f", as {' / '.join(sorted(exps))}" if len(exps) > 1 else ""), ds[1][2].file,
                                   ds[1][2].line, ds[1][2].quote, [f"{s0.file}:{s0.line}"] + [f"{d[2].file}:{d[2].line}" for d in ds[2:]]))
            before = [x for x in u if x[0] < i0]
            if before:
                s = before[0][1]
                out.append(Finding("acronym-before", "minor", f"{a} is used before its definition at {s0.file}:{s0.line}",
                                   s.file, s.line, s.quote))
            if not [x for x in u if x[0] > i0]:
                out.append(Finding("acronym-unused", "style", f"{a} ({e0}) is defined and never used again",
                                   s0.file, s0.line, s0.quote))
    return out


_ZE = re.compile(r"(\w{3,}?)(y|i)z(e|ed|es|ing|ation|ations|er|ers)$")


def spellings(sents):
    """Words written two ways: hyphenated and spaced or joined, and -ise against -ize."""
    words = {}
    where = {}
    for s in sents:
        for w in re.findall(r"[a-z]+(?:-[a-z]+)*", s.text.lower()):
            words[w] = words.get(w, 0) + 1; where.setdefault(w, s)
    corpus = " " + " ".join(re.sub(r"[^a-z\- ]", " ", s.text.lower()) for s in sents) + " "
    out = []
    for w, n in words.items():
        if "-" in w and w.count("-") == 1:
            a, b = w.split("-")
            spaced = len(re.findall(rf"(?<=\s){a}\s+{b}(?=\s)", corpus)); joined = words.get(a + b, 0)
            if spaced or joined:
                other = f"'{a} {b}' {spaced}x" if spaced >= joined else f"'{a}{b}' {joined}x"
                s = where[w]
                out.append(Finding("spelling", "style", f"'{w}' {n}x and {other}", s.file, s.line, s.quote))
        m = _ZE.match(w)
        if m and m.group(1) + m.group(2) + "s" + m.group(3) in words:
            s = where[w]
            out.append(Finding("spelling", "style", f"'{w}' {n}x and '{m.group(1)}{m.group(2)}s{m.group(3)}' "
                               f"{words[m.group(1) + m.group(2) + 's' + m.group(3)]}x", s.file, s.line, s.quote))
    return out


# ----------------------------------------------------------------- captions beside their text
def captions(p: Paper, sents):
    """Each labelled float's caption and the sentences that refer to it: material for the judgement no rule makes."""
    out = []
    for f, n, m in p.scan(FLOAT_RE):
        body = m.group(0); labels = [k.strip() for k in LABEL_RE.findall(body)]
        i = body.find("\\caption")
        if not labels or i < 0:
            continue
        j = body.index("{", i)
        cap, _ = references._braced(body, j)
        refs = [s for s in sents if any(re.search(r"\{[^}]*\b" + re.escape(k) + r"\b[^}]*\}", s.src) for k in labels)]
        out.append(dict(label=labels[-1], file=f, line=n + body[:i].count("\n"), caption=prose.clean(cap),
                        referred=[dict(file=s.file, line=s.line, quote=s.quote, text=s.text) for s in refs]))
    return out


# ----------------------------------------------------------------- the whole check
SECTIONS = (("cross-references", ("undefined-ref", "duplicate-label", "unreferenced-float")),
            ("citations", ("missing-key", "duplicate-key", "same-work", "unused-entry")),
            ("numbers", ("abstract-number", "duplicate-numbers", "symbol-values")),
            ("terms", ("acronym-twice", "acronym-before", "acronym-unused", "spelling")))


def run(paper: Path, roots, dedup_json: Path = None):
    """``(findings, captions, notes)`` for the paper the root documents reach."""
    p = Paper(paper, roots)
    sents = prose.paper_sentences(paper, p.roots)
    per_doc = [prose.paper_sentences(paper, [r]) for r in p.roots]
    found = crossrefs(p) + citations(p) + abstract_numbers(p, sents) + symbol_values(p, sents)
    notes = []
    dup = duplicate_numbers(sents, dedup_json) if dedup_json else None
    if dup is None:
        notes.append("no dedup.json: run desk.py dedup first and the near-duplicate claims are checked for numbers too")
    found += (dup or []) + acronyms(per_doc) + spellings(sents)
    return found, captions(p, sents), notes


def report(found, caps, notes, width=160):
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    out = []
    for title, kinds in SECTIONS:
        rows = [f for f in found if f.kind in kinds]
        out.append(f"{title}: {len(rows)}")
        for f in sorted(rows, key=lambda f: kinds.index(f.kind)):
            more = f"  (also {', '.join(f.also[:4])}{' …' if len(f.also) > 4 else ''})" if f.also else ""
            out.append(f"  {f.severity:5s} {f.file}:{f.line}  {cut(f.text)}{more}")
    out.append(f"\n{len(caps)} captions with the sentences that refer to them in check.json, for a reader to compare")
    return "\n".join(out + notes)


def save(found, caps, path: Path):
    path.write_text(json.dumps(dict(findings=[asdict(f) for f in found], captions=caps), indent=1, ensure_ascii=False))
