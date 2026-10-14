"""Citation density, the section classes, the classifier and the corpus fetcher, without the network or a model."""
import gzip
import io
import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import citations  # noqa: E402
import prose      # noqa: E402

MAIN = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Monte Carlo simulation is the reference method for diffusion in tissue \citep{a,b}.
It has been used for permeability, beading and myelin in many studies \cite{c}.
\input{body}
\appendix
\section{Details}
The pack was written with sixteen bits per band and two hundred and fifty six modes.
\section{Funding}
This work was funded by nobody at all, which is a statement and not science.
\end{document}
"""
BODY = r"""\section{Results}
The replay matches the closed form within one standard error at every diameter here.
The surface channel agrees with Brownstein and Tarr at both of the relaxivities.
"""


@pytest.fixture
def paper(tmp_path):
    (tmp_path / "main.tex").write_text(MAIN)
    (tmp_path / "body.tex").write_text(BODY)
    return tmp_path


def test_sections_follow_inputs_and_appendix(paper):
    got = [(s.section, s.appendix, citations.section_class(s), len(s.cites)) for s in prose.paper_sentences(paper, ["main.tex"])]
    assert got == [("Introduction", False, "introduction", 2), ("Introduction", False, "introduction", 1),
                   ("Results", False, "results", 0), ("Results", False, "results", 0),
                   ("Details", True, "appendix", 0), ("Funding", True, "appendix", 0)]


def test_a_second_document_is_appendix(paper):
    (paper / "supp.tex").write_text("\\begin{document}\n\\section{Methods}\nThe walkers were seeded uniformly in every compartment of the box.\n\\end{document}\n")
    s = [x for x in prose.paper_sentences(paper, ["main.tex", "supp.tex"]) if x.file == "supp.tex"]
    assert s and all(x.appendix and citations.section_class(x) == "appendix" for x in s)


def test_statements_are_their_own_class():
    s = prose.Sentence("f.tex", 1, 1, 0, "x", "x", (), "Data and Code Availability", False)
    assert citations.section_class(s) == "statements"


def test_profile_counts_keys_per_thousand_words(paper):
    p = citations.profile(prose.paper_sentences(paper, ["main.tex"]))
    intro = p["introduction"]
    assert intro["cites"] == 3 and intro["density"] == pytest.approx(1000 * 3 / intro["words"])
    assert p["results"]["cites"] == 0


def test_classifier_learns_a_separable_label_and_scores_held_out_papers():
    rng = np.random.default_rng(0)
    direction = rng.normal(size=32)

    def paper(name, n):
        ss, vs = [], []
        for i in range(n):
            cited = i % 2 == 0
            v = rng.normal(size=32) + (1.5 if cited else -1.5) * direction / np.linalg.norm(direction)
            ss.append(prose.Sentence(f"{name}.tex", i, i, i, f"{name} {i}", "q", ("k",) if cited else (), "Results", False))
            vs.append(v)
        return ss, vs
    field, table = {}, {}
    for name in "abcd":
        ss, vs = paper(name, 60); field[name] = ss; table.update({s.text: v for s, v in zip(ss, vs)})
    mine, mv = paper("mine", 40)
    for s, v in zip(mine, mv):
        table[s.text] = v
    flagged, auc, thr = citations.uncited(mine, field, lambda texts: np.stack([table[t] for t in texts]), top=100)
    assert auc > 0.9
    assert all(not s.cites and sc >= thr for sc, s, _ in flagged)


def _serve(monkeypatch, payload):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(citations.urllib.request, "urlopen", lambda *a, **k: R(payload))
    monkeypatch.setattr(citations.time, "sleep", lambda s: None)


def test_fetch_skips_a_pdf(monkeypatch, tmp_path):
    _serve(monkeypatch, b"%PDF-1.7 ...")
    assert citations.fetch("2401.00001", tmp_path) is None
    assert citations.fetch("2401.00001", tmp_path) is None   # remembered, not fetched again


def test_fetch_unpacks_a_tarball_and_a_single_file(monkeypatch, tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = MAIN.replace("\\input{body}", "").encode()
        info = tarfile.TarInfo("paper.tex"); info.size = len(data); tf.addfile(info, io.BytesIO(data))
    _serve(monkeypatch, buf.getvalue())
    d = citations.fetch("2401.00002", tmp_path)
    assert citations.root_of(d) == "paper.tex"
    _serve(monkeypatch, gzip.compress(MAIN.encode()))
    d = citations.fetch("2401.00003", tmp_path)
    assert (d / "main.tex").read_text() == MAIN


@pytest.mark.parametrize("src, want", [
    (r"the reference method for diffusion \citep{a,b}.", "the reference method for diffusion."),
    (r"as before (\cite{k}; e.g.\ \citealp{m}), and so on", "as before, and so on"),
    (r"in Fig.~\ref{f} and Sec.~\ref{s}", "in Fig. 1 and Sec. 1"),
])
def test_a_removed_citation_leaves_no_trace(src, want):
    """The classifier is trained on sentences whose citations are removed: residue of a removed one would be the label."""
    assert prose.clean(src) == want
