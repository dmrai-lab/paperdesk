"""The field's reading list against the paper's: the works most of the field cites that the paper does not.

A paper's references are what it cites, read from what its build left or its source holds, in this order: the
``.bbl`` of a root document (biblatex ``\\entry`` records or ``\\bibitem`` lists), a ``thebibliography`` written into
the source, or the ``.bib`` files restricted to the keys the source cites. Each reference becomes a string (authors,
title, venue, year) and its DOI when it carries one.

A work is identified by its DOI. A reference without one is matched by Crossref's bibliographic search
(``query.bibliographic``, made for free-text references), and the match is kept only when the words of the title
Crossref returns stand in the reference string itself; otherwise the reference stays unresolved and out of the
comparison, and the count of those is reported. Answers are cached in ``.citecorpus/crossref.json``. Only the
corpus's reference strings and the paper's own bibliography go to Crossref, never the paper's text.

:func:`gap` ranks the works by how many corpus papers cite them, keeping those the paper does not cite.
"""
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import prose

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s{}\"'<>,;]+")


@dataclass
class Ref:
    key: str
    text: str                # authors, title, venue, year as plain text
    doi: str = None
    title: str = None        # when the record names it (a .bib or biblatex entry)


@dataclass
class Work:
    id: str                  # doi:... or title:...
    title: str
    year: str = ""
    first_author: str = ""
    venue: str = ""


def _doi(s):
    m = DOI_RE.search(s or "")
    return m.group(0).rstrip(".").lower() if m else None


def _plain(s):
    s = re.sub(r"\\(?:newblock|penalty0|bibinfo\{[^}]*\}|natexlab\{[^}]*\})", " ", s)
    s = re.sub(r"\\(?:url|doi|href)\{[^}]*\}(\{[^}]*\})?", " ", s)
    return prose.clean(s)


# ----------------------------------------------------------------- reading a paper's references
def _braced(s, i):
    """The content of the brace group opening at ``s[i]`` and the index after it."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)


def _biblatex(bbl):
    out = []
    for m in re.finditer(r"\\entry\{([^}]*)\}\{[^}]*\}\{[^}]*\}(.*?)\\endentry", bbl, re.S):
        key, body = m.group(1), m.group(2)
        field = lambda name: (re.search(r"\\field\{" + name + r"\}\{((?:[^{}]|\{[^{}]*\})*)\}", body) or [None, None])[1]
        fam = re.findall(r"family=\{([^}]*)\}", body)
        doi = re.search(r"\\verb\{doi\}\s*\\verb\s*(\S+)\s*\\endverb", body)
        title = _plain(field("title") or "")
        venue = _plain(field("journaltitle") or field("booktitle") or "")
        text = ", ".join(fam[:6]) + ". " + title + ". " + venue + " " + (field("year") or "")
        out.append(Ref(key, text.strip(), (doi.group(1).lower() if doi else None), title or None))
    return out


def _bibitems(src):
    out = []
    parts = re.split(r"\\bibitem\s*(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?\s*\{([^}]*)\}", src)
    for key, body in zip(parts[1::2], parts[2::2]):
        body = body.split("\\end{thebibliography}")[0]
        out.append(Ref(key, _plain(body)[:400], _doi(body)))
    return out


def _bibfile(text):
    out = {}
    i = 0
    while True:
        m = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,").search(text, i)
        if not m:
            return out
        body, i = _braced(text, text.index("{", m.start()))
        if m.group(1).lower() in ("string", "comment", "preamble"):
            continue
        fields = {}
        for f in re.finditer(r"(\w+)\s*=\s*", body):
            j = f.end()
            if j < len(body) and body[j] == "{":
                v, _ = _braced(body, j)
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1); v = body[j + 1:k]
            else:
                v = re.match(r"[^,]*", body[j:]).group(0)
            fields.setdefault(f.group(1).lower(), v)
        title = _plain(fields.get("title", ""))
        authors = re.split(r"\s+and\s+", fields.get("author", ""))
        fam = [a.split(",")[0].strip() if "," in a else (a.split() or [""])[-1] for a in authors if a.strip()]
        venue = _plain(fields.get("journal", fields.get("booktitle", "")))
        text_ = ", ".join(_plain(x) for x in fam[:6]) + ". " + title + ". " + venue + " " + fields.get("year", "")
        out[m.group(2)] = Ref(m.group(2), text_.strip(), _doi(fields.get("doi", "")), title or None)


def cited_refs(folder: Path, roots):
    """The references the root documents cite, by the first source that has them (see the module)."""
    out, seen = [], set()
    files = prose.tree(folder, roots)
    for r in roots:
        bbl = folder / (Path(r).with_suffix(".bbl"))
        if bbl.is_file():
            t = bbl.read_text(errors="ignore")
            out += _biblatex(t) if "\\entry{" in t else _bibitems(t)
    if not out:
        for f in files:
            t = prose._mask_comments((folder / f).read_text(errors="ignore"))
            if "\\begin{thebibliography}" in t:
                out += _bibitems(t[t.index("\\begin{thebibliography}"):])
    if not out:
        keys = {k.strip() for f in files for m in prose.CITE_RE.finditer(prose._mask_comments((folder / f).read_text(errors="ignore")))
                for k in m.group(1).split(",")}
        bib = {}
        for b in folder.rglob("*.bib"):
            bib.update(_bibfile(b.read_text(errors="ignore")))
        out = [bib[k] for k in sorted(keys) if k in bib]
    uniq = []
    for x in out:
        if x.key not in seen:
            seen.add(x.key); uniq.append(x)
    return uniq


# ----------------------------------------------------------------- identifying the works
def _words(s):
    return re.findall(r"[a-z]{4,}", (s or "").lower())


def _title_id(title):
    """A title as an identity, or ``None`` when it has too few words to tell one work from another."""
    w = _words(title)
    return "title:" + "".join(w)[:80] if len(w) >= 3 else None


class Resolver:
    """Each reference to a :class:`Work`, through its DOI or Crossref's bibliographic search, cached on disk."""
    def __init__(self, cache: Path, mailto=None, log=print):
        self.cache = cache; self.mailto = mailto; self.log = log
        self.known = json.loads(cache.read_text()) if cache.exists() else {}
        self.asked = 0

    def save(self):
        self.cache.write_text(json.dumps(self.known))

    def _crossref(self, params):
        q = urllib.parse.urlencode(dict(params, **({"mailto": self.mailto} if self.mailto else {})))
        req = urllib.request.Request(f"https://api.crossref.org/works{q and '?' + q}",
                                     headers={"User-Agent": "paperdesk readinglist"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except Exception as e:                           # noqa: BLE001
                if attempt == 2:
                    self.log(f"  crossref: {e}"); return None
                time.sleep(2 * (attempt + 1))

    def __call__(self, ref: Ref):
        h = hashlib.sha1(f"{ref.doi}\0{ref.text}".encode()).hexdigest()
        if h not in self.known:
            self.known[h] = self._look(ref); time.sleep(0.2)
            self.asked += 1
            if self.asked % 50 == 0:                         # an interrupted run keeps what it learnt
                self.save()
        w = self.known[h]
        if w is None and ref.title and len(_words(ref.title)) >= 3:
            return Work(_title_id(ref.title), ref.title)     # unresolved but named: matched by its title
        return Work(**w) if w else None

    def _look(self, ref):
        if ref.doi:
            got = self._crossref({"filter": f"doi:{ref.doi}", "rows": 1})
            items = (got or {}).get("message", {}).get("items", [])
            if items:
                return self._work(items[0])
            return dict(id="doi:" + ref.doi, title=ref.title or ref.text[:120])
        if len(_words(ref.text)) < 4:
            return None
        got = self._crossref({"query.bibliographic": ref.text[:300], "rows": 1})
        items = (got or {}).get("message", {}).get("items", [])
        if not items:
            return None
        title = (items[0].get("title") or [""])[0]
        tw, rw = _words(title), set(_words(ref.text))
        if not tw or sum(w in rw for w in tw) / len(tw) < 0.8:   # the match must be the reference's own title
            return None
        return self._work(items[0])

    @staticmethod
    def _work(it):
        year = str(((it.get("issued") or {}).get("date-parts") or [[""]])[0][0] or "")
        auth = (it.get("author") or [{}])[0].get("family", "")
        return dict(id="doi:" + it["DOI"].lower(), title=(it.get("title") or [""])[0], year=year, first_author=auth,
                    venue=(it.get("container-title") or [""])[0])


# ----------------------------------------------------------------- the gap
def gap(field_works, mine_works, min_papers=3):
    """``(rows, common)``: the works at least ``min_papers`` corpus papers cite and the paper does not, most cited
    first, each ``(n, Work, [corpus ids])``; and ``common``, the same list's works the paper does cite."""
    mine = {w.id for w in mine_works if w}
    mine_titles = {_title_id(w.title) for w in mine_works if w and w.title} - {None}
    by, info = {}, {}
    for pid, works in field_works.items():
        for w in works:
            if w is None:
                continue
            by.setdefault(w.id, set()).add(pid); info.setdefault(w.id, w)
    rows, common = [], []
    for wid, pids in by.items():
        if len(pids) < min_papers:
            continue
        w = info[wid]
        row = (len(pids), w, sorted(pids))
        (common if wid in mine or _title_id(w.title) in mine_titles else rows).append(row)
    key = lambda r: (-r[0], r[1].year or "")
    return sorted(rows, key=key), sorted(common, key=key)


def bib_keys(folder: Path):
    """``{identity: key}`` of every entry of the paper's ``.bib`` files, cited or not, by DOI and by title: a work the
    field cites may sit in the bibliography with its citation lost in a rewrite."""
    out = {}
    for b in folder.rglob("*.bib"):
        for k, r in _bibfile(b.read_text(errors="ignore")).items():
            if r.doi:
                out["doi:" + r.doi] = k
            t = _title_id(r.title)
            if t:
                out[t] = k
    return out


def report(rows, common, n_papers, stats, width=110, in_bib=None):
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    total = len(rows) + len(common)
    out = [f"{stats}\nof the {total} works cited by several of the {n_papers} field papers, the paper cites {len(common)}; "
           f"the {len(rows)} it does not, most cited first:\n"]
    in_bib = in_bib or {}
    for n, w, pids in rows:
        key = in_bib.get(w.id) or in_bib.get(_title_id(w.title))
        out.append(f"{n:3d}/{n_papers}  {w.first_author or '?'} {w.year or ''}  {cut(w.title)}\n"
                   f"         {w.id[4:] if w.id.startswith('doi:') else '(no DOI)'}{'  ' + w.venue if w.venue else ''}"
                   + (f"\n         in the .bib as {key}, cited nowhere" if key else ""))
    lost = [r for r in rows if in_bib.get(r[1].id) or in_bib.get(_title_id(r[1].title))]
    if lost:
        out.insert(1, f"{len(lost)} of them are in the paper's .bib but cited nowhere (a citation lost in a rewrite?)\n")
    return "\n".join(out)


def to_json(rows, common, in_bib=None):
    in_bib = in_bib or {}
    row = lambda r: dict(cited_by=r[0], papers=r[2], id=r[1].id, title=r[1].title, year=r[1].year,
                         first_author=r[1].first_author, venue=r[1].venue,
                         bib_key=in_bib.get(r[1].id) or in_bib.get(_title_id(r[1].title)))
    return dict(missing=[row(r) for r in rows], shared=[row(r) for r in common])
