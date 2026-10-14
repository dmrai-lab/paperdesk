"""The mechanical consistency checks on a small two-document paper written here."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check  # noqa: E402

MAIN = r"""\documentclass{article}
\usepackage{xr}
\addbibresource{refs.bib}
\externaldocument{supplement}
\begin{document}
\input{abstract}
\section{Introduction}
Diffusion is measured with the stimulated echo in many laboratories today \citep{tanner}.
The method of moments (MoM) is used here for every experiment we report below.
The walk is described in Section~\ref{sec:theory} and its error in Section~\ref{sec:nowhere} of this paper.
\section{Theory}\label{sec:theory}
We use the moment-ordered mean (MOM) once more, and the matrix method (MM) too, in this section here.
The walkers are time dependent in the bulk, as the time-dependent diffusion literature describes \cite{ghost}.
The supplement's Figure~\ref{fig:supp} shows the same walk at a second resolution of the grid.
With $K = 256$ modes the series converges, and with $\Delta = 30$~ms the walk is long enough to probe it.
Later, $K = 128$ modes suffice for the narrower band considered in the second experiment of the paper.
The signal is normalised here, and it is normalized in the appendix of the supplement that follows.
\begin{figure}\caption{A figure nobody mentions.}\label{fig:orphan}\end{figure}
\begin{figure}\caption{The walk at full scale.}\label{fig:main}\end{figure}
\label{sec:theory}
\end{document}
"""
ABSTRACT = r"""We simulate $10^5$ walkers and reach an error of 2.5\% on every substrate we tried in this work.
The signal is computed in 40~ms for one billion walkers across the whole of the phantom described here.
"""
SUPP = r"""\documentclass{article}
\externaldocument{main}
\begin{document}
\section{Supplement}
As Figure~\ref{fig:main} of the paper shows, the walk converges, and $10^5$ walkers err by 2.46\%.
\begin{figure}\caption{The same walk.}\label{fig:supp}\end{figure}
\end{document}
"""
BIB = r"""@article{tanner, author = {Tanner, J. E.}, title = {Use of the stimulated echo in NMR diffusion studies}, year = 1970, doi = {10.1063/1.1673336}}
@article{tanner2, author = {Tanner, J. E.}, title = {Use of the stimulated echo in {NMR} diffusion studies}, year = 1970, doi = {10.1063/1.1673336}}
@article{lonely, author = {Nobody, A.}, title = {Never cited here at all}, year = 2000}
@article{lonely, author = {Nobody, A.}, title = {Never cited here at all}, year = 2000}
"""


def paper(tmp_path):
    for name, text in (("main.tex", MAIN), ("abstract.tex", ABSTRACT), ("supplement.tex", SUPP), ("refs.bib", BIB)):
        (tmp_path / name).write_text(text)
    return tmp_path


def kinds(found, kind):
    return [f for f in found if f.kind == kind]


def test_cross_references(tmp_path):
    found, caps, _ = check.run(paper(tmp_path), ["main.tex", "supplement.tex"])
    [u] = kinds(found, "undefined-ref")
    assert "sec:nowhere" in u.text and u.file == "main.tex" and u.quote == r"\ref{sec:nowhere}"
    [d] = kinds(found, "duplicate-label")
    assert "sec:theory" in d.text
    [o] = kinds(found, "unreferenced-float")
    assert "fig:orphan" in o.text and o.line == MAIN.splitlines().index(r"\begin{figure}\caption{A figure nobody mentions.}\label{fig:orphan}\end{figure}") + 1
    # fig:supp is the supplement's, read through \externaldocument; fig:main is referred to from the supplement
    assert {c["label"] for c in caps} == {"fig:orphan", "fig:main", "fig:supp"}
    assert [r["file"] for c in caps if c["label"] == "fig:main" for r in c["referred"]] == ["supplement.tex"]


def test_a_document_sees_another_only_through_externaldocument(tmp_path):
    paper(tmp_path)
    (tmp_path / "main.tex").write_text(MAIN.replace(r"\externaldocument{supplement}", ""))
    found, _, _ = check.run(tmp_path, ["main.tex", "supplement.tex"])
    assert {f.quote for f in kinds(found, "undefined-ref")} == {r"\ref{sec:nowhere}", r"\ref{fig:supp}"}


def test_citations(tmp_path):
    found, _, _ = check.run(paper(tmp_path), ["main.tex", "supplement.tex"])
    [m] = kinds(found, "missing-key")
    assert "ghost" in m.text and m.quote == r"\cite{ghost}"
    assert [f.text.split()[0] for f in kinds(found, "unused-entry")] == ["tanner2"]
    [d] = kinds(found, "duplicate-key")
    assert "lonely" in d.text and d.file == "refs.bib" and not d.quote
    [s] = kinds(found, "same-work")
    assert "tanner, tanner2" in s.text


def test_numbers_read_as_written():
    got = [(n.value, n.unit, n.decimals) for n in check.numbers(r"$10^5$ walkers, 2.5\% error, $2\times10^{-9}$~m$^2$/s, "
                                                                 r"3~$\mu$m, in 2019 and two minutes, one percent, \ref{fig:3}")]
    assert got == [(1e5, "walker", 99), (2.5, "%", 1), (2e-9, "m", 99), (3, "um", 0), (2, "min", 0), (1, "%", 0)]


def test_abstract_numbers_against_the_body(tmp_path):
    found, _, _ = check.run(paper(tmp_path), ["main.tex", "supplement.tex"])
    # 2.5% is stated (2.46% rounds to it), 10^5 walkers too; 40 ms and a billion walkers are not
    assert sorted(f.text.split("states ")[1].split(",")[0] for f in kinds(found, "abstract-number")) == \
        ["1000000000 walkers", "40 ms"]


def test_symbol_values_and_duplicate_numbers(tmp_path):
    paper(tmp_path)
    found, _, notes = check.run(tmp_path, ["main.tex", "supplement.tex"])
    [k] = kinds(found, "symbol-values")
    assert k.text.startswith("$K$ takes 2 values") and "256" in k.text and "128" in k.text
    assert notes and not kinds(found, "duplicate-numbers")
    side = lambda line, words: dict(file="main.tex", line=line, quote=words, text=words)
    lines = MAIN.splitlines()
    a = lines.index(next(l for l in lines if l.startswith("With $K = 256$"))) + 1
    b = lines.index(next(l for l in lines if l.startswith("Later, $K = 128$"))) + 1
    (tmp_path / "dedup.json").write_text(json.dumps(dict(pairs=[dict(rank=1, a=side(a, "With"), b=side(b, "Later,"))])))
    found, _, notes = check.run(tmp_path, ["main.tex", "supplement.tex"], tmp_path / "dedup.json")
    [d] = kinds(found, "duplicate-numbers")
    assert "256 modes here against 128 modes" in d.text and not notes


def test_terms(tmp_path):
    found, _, _ = check.run(paper(tmp_path), ["main.tex", "supplement.tex"])
    [t] = kinds(found, "acronym-twice")
    assert t.text.startswith("MOM is defined 2 times") and " / " in t.text   # two expansions
    assert {f.text.split()[0] for f in kinds(found, "acronym-unused")} >= {"MM"}
    sp = {f.text for f in kinds(found, "spelling")}
    assert any("time-dependent" in x and "time dependent" in x for x in sp)
    assert any("normalized" in x and "normalised" in x for x in sp)
