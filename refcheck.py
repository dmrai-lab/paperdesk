"""Whether each work of the bibliography exists, and is the work its entry describes.

An entry with a DOI is checked against what the DOI's registration agency says of it (doi.org's content negotiation:
Crossref, DataCite and the others answer alike); a DOI that is not registered is the classic invented reference. An
entry naming an arXiv identifier and no DOI is checked against arXiv. Any other entry is looked for in Crossref by its
title and first author, and kept only when a record's title is the entry's. A web page (an entry with a URL and none of
those) is not checked.

The record found is compared with the entry: the title (its words of four letters or more, either holding most of the
other's), the first author (among the record's authors, and first), and the year (any of the record's issue, print and
online years, or for arXiv any year from the first version to the last). Each disagreement is a finding, placed on the
first sentence that cites the entry, so ``desk.py add`` can post it, or on the ``.bib`` when nothing cites it.

What goes out is the bibliography's own metadata (DOIs, arXiv ids, titles and first authors), never the paper's text.
Answers are cached in ``.refcheck.json`` beside the config; a lookup that failed for the network is asked again.
"""
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import check
import references

ARXIV_RE = re.compile(r"(?:arxiv[:\s]*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.I)
ATOM = "{http://www.w3.org/2005/Atom}"
CORPORATE = re.compile(r"^\s*\{.*\}\s*$")


def _ascii(s):
    s = re.sub(r"\\[a-zA-Z]+\s*|\\.|[{}]", "", s or "")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if c.isascii() and c.isalpha()).lower()


def families(author_field):
    """The family names of a .bib ``author`` field, folded to plain lower-case letters; ``[]`` for an organisation."""
    out = []
    for a in re.split(r"\s+and\s+", author_field or ""):
        if not a.strip() or a.strip().lower() == "others":
            continue
        if CORPORATE.match(a):
            return []
        out.append(_ascii(a.split(",")[0] if "," in a else a.split()[-1]))
    return out


class Lookup:
    """HTTP answers cached on disk by URL: a JSON or XML body, or ``None`` for a 404."""
    def __init__(self, cache: Path, log=print):
        self.cache = cache; self.log = log
        self.known = json.loads(cache.read_text()) if cache.exists() else {}
        self.last = {}

    def save(self):
        self.cache.write_text(json.dumps(self.known))

    def get(self, url, accept="application/json", pause=0.2):
        if url in self.known:
            return self.known[url]
        host = urllib.parse.urlparse(url).netloc
        wait = self.last.get(host, 0) + pause - time.time()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "paperdesk refcheck"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    body = None; break
                err = e
            except Exception as e:                           # noqa: BLE001
                err = e
            time.sleep(2 * (attempt + 1))
        else:
            self.log(f"  {url}: {err}"); self.last[host] = time.time()
            return "failed"
        self.last[host] = time.time()
        self.known[url] = body
        if len(self.known) % 25 == 0:
            self.save()
        return body


# ----------------------------------------------------------------- records
def from_doi(look, doi):
    body = look.get("https://doi.org/" + urllib.parse.quote(doi, safe="/:;()"), "application/vnd.citationstyles.csl+json")
    if body in (None, "failed"):
        return body
    try:
        d = json.loads(body)
    except ValueError:
        return "failed"
    years = {str(d.get(k, {}).get("date-parts", [[None]])[0][0]) for k in ("issued", "published-print", "published-online", "published")}
    title = d.get("title") or ""
    title = title[0] if isinstance(title, list) else title
    sub = d.get("subtitle") or ""
    sub = sub[0] if isinstance(sub, list) and sub else (sub if isinstance(sub, str) else "")
    return dict(source="doi", title=title + (": " + sub if sub else ""), years=sorted(years - {"None"}),
                families=[_ascii(a.get("family") or a.get("literal") or "") for a in d.get("author", [])])


def from_arxiv(look, aid):
    body = look.get(f"https://export.arxiv.org/api/query?id_list={aid}", "application/atom+xml", pause=3)
    if body in (None, "failed"):
        return body
    try:
        e = ET.fromstring(body).find(ATOM + "entry")
    except ET.ParseError:
        return "failed"
    if e is None or e.find(ATOM + "id") is None or "api/errors" in (e.findtext(ATOM + "id") or ""):
        return None
    y0, y1 = (e.findtext(ATOM + "published") or "")[:4], (e.findtext(ATOM + "updated") or "")[:4]
    years = [str(y) for y in range(int(y0), int(y1 or y0) + 1)] if y0.isdigit() else []
    return dict(source="arxiv", title=re.sub(r"\s+", " ", e.findtext(ATOM + "title") or ""), years=years,
                families=[_ascii((a.findtext(ATOM + "name") or "").split()[-1]) for a in e.findall(ATOM + "author")
                          if (a.findtext(ATOM + "name") or "").split()])


def from_search(look, title, first):
    q = urllib.parse.urlencode({"query.bibliographic": f"{title} {first}".strip()[:300], "rows": 3})
    body = look.get("https://api.crossref.org/works?" + q)
    if body in (None, "failed"):
        return body
    for it in json.loads(body).get("message", {}).get("items", []):
        rec = dict(source="crossref", title=(it.get("title") or [""])[0] + ((": " + it["subtitle"][0]) if it.get("subtitle") else ""),
                   years=sorted({str(((it.get(k) or {}).get("date-parts") or [[None]])[0][0])
                                 for k in ("issued", "published-print", "published-online")} - {"None"}),
                   families=[_ascii(a.get("family", "")) for a in it.get("author", [])], doi=it.get("DOI"))
        if title_match(title, rec["title"]) >= 0.8:
            return rec
    return None


def from_arxiv_search(look, title):
    q = urllib.parse.quote('ti:"' + re.sub(r"[^\w\s-]", " ", title)[:200] + '"')
    body = look.get(f"https://export.arxiv.org/api/query?search_query={q}&max_results=3", "application/atom+xml", pause=3)
    if body in (None, "failed"):
        return body
    try:
        entries = ET.fromstring(body).findall(ATOM + "entry")
    except ET.ParseError:
        return "failed"
    for e in entries:
        aid = (e.findtext(ATOM + "id") or "").rsplit("/abs/", 1)[-1]
        rec = from_arxiv(look, re.sub(r"v\d+$", "", aid)) if aid else None
        if isinstance(rec, dict) and title_match(title, rec["title"]) >= 0.8:
            return dict(rec, years=[])                       # a posting of a work published elsewhere: its year is not the work's
    return None


def title_match(a, b):
    """The share of the shorter title's words (four letters or more) that the other holds."""
    wa, wb = set(references._words(references._plain(a))), set(references._words(b))
    return len(wa & wb) / max(1, min(len(wa), len(wb))) if wa and wb else 0.0


# ----------------------------------------------------------------- the check
def compare(ref, rec, edition=False):
    """The disagreements between an entry and its record: ``[(severity, text)]``. ``edition``: the record was found
    for a book by its title, and may be another edition, so a year apart is a note."""
    out, f = [], ref.fields
    m = title_match(ref.title or "", rec["title"])
    if m < 0.5:
        out.append(("major", f"the {'DOI' if rec['source'] == 'doi' else rec['source']} record is another work: "
                             f"'{rec['title'][:140]}'"))
    elif m < 0.8:
        out.append(("minor", f"the title differs from the record's: '{rec['title'][:140]}'"))
    fam = families(f.get("author", ""))
    if fam and rec["families"] and m >= 0.5:
        if fam[0] not in rec["families"]:
            out.append(("major", f"the first author, {fam[0].capitalize()}, is not among the record's "
                                 f"({', '.join(x.capitalize() for x in rec['families'][:4])}{'…' if len(rec['families']) > 4 else ''})"))
        elif rec["families"][0] != fam[0]:
            out.append(("minor", f"the record's first author is {rec['families'][0].capitalize()}, not {fam[0].capitalize()}"))
    year = re.sub(r"\D", "", f.get("year", "") or f.get("date", "")[:4])
    if year and rec["years"] and m >= 0.5 and year not in rec["years"]:
        off = min(abs(int(year) - int(y)) for y in rec["years"])
        out.append(("minor" if off == 1 or edition else "major", f"the year {year} is not the record's "
                    f"({', '.join(rec['years'])}{'; another edition?' if edition else ''})"))
    return out


def run(paper: Path, roots, look: Lookup, log=print):
    """``(findings, counts)``: what disagrees or was not found, and how many entries were verified, unchecked, ..."""
    p = check.Paper(paper, roots)
    pos, refs = check.bib_entries(p)
    where = {k: (b, n) for b, n, k in pos}
    sites = {}
    for f, n, m in p.scan(check.CITE_RE):
        for k in m.group(1).split(","):
            sites.setdefault(k.strip(), (f, n, m.group(0)))
    out, counts = [], dict(verified=0, unchecked=0, failed=0)
    for i, (key, ref) in enumerate(refs.items()):
        if i and i % 20 == 0:
            log(f"  {i}/{len(refs)} entries")
        f = ref.fields or {}
        text = " ".join(f.get(x, "") for x in ("eprint", "journal", "note", "url", "howpublished", "archiveprefix"))
        aid = ARXIV_RE.search(text) or (re.match(r"\d{4}\.\d{4,5}$", f.get("eprint", "").strip()) and
                                        ARXIV_RE.search("arxiv:" + f["eprint"].strip()))
        fam = families(f.get("author", ""))
        if ref.doi:
            rec, how = from_doi(look, ref.doi), f"DOI {ref.doi}"
        elif aid:
            rec, how = from_arxiv(look, aid.group(1)), f"arXiv {aid.group(1)}"
        elif f.get("url") or "\\url" in f.get("howpublished", ""):
            counts["unchecked"] += 1; continue                # a web page
        elif ref.title:
            rec, how = from_search(look, ref.title, fam[0] if fam else ""), "a Crossref search by title"
            if rec is None:
                rec, how = from_arxiv_search(look, ref.title), "an arXiv search by title"
        else:
            counts["unchecked"] += 1; continue
        if rec == "failed":
            counts["failed"] += 1; continue
        cited = key in sites
        file, line, quote = sites[key] if cited else (*where[key], "")
        if rec is None:
            kind = f.get("entrytype", "")
            if how.startswith("DOI"):
                sev, why = "major", f"{key}: the DOI {ref.doi} is not registered (doi.org knows no such work)"
            elif how.startswith("arXiv"):
                sev, why = "major", f"{key}: arXiv has no paper {aid.group(1)}"
            else:
                sev = "major" if kind == "article" else "minor"
                why = (f"{key}: neither Crossref nor arXiv has a work of this title ('{ref.title[:100]}')" +
                       ("; a journal article should be there" if kind == "article" else
                        "; a conference abstract, book or report may simply not be there"))
            out.append(check.Finding("reference-missing", sev if cited else "minor", why + ("" if cited else " (cited nowhere)"),
                                     file, line, quote))
            continue
        diffs = compare(ref, rec, edition=how.startswith("a Crossref search") and f.get("entrytype") == "book")
        if not diffs:
            counts["verified"] += 1
        for sev, why in diffs:
            out.append(check.Finding("reference-differs", sev if cited else "minor",
                                     f"{key} ({how}): {why}" + ("" if cited else " (cited nowhere)"), file, line, quote))
    look.save()
    return out, counts


def report(found, counts, n):
    rows = [f"{n} .bib entries: {counts['verified']} match their record, {counts['unchecked']} web pages or entries "
            f"without a title not checked, {counts['failed']} not reached (network; run again)"]
    for kind, title in (("reference-missing", "not found"), ("reference-differs", "disagrees with its record")):
        got = [f for f in found if f.kind == kind]
        rows.append(f"\n{title}: {len(got)}")
        rows += [f"  {f.severity:5s} {f.file}:{f.line}  {f.text}" for f in sorted(got, key=lambda f: f.severity != "major")]
    return "\n".join(rows)
