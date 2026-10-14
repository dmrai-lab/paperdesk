"""The prose splitter and the duplicate ranking, on a small paper written here (no model: the ranking is given
embeddings)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anchor  # noqa: E402
import dedup   # noqa: E402
import prose   # noqa: E402

MAIN = r"""\documentclass{article}
\newcommand{\x}{y}
\begin{document}
\input{sections/intro}
\section{Results}
The replay matches the closed form to within one standard error, e.g.\ at every diameter in Fig.~\ref{f}.
% a comment that is not prose. Nor is this sentence of it.
The pack serves every scanner whose band lies under its cut, so the certificate is a frequency.
\begin{equation}
a = b. This is maths, not prose at all here.
\end{equation}
\begin{figure}
\includegraphics{x.pdf}
\caption{The replay against the closed form at every diameter, with the standard error shaded.}
\label{f}
\end{figure}
\end{document}
"""
INTRO = r"""\section{Introduction}
Monte Carlo simulation is the reference method for diffusion MRI in idealized tissue \citep{a,b}.
It is run once per acquisition, and the walk is thrown away when the signal is out.
% \input{sections/never}
I show that the walk can be kept: the signal is a query of it, at $b = 1000$ and any other value.
"""


@pytest.fixture
def paper(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "main.tex").write_text(MAIN)
    (tmp_path / "sections" / "intro.tex").write_text(INTRO)
    return tmp_path


def test_tree_follows_inputs_not_commented_ones(paper):
    assert prose.tree(paper, ["main.tex"]) == ["main.tex", "sections/intro.tex"]


def test_sentences_are_the_prose_only(paper):
    texts = [s.text for s in prose.paper_sentences(paper, ["main.tex"])]
    assert len(texts) == 6, texts
    assert not any("comment" in t or "maths" in t or "newcommand" in t or "Results" == t for t in texts)
    assert any(t.startswith("The replay matches the closed form") and "e.g." in t for t in texts)  # no cut at e.g.
    assert any(t.startswith("The replay against the closed form") for t in texts)                 # a caption is prose


def test_every_quote_anchors_at_its_line(paper):
    for s in prose.paper_sentences(paper, ["main.tex"]):
        assert anchor.find_quote(paper, s.file, s.quote, s.line) == s.line


def _sent(file, index, para, text):
    return prose.Sentence(file, index + 1, para, index, text, text)


def test_rank_puts_the_repeat_first_and_skips_neighbours():
    sents = [_sent("a.tex", 0, 1, "alpha claim one two three"), _sent("a.tex", 1, 1, "neighbour one two three four"),
             _sent("a.tex", 2, 2, "something else entirely here"), _sent("b.tex", 0, 1, "alpha claim restated elsewhere")]
    us = dedup.units(sents)
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(len(us), 16))
    emb[1] = emb[0] + 0.01 * rng.normal(size=16)            # a neighbour in the same paragraph: not a repeat
    emb[3] = emb[0] + 0.05 * rng.normal(size=16)            # the same claim in another file
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    res = dedup.rank(us, emb, top=3)
    top = res.pairs[0]
    assert {top.a.first.file, top.b.first.file} == {"a.tex", "b.tex"}
    assert {top.a.first.index, top.b.first.index} == {0}
    assert all(not dedup._same_place(p.a, p.b) for p in res.pairs)
    assert not any(dedup._overlaps(p, q) for i, p in enumerate(res.pairs) for q in res.pairs[:i])


def test_a_claim_made_three_times_is_a_cluster():
    sents = [_sent(f"{n}.tex", 0, 1, f"the claim in file {n}") for n in "abc"] + \
            [_sent(f"{n}.tex", 1, 2, f"unrelated words {n}") for n in "abc"]
    us = dedup.units(sents)
    rng = np.random.default_rng(1)
    emb = rng.normal(size=(len(us), 32)); base = rng.normal(size=32)
    for i in range(3):
        emb[i] = base + 0.05 * rng.normal(size=32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    res = dedup.rank(us, emb, top=6)
    assert any(sorted(s.file for s in c) == ["a.tex", "b.tex", "c.tex"] for c in res.clusters)
