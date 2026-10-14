"""Reading a paper's references in each format, the resolver's acceptance rule, and the gap, without the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import references  # noqa: E402

BIBLATEX = r"""\refsection{0}
    \entry{Basser1994}{article}{}
      \name{author}{1}{}{%
        {{hash=x}{family={Basser},given={Peter}}}%
      }
      \field{title}{{MR} diffusion tensor spectroscopy and imaging}
      \field{journaltitle}{Biophysical Journal}
      \field{year}{1994}
      \verb{doi}
      \verb 10.1016/S0006-3495(94)80775-1
      \endverb
    \endentry
"""
BIBITEMS = r"""\begin{thebibliography}{2}
\bibitem[Grebenkov(2007)]{greb}
Denis S Grebenkov.
\newblock {NMR} survey of reflected brownian motion.
\newblock \emph{Reviews of Modern Physics}, 79\penalty0 (3):\penalty0 1077, 2007.
\bibitem{tanner} J. E. Tanner, Use of the stimulated echo in NMR diffusion studies, J. Chem. Phys. 52 (1970), doi:10.1063/1.1673336.
\end{thebibliography}"""
BIB = r"""@article{used, author = {Stejskal, E. O. and Tanner, J. E.}, title = {Spin diffusion measurements}, journal = {J. Chem. Phys.}, year = 1965, doi = {10.1063/1.1695690}}
@article{unused, author = {Nobody, A.}, title = {Never cited here}, year = 2000}
"""


def test_biblatex_entries(tmp_path):
    (tmp_path / "main.tex").write_text("\\begin{document}\\end{document}")
    (tmp_path / "main.bbl").write_text(BIBLATEX)
    [r] = references.cited_refs(tmp_path, ["main.tex"])
    assert r.key == "Basser1994" and r.doi == "10.1016/s0006-3495(94)80775-1"
    assert r.title == "MR diffusion tensor spectroscopy and imaging" and "Basser" in r.text


def test_inline_thebibliography(tmp_path):
    (tmp_path / "main.tex").write_text("\\begin{document}\n" + BIBITEMS + "\n\\end{document}")
    a, b = references.cited_refs(tmp_path, ["main.tex"])
    assert a.key == "greb" and "NMR survey of reflected brownian motion" in a.text and a.doi is None
    assert b.key == "tanner" and b.doi == "10.1063/1.1673336"


def test_bib_restricted_to_cited_keys(tmp_path):
    (tmp_path / "main.tex").write_text("\\begin{document}\nAs measured \\cite{used}.\n% \\cite{unused}\n\\end{document}")
    (tmp_path / "refs.bib").write_text(BIB)
    [r] = references.cited_refs(tmp_path, ["main.tex"])
    assert r.key == "used" and r.doi == "10.1063/1.1695690" and r.text.startswith("Stejskal, Tanner. Spin diffusion")


def _resolver(tmp_path, answer):
    res = references.Resolver(tmp_path / "c.json", log=lambda m: None)
    res._crossref = lambda params: answer
    references.time.sleep = lambda s: None
    return res


def _item(title, doi="10.1/x"):
    return {"message": {"items": [{"DOI": doi, "title": [title], "issued": {"date-parts": [[1979]]},
                                   "author": [{"family": "Brownstein"}]}]}}


def test_a_match_must_be_the_references_own_title(tmp_path):
    ref = references.Ref("bt", "Brownstein, Tarr. Importance of classical diffusion in NMR studies of water in biological cells. Phys Rev A 1979")
    w = _resolver(tmp_path, _item("Importance of classical diffusion in NMR studies of water in biological cells"))(ref)
    assert w.id == "doi:10.1/x" and w.year == "1979"
    w = _resolver(tmp_path / "..", _item("A completely different paper about magnets"))(
        references.Ref("bt2", ref.text + " "))
    assert w is None


def test_gap_ranks_what_the_field_cites_and_the_paper_does_not():
    W = lambda i, t="t": references.Work(i, t + i)
    field = {"p1": [W("doi:a"), W("doi:b"), W("doi:c")], "p2": [W("doi:a"), W("doi:b")], "p3": [W("doi:a"), W("doi:c"), None]}
    rows, common = references.gap(field, [W("doi:b")], min_papers=2)
    assert [(n, w.id) for n, w, _ in rows] == [(3, "doi:a"), (2, "doi:c")]
    assert [w.id for _, w, _ in common] == ["doi:b"]


def test_a_work_in_the_bib_but_cited_nowhere_is_named(tmp_path):
    (tmp_path / "refs.bib").write_text(BIB)
    keys = references.bib_keys(tmp_path)
    assert keys["doi:10.1063/1.1695690"] == "used" and keys[references._title_id("Never cited here")] == "unused"
    W = references.Work("doi:10.1063/1.1695690", "Spin diffusion measurements", "1965", "Stejskal")
    text = references.report([(3, W, ["a", "b", "c"])], [], 3, "", in_bib=keys)
    assert "in the .bib as used, cited nowhere" in text and "1 of them are in the paper's .bib" in text
