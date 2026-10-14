"""Re-anchoring when a comment's words left its file: found in another file and document, or marked stale with the
build's time and the commit that took them out; a float's box cut to its page."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anchor  # noqa: E402

MOVED = "The walkers that cross a membrane are counted once per crossing in every substrate."
GONE = "A sentence that the author has since cut from the paper altogether, with its point."


def comment(cid, quote, doc="main", file="sections/results.tex"):
    return dict(id=cid, status="proposed", doc=doc, quote=quote, page=4, x_pt=60.0, y_pt=300.0, rect=None,
                anchor=dict(file=file, line=3, source_anchored=True))


def test_moved_words_follow_into_the_other_document_and_cut_ones_go_stale(tmp_path, monkeypatch):
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "results.tex").write_text("Only what stays.\n")
    (tmp_path / "sections" / "appendix.tex").write_text("\\section{More}\n" + MOVED + "\n")
    main, supp = tmp_path / "main.pdf", tmp_path / "supplement.pdf"
    main.write_bytes(b""); supp.write_bytes(b"")
    monkeypatch.setattr(anchor, "synctex_view", lambda paper, pdf, file, line:
                        (2, 72.0, 140.0, dict(x=72.0, y=130.0, w=400.0, h=11.0)) if pdf == supp and file == "sections/appendix.tex" else None)
    cs = [comment(1, MOVED), comment(2, GONE)]
    moved, stale = anchor.reanchor(tmp_path, main, cs, words={1: []}, docs=[("main", main), ("supplement", supp)])
    assert (moved, stale) == (1, 1)
    assert cs[0]["doc"] == "supplement" and cs[0]["page"] == 2 and cs[0]["anchor"]["file"] == "sections/appendix.tex"
    assert cs[0]["anchor"]["line"] == 2 and "stale" not in cs[0]["anchor"]
    assert isinstance(cs[1]["anchor"]["stale"], str) and cs[1]["page"] == 4      # the build's time; the pin stays put
    assert anchor.reanchor(tmp_path, main, cs, words={1: []}, docs=[("main", main), ("supplement", supp)])[1] == 0  # counted once


def test_the_commit_that_cut_the_words(tmp_path):
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "r.tex").write_text(GONE + "\n"); git("init", "-q"); git("add", "."); git("commit", "-qm", "first")
    (tmp_path / "r.tex").write_text("Shorter now.\n"); git("commit", "-qam", "the cut")
    assert anchor.removed_by(tmp_path, "r.tex", GONE).endswith("the cut")
    assert anchor.removed_by(tmp_path, "r.tex", r"\citep{x}") is None             # too short to tell apart


def test_a_float_box_is_cut_to_its_page():
    assert anchor.on_page(dict(x=55.0, y=-352.0, w=1067.0, h=811.0), (612.0, 792.0)) == dict(x=55.0, y=0.0, w=557.0, h=459.0)
    assert anchor.on_page(dict(x=55.0, y=-50.0, w=10.0, h=20.0), (612.0, 792.0)) is None
    assert anchor.on_page(None, (612.0, 792.0)) is None and anchor.on_page(dict(x=1.0, y=1.0, w=1.0, h=1.0), None)


def test_a_reviewers_words_found_in_the_other_documents_pdf(tmp_path, monkeypatch):
    main, supp = tmp_path / "main.pdf", tmp_path / "supplement.pdf"
    main.write_bytes(b""); supp.write_bytes(b"")
    said = "the walkers cross the membrane once".split()
    words = {1: [(72.0 + 40 * k, 100.0, 108.0 + 40 * k, 110.0, w) for k, w in enumerate(said)]}
    monkeypatch.setattr(anchor, "pdf_words", lambda pdf: words if pdf == supp else {1: []})
    monkeypatch.setattr(anchor, "synctex_view", lambda *a: None)
    monkeypatch.setattr(anchor, "synctex_edit", lambda paper, pdf, n, x, y: ("sections/appendix.tex", 9, None))
    c = dict(id=1, status="open", doc="main", quote="the walkers cross the membrane once", page=3, x_pt=1.0, y_pt=1.0,
             rect=None, anchor=dict(file="sections/results.tex", line=4))
    monkeypatch.setattr(anchor, "context_of", lambda paper, f, ln: dict(section="More"))
    assert anchor.reanchor(tmp_path, main, [c], words={1: []}, docs=[("main", main), ("supplement", supp)]) == (1, 0)
    assert c["doc"] == "supplement" and c["page"] == 1 and c["anchor"]["line"] == 9 and c["x_pt"] == 72.0
