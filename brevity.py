"""Where the paper could say the same in fewer words, and a gate that keeps a shorter version from saying less.

Recall, without a model. Each sentence is scored by the patterns Lanham's Paramedic Method looks for (*Revising
Prose*), as proselint, write-good and Vale encode them: chains of "of" (the colour of the cat), a noun made of a verb
behind a weak verb (perform an evaluation of), an opener that delays the subject (there is, it is ... that),
throat-clearing (it is worth noting that, in order to), redundant pairs (each and every), a passive that names its
agent, stacked hedges, signposting (in this section), and length. Each pair of sentences is scored too: a sentence
whose embedding is close to its neighbour's restates it, and a paragraph's last sentence close to its first sums the
paragraph up. Words are counted per section, against a budget when the config sets one.

The gate, for a rewrite an agent proposes: everything the original carries that a reader can check must survive it,
the citation keys, the cross-references, the numbers with their units, the inline maths, the emphasised terms, the
negations and the quantifiers and hedges (only, every, at most, approximately, may: the words a shortening drops and
the meaning needs), and it must be shorter. Whether the claims are the same both ways is the agent's judgement; the
gate is the part no judgement should be trusted with.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import check
import citations
import prose

# (name, pattern on the sentence's words, what to do about it)
PATTERNS = [
    ("of-chain", r"\b(?:the|a|an|its|their)\s+[\w-]+\s+of\s+(?:the|a|an|its|their)\s+[\w-]+(?:\s+[\w-]+)?\s+of\b",
     "a chain of 'of': make the owner a modifier (the cat's colour)"),
    ("weak-verb", r"\b(?:perform|performs|performed|performing|make|makes|made|carry out|carries out|carried out|conduct|"
     r"conducts|conducted|provide|provides|provided|give|gives|gave|achieve|achieves|undertake|undertakes|undertaken)\s+"
     r"(?:a|an|the)?\s*[\w-]+(?:tion|ment|ance|ence|sis|ison|al)s?\s+(?:of|on|for|to|between)\b",
     "a noun made of a verb behind a weak verb: use the verb (perform an evaluation of: evaluate)"),
    ("be-adjective", r"\b(?:is|are|was|were|be|being|been)\s+(?:dependent|independent|indicative|reflective|suggestive|"
     r"capable|able|in agreement|consistent|responsible)\s+(?:on|of|with|for|to)\b",
     "a state where a verb would do (is dependent on: depends on)"),
    ("delayed-subject", r"^(?:there|it)\s+(?:is|are|was|were|has been|have been)\b|\b(?:there\s+(?:is|are)\s+[\w-]+\s+"
     r"(?:that|which|who))\b|\bit\s+(?:is|was)\s+[\w-]+\s+(?:that|to)\b",
     "an opener that delays the subject (there is, it is ... that)"),
    ("throat-clearing", r"\b(?:it (?:is|should be) (?:worth noting|noted|important to note|clear|evident|apparent|"
     r"interesting to note) that|note that|in order to|due to the fact that|the fact that|in terms of|with respect to|"
     r"with regard to|a (?:large |small )?number of|in the case of|for the purpose of|is able to|are able to|has the "
     r"ability to|in the context of|it can be seen that|as a matter of fact|in fact|basically|essentially|actually|"
     r"needless to say|at the same time|so as to)\b",
     "words that clear the throat: cut them or use the short form (in order to: to)"),
    ("redundant", r"\b(?:each and every|first and foremost|completely (?:eliminate|remove|determine)|absolutely "
     r"(?:essential|necessary)|end result|past history|exact same|currently ongoing|join together|combine together|"
     r"repeat again|still remains|whether or not|true fact|final outcome|close proximity|very unique|both of the)\b",
     "a redundant pair or modifier"),
    ("agent-passive", r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?\w+(?:ed|en)\s+by\s+(?:the|a|an|our|its|each)\b",
     "a passive that names its agent: make the agent the subject"),
    ("hedge-stack", r"\b(?:may|might|could|possibly|potentially|perhaps|likely|seems?|appears?|somewhat|suggests?)\b"
     r"(?:\s+\S+){0,3}\s+\b(?:may|might|could|possibly|potentially|perhaps|likely|seem|appear|somewhat|suggest)\w*\b",
     "two hedges where one is enough"),
    ("signpost", r"\b(?:in this (?:section|paper|work|subsection|appendix|chapter)|as (?:mentioned|discussed|noted|shown|"
     r"described|stated) (?:above|earlier|before|previously)|in the following|below we|here we|we now turn)\b",
     "signposting: the heading or the reference already says it"),
]
LONG = 40                            # words: a sentence this long carries two
_COMPILED = [(n, re.compile(p, re.I), why) for n, p, why in PATTERNS]


@dataclass
class Candidate:
    kind: str                        # "sentence", "neighbours" or "summary"
    score: float
    sentences: list                  # prose.Sentence, one or two
    hits: list = field(default_factory=list)       # (pattern, words matched)


def patterns(s: prose.Sentence):
    """``[(name, matched words)]`` of one sentence; a long sentence counts as a hit."""
    out = [(n, m.group(0)) for n, rx, _ in _COMPILED for m in rx.finditer(s.text)]
    if len(s.text.split()) >= LONG:
        out.append(("long", f"{len(s.text.split())} words"))
    return out


def sentence_candidates(sents, top=60):
    out = [Candidate("sentence", len(h) + 0.02 * len(s.text.split()), [s], h) for s in sents for h in [patterns(s)] if h]
    return sorted(out, key=lambda c: -c.score)[:top]


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


def gate(original, rewrite):
    """What the rewrite loses of the original (both LaTeX source), as a list of reasons; empty when it may stand."""
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
    for name, rx in (("negation", NEGATIONS), ("quantifier or hedge", SCOPE)):
        ca, cb = _count(rx, ta), _count(rx, tb)
        short = [w for w, n in ca.items() if cb.get(w, 0) < n]
        if short:
            lost.append(f"{name} dropped: {', '.join(short)}")
    if len(tb.split()) >= len(ta.split()):
        lost.append(f"not shorter ({len(ta.split())} words to {len(tb.split())})")
    return lost


def verify(rows):
    """Each proposed rewrite (``original`` and ``suggestion``, LaTeX source) through :func:`gate`: ``(kept, dropped)``,
    each kept row given ``saved``, the words it saves (also said at the end of its ``text``), and each dropped one
    ``lost``."""
    kept, dropped = [], []
    for d in rows:
        lost = gate(d["original"], d["suggestion"])
        if lost:
            dropped.append(dict(d, lost=lost))
        else:
            n = len(prose.clean(d["original"]).split()) - len(prose.clean(d["suggestion"]).split())
            kept.append(dict(d, saved=n, text=(d.get("text") or "Shorter, the same claims.").rstrip() + f" ({n} words fewer)"))
    return kept, dropped


# ----------------------------------------------------------------- output
def _side(s):
    return dict(file=s.file, line=s.line, quote=s.quote, text=s.text, original=s.src.strip())


def to_json(sent_c, pair_c, counts):
    return dict(sentences=[dict(score=round(c.score, 2), hits=[dict(pattern=n, words=w) for n, w in c.hits], **_side(c.sentences[0]))
                           for c in sent_c],
                pairs=[dict(kind=c.kind, cosine=round(c.score, 3), a=_side(c.sentences[0]), b=_side(c.sentences[1]))
                       for c in pair_c],
                words={k: dict(words=v[0], budget=v[1]) for k, v in counts.items()})


def report(sent_c, pair_c, counts, width=160):
    cut = lambda t: t if len(t) <= width else t[:width - 1] + "…"
    total = sum(v[0] for v in counts.values())
    out = [f"words of prose per section ({total} in all):"]
    for c, (n, b) in counts.items():
        out.append(f"  {c:13s}{n:7d}" + (f"  of {b} ({'+' if n > b else ''}{n - b})" if b else ""))
    out.append(f"\nsentences with the most to cut ({len(sent_c)}):")
    for c in sent_c:
        s = c.sentences[0]
        out.append(f"  {s.file}:{s.line}  " + "; ".join(f"{n} '{w}'" for n, w in c.hits))
        out.append(f"      {cut(s.text)}")
    if pair_c:
        out.append(f"\nsentences that restate their neighbour, or a paragraph's end its start ({len(pair_c)}):")
        for c in pair_c:
            a, b = c.sentences
            out.append(f"  {c.kind:10s} cos {c.score:.2f}  {a.file}:{a.line} / {b.line}")
            out.append(f"      A: {cut(a.text)}")
            out.append(f"      B: {cut(b.text)}")
    return "\n".join(out)
