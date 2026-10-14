"""Claims the paper makes more than once: near-duplicate sentences ranked for an agent, or the author, to judge.

Every prose sentence (:mod:`prose`) and every pair of consecutive sentences in a paragraph (a claim often spans two)
is embedded by a sentence-embedding model trained on paraphrases. The mean embedding of the paper is removed before
comparing: every sentence of a paper shares its topic, and without the centring that shared direction dominates the
cosine, so that everything looks alike. Pairs from different places are ranked by the centred cosine; a pair of
neighbours in one paragraph is not a repeat, and a weaker pair whose two sides overlap the two sides of a stronger
one is the same finding again. Word overlap (Jaccard over words of four letters or more) is reported beside the
cosine: high on both is a copy, a high cosine on few shared words a paraphrase, which no text search finds. Claims
made in three places or more come out as clusters.

The ranking is recall, not judgement: a definition in the theory and its use in the results are close by design.
The candidates go to whoever judges them, who posts the real repeats with ``desk.py add --category duplicate``.

Needs ``pip install sentence-transformers`` (CPU is enough: a 50-page paper embeds in about a minute, and the
embeddings are cached by sentence, so a run after an edit embeds only what changed).
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import prose

MODEL = "sentence-transformers/all-mpnet-base-v2"


@dataclass
class Unit:
    """A sentence, or two consecutive sentences of one paragraph."""
    first: prose.Sentence
    sentences: tuple                                         # (file, index) of each sentence it covers
    text: str


@dataclass
class Pair:
    a: Unit
    b: Unit
    cosine: float
    jaccard: float


@dataclass
class Result:
    pairs: list = field(default_factory=list)
    clusters: list = field(default_factory=list)            # each a list of prose.Sentence, in reading order


def units(sents):
    out = [Unit(s, ((s.file, s.index),), s.text) for s in sents]
    for s, t in zip(sents, sents[1:]):
        if s.file == t.file and s.para == t.para:
            out.append(Unit(s, ((s.file, s.index), (t.file, t.index)), s.text + " " + t.text))
    return out


def _words(text):
    return set(re.findall(r"[a-z]{4,}", text.lower()))


def _same_place(u, v):
    """Overlapping, or neighbours within one paragraph: not a repeat."""
    if u.first.file != v.first.file or u.first.para != v.first.para:
        return False
    iu = [i for _, i in u.sentences]; iv = [i for _, i in v.sentences]
    return min(iv) - max(iu) <= 1 and min(iu) - max(iv) <= 1


def _overlaps(p, q):
    """Two pairs that are one finding: each side of one shares a sentence with a side of the other."""
    sa, sb, ta, tb = (set(x.sentences) for x in (p.a, p.b, q.a, q.b))
    return (sa & ta and sb & tb) or (sa & tb and sb & ta)


def rank(us, emb, top=60):
    """The ``top`` strongest distinct pairs, strongest first, and the claims made in three places or more.
    ``emb`` holds one unit-norm embedding per unit (an array, rows in the order of ``us``)."""
    import numpy as np
    e = emb - emb.mean(0)
    e /= np.linalg.norm(e, axis=1, keepdims=True)
    sim = e @ e.T
    iu, ju = np.triu_indices(len(us), 1)
    order = np.argsort(-sim[iu, ju])
    pairs = []
    for k in order:
        i, j = int(iu[k]), int(ju[k])
        if _same_place(us[i], us[j]):
            continue
        p = Pair(us[i], us[j], float(sim[i, j]), 0.0)
        if any(_overlaps(p, q) for q in pairs):
            continue
        wa, wb = _words(p.a.text), _words(p.b.text)
        p.jaccard = len(wa & wb) / max(1, len(wa | wb))
        pairs.append(p)
        if len(pairs) == top:
            break
    return Result(pairs, _clusters(pairs))


def _clusters(pairs):
    """Sentences joined by the stronger half of the pairs, in groups of three places or more."""
    if not pairs:
        return []
    cut = sorted(p.cosine for p in pairs)[len(pairs) // 2]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    first = {}
    for p in pairs:
        if p.cosine >= cut:
            ka, kb = (p.a.first.file, p.a.first.index), (p.b.first.file, p.b.first.index)
            first[ka], first[kb] = p.a.first, p.b.first
            parent[find(ka)] = find(kb)
    groups = {}
    for k in first:
        groups.setdefault(find(k), []).append(first[k])
    return [sorted(g, key=lambda s: (s.file, s.index)) for g in groups.values() if len(g) >= 3]


def embed(texts, model_name=MODEL, cache: Path = None, threads=8):
    """Unit-norm embeddings of ``texts`` (an array), reusing those in ``cache`` (an ``.npz`` keyed by model and text)."""
    import numpy as np
    key = [hashlib.sha1(f"{model_name}\0{t}".encode()).hexdigest() for t in texts]
    have = {}
    if cache is not None and cache.exists():
        with np.load(cache) as z:
            have = dict(zip(z["keys"].tolist(), z["vectors"]))
    todo = sorted({k: t for k, t in zip(key, texts) if k not in have}.items())
    if todo:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError:
            raise SystemExit("desk.py dedup embeds with sentence-transformers: pip install sentence-transformers")
        torch.set_num_threads(threads)
        model = SentenceTransformer(model_name, device="cpu")
        vecs = model.encode([t for _, t in todo], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        have.update(zip([k for k, _ in todo], vecs))
        if cache is not None:
            keep = set(key)                                  # the cache holds the paper as it is now, nothing older
            ks = [k for k in have if k in keep]
            np.savez(cache, keys=np.array(ks), vectors=np.stack([have[k] for k in ks]))
    return np.stack([have[k] for k in key])


def find(paper: Path, roots, model_name=MODEL, cache: Path = None, top=60, threads=8):
    """The ranked candidates of the paper the root documents reach."""
    us = units(prose.paper_sentences(paper, roots))
    return rank(us, embed([u.text for u in us], model_name, cache, threads), top)


def _side(u):
    s = u.first
    return dict(file=s.file, line=s.line, quote=s.quote, text=u.text)


def to_json(res: Result):
    return dict(pairs=[dict(rank=n, cosine=round(p.cosine, 3), jaccard=round(p.jaccard, 2), a=_side(p.a), b=_side(p.b))
                       for n, p in enumerate(res.pairs, 1)],
                clusters=[[dict(file=s.file, line=s.line, quote=s.quote, text=s.text) for s in c] for c in res.clusters])


def report(res: Result, width=200):
    """The candidates as text for a terminal: rank, cosine, word overlap, the two places and their words."""
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    out = []
    for n, p in enumerate(res.pairs, 1):
        out.append(f"{n:3d}  cos {p.cosine:.2f}  words {p.jaccard:.2f}  {p.a.first.file}:{p.a.first.line}  <->  "
                   f"{p.b.first.file}:{p.b.first.line}")
        out.append(f"     A: {cut(p.a.text)}")
        out.append(f"     B: {cut(p.b.text)}")
    for c in res.clusters:
        out.append(f"\nmade {len(c)} times:")
        out += [f"     {s.file}:{s.line}  {cut(s.text)}" for s in c]
    return "\n".join(out)


def save(res: Result, path: Path):
    path.write_text(json.dumps(to_json(res), indent=1, ensure_ascii=False))
