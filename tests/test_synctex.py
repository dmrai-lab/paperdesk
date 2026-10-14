"""synctex_edit's vote along the line: a line's left-margin record names the file read at shipout, the glyphs their
own, and the file most probes agree on wins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anchor  # noqa: E402


def test_the_margin_record_is_outvoted(monkeypatch):
    answers = {56.0: ("sections/results.tex", 176, None), 68.0: ("sections/methods.tex", 176, None),
               80.0: ("sections/methods.tex", 175, None)}
    monkeypatch.setattr(anchor, "_synctex_point", lambda paper, pdf, page, x, y: answers[x])
    assert anchor.synctex_edit(Path("."), Path("p.pdf"), 24, 56.0, 423.0) == ("sections/methods.tex", 176, None)


def test_agreement_and_failure(monkeypatch):
    monkeypatch.setattr(anchor, "_synctex_point", lambda paper, pdf, page, x, y: ("a.tex", int(x), None))
    assert anchor.synctex_edit(Path("."), Path("p.pdf"), 1, 10.0, 5.0) == ("a.tex", 10, None)   # the first probe's line
    monkeypatch.setattr(anchor, "_synctex_point", lambda paper, pdf, page, x, y: (None, None, "no record"))
    assert anchor.synctex_edit(Path("."), Path("p.pdf"), 1, 10.0, 5.0) == (None, None, "no record")
