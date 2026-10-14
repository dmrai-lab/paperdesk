"""How the paper cites, against papers of its field: density per section, and the sentences that look cited but are not.

The field is a corpus of arXiv papers named in the config (``[citations] corpus``, arXiv identifiers). Their LaTeX
sources are fetched once into ``.citecorpus/`` beside the config and read like the paper, through :mod:`prose`, so
both sides are counted the same way: citation keys and words of prose sentences, per section class. A submission
without LaTeX source (a PDF only) is skipped and named.

Section classes come from the ``\\section`` titles (:func:`section_class`): introduction, theory, methods, results,
discussion, conclusion, appendix (anything after ``\\appendix`` or in a supplement), front matter before the first
section, and statements (data availability, funding, contributions, ...), which carry no science and are left out.

:func:`profile` gives, per class, words, citation keys, keys per thousand words and the share of paragraphs of 40
words or more that cite nothing; :func:`norm` the corpus's median and interquartile range of the density.

:func:`uncited` learns what the field cites. Each corpus sentence is embedded by CiteBERT (SciBERT trained further on
cite-worthiness; ``copenlu/citebert``) and labelled by whether it carries a citation, and a logistic regression on the
embedding and the section class learns the label. It is checked on each corpus paper with that paper held out (the
area under the ROC curve), then scores the paper's sentences. A sentence without a citation that scores above the
median of the paper's own cited sentences reads, to the field, like one that should cite.
"""
import io
import re
import tarfile
import time
import urllib.request
from pathlib import Path

import prose

MODEL = "copenlu/citebert"
CLASSES = ("introduction", "theory", "methods", "results", "discussion", "conclusion", "appendix")
_TITLES = [("statements", r"availab|ethic|contribution|funding|acknowledg|competing|conflict|declaration|supplementary material|author"),
           ("results", r"results? and discussion"),
           ("introduction", r"intro|background|motivation|related work|prior work"),
           ("theory", r"theor|formalism|framework|model|derivation"),
           ("methods", r"method|material|simulat|experiment|implement|data|acquisition|protocol|setup"),
           ("results", r"result|validation|benchmark|application"),
           ("discussion", r"discuss|limitation|outlook"),
           ("conclusion", r"conclu|summary")]
PARA_WORDS = 40


def section_class(sentence: prose.Sentence):
    if sentence.appendix:
        return "appendix"
    if not sentence.section:
        return "front"
    t = sentence.section.lower()
    for cls, pat in _TITLES:
        if re.search(pat, t):
            return cls
    return "other"


# ----------------------------------------------------------------- the corpus
def fetch(arxiv_id, cache: Path):
    """The folder of one arXiv paper's LaTeX source, fetched once; ``None`` when arXiv holds a PDF only."""
    d = cache / arxiv_id.replace("/", "_")
    if (d / ".pdf-only").exists():
        return None
    if not (d / ".ok").exists():
        d.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f"https://arxiv.org/e-print/{arxiv_id}", headers={"User-Agent": "paperdesk citestats"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        time.sleep(3)                                        # arXiv asks for a pause between requests
        if data[:4] == b"%PDF":
            (d / ".pdf-only").touch(); return None
        if data[:2] == b"\x1f\x8b":
            import gzip
            data = gzip.decompress(data)
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                tf.extractall(d, filter="data")              # an archive from the net: nothing outside d, no links out
        except tarfile.ReadError:
            if data[:4] == b"%PDF":
                (d / ".pdf-only").touch(); return None
            (d / "main.tex").write_bytes(data)              # a single gzipped .tex
        (d / ".ok").touch()
    return d


def root_of(folder: Path):
    """The file a source folder builds (``\\begin{document}``), the one reaching most prose when there are several."""
    roots = [p for p in folder.rglob("*.tex")
             if "\\begin{document}" in prose._mask_comments(p.read_text(errors="ignore"))]
    if not roots:
        return None
    size = lambda p: sum(len(s.text) for s in _safe_sentences(folder, [p.relative_to(folder).as_posix()]))
    return max(roots, key=size).relative_to(folder).as_posix()


def _safe_sentences(folder, roots):
    try:
        return prose.paper_sentences(folder, roots)
    except (UnicodeDecodeError, OSError):
        return []


def corpus(ids, cache: Path, log=print):
    """``{arxiv_id: [Sentence, ...]}`` of the corpus papers that have LaTeX source."""
    out = {}
    for i in ids:
        try:
            d = fetch(i, cache)
        except Exception as e:                               # noqa: BLE001
            log(f"  {i}: not fetched ({e})"); continue
        if d is None:
            log(f"  {i}: PDF only on arXiv, skipped"); continue
        r = root_of(d)
        sents = _safe_sentences(d, [r]) if r else []
        if len(sents) < 50:
            log(f"  {i}: {len(sents)} sentences read, skipped"); continue
        out[i] = sents
    return out


# ----------------------------------------------------------------- density per section
def profile(sents):
    """``{class: dict(words, cites, density, paragraphs, uncited_paragraphs)}`` (density in keys per 1000 words)."""
    out = {}
    paras = {}
    for s in sents:
        c = section_class(s)
        p = out.setdefault(c, dict(words=0, cites=0))
        p["words"] += len(s.text.split()); p["cites"] += len(s.cites)
        q = paras.setdefault((c, s.file, s.para), [0, False])
        q[0] += len(s.text.split()); q[1] |= bool(s.cites)
    for (c, _, _), (w, cited) in paras.items():
        if w >= PARA_WORDS:
            out[c]["paragraphs"] = out[c].get("paragraphs", 0) + 1
            out[c]["uncited_paragraphs"] = out[c].get("uncited_paragraphs", 0) + (not cited)
    for p in out.values():
        p["density"] = 1000 * p["cites"] / max(p["words"], 1)
    return out


def norm(profiles, min_words=150):
    """``{class: (n, median, q1, q3)}`` of the density over the corpus papers with at least ``min_words`` there."""
    import numpy as np
    out = {}
    for c in CLASSES:
        v = [p[c]["density"] for p in profiles if c in p and p[c]["words"] >= min_words]
        if v:
            out[c] = (len(v), float(np.median(v)), float(np.percentile(v, 25)), float(np.percentile(v, 75)))
    return out


def stats_report(mine, field, n_papers):
    rows = [f"{'section':13}{'words':>7}{'cites':>7}{'/1k words':>11}   {'field median (IQR)':>20}  {'ratio':>6}  uncited paragraphs"]
    for c in CLASSES:
        if c not in mine:
            continue
        p = mine[c]
        f = field.get(c)
        fn = f"{f[1]:5.1f} ({f[2]:.1f}-{f[3]:.1f}) n={f[0]}" if f else "-"
        ratio = f"{p['density'] / f[1]:.2f}" if f and f[1] > 0 else "-"
        flag = "  <- below the field's quartile" if f and p["density"] < f[2] else ""
        up = f"{p.get('uncited_paragraphs', 0)}/{p.get('paragraphs', 0)}"
        rows.append(f"{c:13}{p['words']:7d}{p['cites']:7d}{p['density']:11.1f}   {fn:>20}  {ratio:>6}  {up:>8}{flag}")
    return f"citation keys per 1000 words of prose, against {n_papers} papers of the field\n" + "\n".join(rows)


# ----------------------------------------------------------------- what the field cites
SCORED = ("introduction", "theory", "methods", "results", "discussion", "conclusion", "appendix")


def _features(emb, sents):
    import numpy as np
    onehot = np.array([[section_class(s) == c for c in SCORED] for s in sents], float)
    return np.hstack([emb, onehot, np.ones((len(sents), 1))])


def _fit(x, y, l2=1.0, iters=25):
    """Logistic regression by Newton's method with an L2 penalty (the intercept, the last column, unpenalised)."""
    import numpy as np
    w = np.zeros(x.shape[1]); pen = np.full(x.shape[1], l2); pen[-1] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))
        g = x.T @ (p - y) + pen * w
        h = (x * (p * (1 - p))[:, None]).T @ x + np.diag(pen)
        step = np.linalg.solve(h, g); w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w


def _auc(score, y):
    import numpy as np
    pos, neg = score[y == 1], score[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def uncited(mine, field, embed, quantile=0.5, top=80):
    """The paper's uncited sentences that score like its cited ones. ``field`` is :func:`corpus`'s dict, ``embed`` a
    function from texts to embeddings. Returns ``(flagged, held_out_auc, threshold)``; ``flagged`` is a list of
    ``(score, Sentence, paragraph_cites)``, highest first, at most ``top``."""
    import numpy as np
    keep = lambda ss: [s for s in ss if section_class(s) in SCORED]
    papers = {k: keep(v) for k, v in field.items()}
    mine = keep(mine)
    allc = [s for v in papers.values() for s in v]
    e = embed([s.text for s in allc + mine])
    xc, xm = _features(e[:len(allc)], allc), _features(e[len(allc):], mine)
    yc = np.array([bool(s.cites) for s in allc], float)
    owner = np.array([k for k, v in papers.items() for _ in v])
    aucs = []
    for k in papers:                                         # each corpus paper scored by a model that never saw it
        tr, te = owner != k, owner == k
        w = _fit(xc[tr], yc[tr]); aucs.append(_auc(xc[te] @ w, yc[te]))
    w = _fit(xc, yc)
    score = 1 / (1 + np.exp(-np.clip(xm @ w, -30, 30)))
    cited = np.array([bool(s.cites) for s in mine])
    thr = float(np.quantile(score[cited], quantile)) if cited.any() else 0.5
    para_cites = {}
    for s in mine:
        para_cites[(s.file, s.para)] = para_cites.get((s.file, s.para), False) or bool(s.cites)
    flagged = sorted(((float(sc), s, para_cites[(s.file, s.para)]) for sc, s, c in zip(score, mine, cited)
                      if not c and sc >= thr), key=lambda t: -t[0])[:top]
    return flagged, float(np.nanmean(aucs)), thr


def uncited_report(flagged, auc, thr, n_mine_cited, width=200):
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    by = {}
    for _, s, _ in flagged:
        by[section_class(s)] = by.get(section_class(s), 0) + 1
    head = (f"what the field cites, learnt from its papers: area under the ROC curve {auc:.2f} on each paper held out\n"
            f"flagged: uncited sentences scoring above {thr:.2f}, the median of this paper's {n_mine_cited} cited ones; "
            + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    rows = [f"{sc:.2f}  {s.file}:{s.line}  [{section_class(s)}]{'  (its paragraph cites)' if pc else ''}\n      {cut(s.text)}"
            for sc, s, pc in flagged]
    return head + "\n\n" + "\n".join(rows)


def uncited_json(flagged):
    return [dict(score=round(sc, 3), file=s.file, line=s.line, section=s.section, cls=section_class(s),
                 paragraph_cites=pc, quote=s.quote, text=s.text) for sc, s, pc in flagged]
