"""The bibliography against fake records: an unregistered DOI, another work behind a DOI, an author and a year that
are not the record's, arXiv by id and by title, and what is left unchecked."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcheck  # noqa: E402

MAIN = r"""\documentclass{article}
\addbibresource{refs.bib}
\begin{document}
\section{Introduction}
We cite \cite{good}, \cite{gone}, \cite{other}, \cite{late}, \cite{author}, \cite{arx}, \cite{herding} and \cite{page}.
\end{document}
"""
BIB = r"""@article{good, author = {Gr{\"o}bner, W. and Tanner, J.}, title = {The {B}rownian walk}, year = 1970, doi = {10.1000/good}}
@article{gone, author = {Nobody, A.}, title = {Invented work}, year = 2001, doi = {10.1000/gone}}
@article{other, author = {Real, A.}, title = {Stimulated echoes in diffusion}, year = 2002, doi = {10.1000/other}}
@article{late, author = {Real, A.}, title = {Late walks of the field}, year = 2005, doi = {10.1000/late}}
@article{author, author = {Wrong, A. and Real, B.}, title = {Authored walks of the field}, year = 2006, doi = {10.1000/author}}
@article{arx, author = {Fick, R.}, title = {Replay of the walks}, journal = {arXiv preprint arXiv:2101.00001}, year = 2021}
@inproceedings{herding, author = {Chen, Y.}, title = {Super-samples from kernel herding}, year = 2010}
@misc{page, author = {{Vendor}}, title = {A data sheet}, url = {https://example.org}, year = 2024}
@article{lost, author = {Lost, A.}, title = {Never cited and never found}, year = 2000, doi = {10.1000/lost}}
"""
CSL = lambda title, fam, year: json.dumps(dict(title=title, author=[dict(family=f) for f in fam], issued={"date-parts": [[year]]}))
ATOM = lambda aid, title, name, y: (f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{aid}v1</id>'
                                    f'<title>{title}</title><published>{y}-01-01</published><updated>{y}-02-01</updated>'
                                    f'<author><name>{name}</name></author></entry></feed>')


class Fake:
    def __init__(self):
        self.asked = []
        self.answers = {
            "https://doi.org/10.1000/good": CSL("The Brownian walk", ["Gröbner", "Tanner"], 1970),
            "https://doi.org/10.1000/gone": None,
            "https://doi.org/10.1000/other": CSL("Worst case complexity of weighted approximation", ["Real"], 2002),
            "https://doi.org/10.1000/late": CSL("Late walks of the field", ["Real"], 2009),
            "https://doi.org/10.1000/author": CSL("Authored walks of the field", ["Real", "Else"], 2006),
            "https://doi.org/10.1000/lost": None,
            "https://export.arxiv.org/api/query?id_list=2101.00001": ATOM("2101.00001", "Replay of the walks", "R. Fick", 2021),
            "https://export.arxiv.org/api/query?id_list=1203.3472": ATOM("1203.3472", "Super-Samples from Kernel Herding", "Yutian Chen", 2012),
        }

    def get(self, url, accept="", pause=0):
        self.asked.append(url)
        if url.startswith("https://api.crossref.org/"):
            return json.dumps({"message": {"items": []}})
        if url.startswith("https://export.arxiv.org/api/query?search_query="):
            return ATOM("1203.3472", "Super-Samples from Kernel Herding", "Yutian Chen", 2012)
        return self.answers[url]

    def save(self):
        pass


def test_entries_against_their_records(tmp_path):
    (tmp_path / "main.tex").write_text(MAIN); (tmp_path / "refs.bib").write_text(BIB)
    look = Fake()
    found, counts = refcheck.run(tmp_path, ["main.tex"], look, log=lambda m: None)
    by = {f.text.split()[0].rstrip(":"): f for f in found}
    assert counts == dict(verified=3, unchecked=1, failed=0)            # good, arx and herding; the web page unchecked
    assert by["gone"].kind == "reference-missing" and by["gone"].severity == "major"
    assert by["gone"].file == "main.tex" and by["gone"].quote == r"\cite{gone}"
    assert "another work" in by["other"].text and by["other"].severity == "major"
    assert "2005 is not the record's (2009)" in by["late"].text and by["late"].severity == "major"
    assert "Wrong, is not among" in by["author"].text
    assert by["lost"].severity == "minor" and by["lost"].file == "refs.bib" and "cited nowhere" in by["lost"].text
    assert not any("example.org" in u for u in look.asked)                # a web page is not fetched


def test_families_and_titles():
    assert refcheck.families(r"Gr{\"o}bner, Wolfgang and {\v{S}}imon, J. and Jane Doe and others") == ["grobner", "simon", "doe"]
    assert refcheck.families("{The FSL Team}") == []
    assert refcheck.title_match("The {B}rownian walk", "The Brownian walk: a survey") == 1.0
