"""The brevity patterns, the neighbour ranking on given embeddings, the words per section, and the gate."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brevity  # noqa: E402
import prose  # noqa: E402


def sent(text, para=1, index=0, section="Results", file="r.tex"):
    return prose.Sentence(file, index + 1, para, index, text, " ".join(text.split()[:5]), (), section, False, text)


def test_sites():
    names = lambda t: [x.name for x in brevity.sites([sent(t)])]
    assert "of-the" in names("We report the colour of the cat today.")
    assert "all-of-the" in names("All of the walkers are stored once.")
    assert "whiz" in names("The walkers that are near the wall reflect.")
    assert "weak-verb" in names("We perform an evaluation of the error on every substrate.")
    assert "nominal" in names("The estimation of the floor takes a minute.")
    assert "be-adjective" in names("The floor is dependent on the walker count of the pack.")
    assert "delayed-subject" in names("There are walkers that never touch a wall in the box.")
    assert "stock" in names("The walk is stored in order to replay it later.")
    assert "agent-passive" in names("The signal is computed by the engine at replay time.")
    assert "hedge-stack" in names("This may possibly suggest a bias in the floor.")
    assert "signpost" in names("In this section the floor of every pack is shown.")
    assert names("Replay answers every acquisition by an inner product.") == []
    assert brevity.long_sentences([sent(" ".join(["word"] * brevity.LONG))])


def test_paragraph_of_and_prose_lines():
    s = [sent("a b c d e f", 1, 0), sent("g h i j k l", 1, 1), sent("m n o p q r", 2, 2)]
    s[0].line, s[1].line, s[2].line = 10, 12, 20
    s[1].src = "g h i\nj k l"
    find = brevity._paragraph_of(s)
    assert find("r.tex", 13) == ("r.tex", 1) and find("r.tex", 15) is None and find("r.tex", 20) == ("r.tex", 2)
    assert brevity._holds({"walkers", "cross", "wall"}, "walkers cross the wall") and not brevity._holds({"walkers"}, "the page bins")
    assert brevity._prose_like("encoded (Sec. 3.3).") and not brevity._prose_like("w = −") and not brevity._prose_like("| {z }")


def test_neighbours_and_summaries():
    s = [sent("a b c d e f", 1, 0), sent("a b c d e g", 1, 1), sent("x y z w v u", 1, 2), sent("p q r s t o", 2, 3)]
    emb = np.array([[1, 0, 0, 0], [0.95, 0.3, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    got = brevity.neighbour_candidates(s, emb, top=10)
    assert got[0].kind == "neighbours" and [x.index for x in got[0].sentences] == [0, 1]
    assert {c.kind for c in got} == {"neighbours", "summary"}
    assert not any({x.para for x in c.sentences} == {1, 2} for c in got)      # never across paragraphs


def test_words_per_section():
    s = [sent("one two three four five six", section="Introduction"), sent("one two three four five six seven")]
    assert brevity.words_per_section(s, {"results": 5}) == {"introduction": (6, None), "results": (7, 5)}


def test_gate():
    orig = (r"The pack is not refreshed, and only $K=256$ modes at 1.28~kHz reach the signal for every scanner of "
            r"Table~\ref{tab:scanners} \citep{Fick2026}, as \emph{replay} requires at most once per walk.")
    good = (r"The pack is not refreshed: only $K=256$ modes at 1.28~kHz reach the signal for every scanner of "
            r"Table~\ref{tab:scanners} \citep{Fick2026}; \emph{replay} reads it at most once per walk.")
    assert brevity.gate(orig, good) == []
    bad = r"The pack's $K=256$ modes at 1.3~kHz reach the signal for each scanner \citep{Fick2026}, as the walk requires."
    lost = " | ".join(brevity.gate(orig, bad))
    for word in ("cross-references", "1.28", "negation dropped (1 in the original, 0", "only", "every", "at most", "emphasised"):
        assert word in lost, word
    assert any("not shorter" in x for x in brevity.gate("Two short words here now.", "Two short words here now, again."))
    assert brevity.gate("Neither engine has explained it, nor has the theory.", "Not yet explained by either engine or theory.") == []
    kept, dropped = brevity.verify([dict(original=orig, suggestion=good), dict(original=orig, suggestion=bad)])
    assert len(kept) == 1 and kept[0]["saved"] > 0 and len(dropped) == 1 and dropped[0]["lost"]


def test_verify_edits():
    orig = r"The colour of the cat is measured in order to compare it with the colour of the dog (Fig.~\ref{fig:cat})."
    row = dict(original=orig, needs=12, edits=[["The colour of the cat", "The cat's colour"], ["in order to", "to"]])
    [k], [] = brevity.verify([row])
    assert k["saved"] > 12 and "'in order to' → 'to'" in k["text"] and "the last line holds 12" in k["text"]
    assert "suggestion" not in k
    [], [d] = brevity.verify([dict(original=orig, edits=[["the colour of a cat", "x"]])])
    assert "not in the source" in d["lost"][0]
    [], [d] = brevity.verify([dict(original=orig, edits=[[r" (Fig.~\ref{fig:cat})", ""]])])
    assert "cross-reference" in d["lost"][0]


def test_every_paragraph_is_a_candidate_in_reading_order():
    s = [sent("Replay answers every acquisition by an inner product.", 2, 0), sent("The pack is read once.", 2, 1),
         sent("The walkers that are near the wall reflect.", 5, 2)]
    got = brevity.paragraphs(s, brevity.sites(s), [])
    assert [d["para"] for d in got] == [2, 5] and got[0]["words"] == 13 and got[0]["sites"] == [] and got[0]["needs"] is None


def test_an_edit_that_tears_a_group_is_refused():
    orig = r"The \emph{pack} holds $K=256$ modes of the walk, read in order to replay it."
    [], [d] = brevity.verify([dict(original=orig, edits=[[r"\emph{pack", "pack"]])])
    assert "brace group" in d["lost"][0]
    [], [d] = brevity.verify([dict(original=orig, edits=[["$K=256", "K"]])])
    assert "brace group or maths" in d["lost"][0]
    [k], [] = brevity.verify([dict(original=orig, edits=[["in order to", "to"]])])


def test_edits_land_in_the_source_nearest_their_line():
    text = "in order to replay\n\nother words\n\nwe read it in order\nto replay it\n"
    out, n, miss = brevity.edit_source(text, dict(line=5, edits=[["in order to", "to"], ["absent words", "x"]]))
    assert out == "in order to replay\n\nother words\n\nwe read it to replay it\n" and n == 1 and miss == ["absent words"]
    marked, _, _ = brevity.edit_source(text, dict(line=1, edits=[["in order to", "to"]]), mark=True)
    assert marked.startswith(r"\brevcut{in order to}\brevnew{to} replay")


def test_build_inputs_are_the_files_the_build_read(tmp_path):
    (tmp_path / "sections").mkdir(); (tmp_path / "notes").mkdir()
    for f in ("main.tex", "sections/a.tex", "refs.bib", "notes/big.dat"):
        (tmp_path / f).write_text("x")
    (tmp_path / "main.fls").write_text(f"PWD {tmp_path}\nINPUT ./sections/a.tex\nINPUT /usr/share/texmf/tex/latex/base/article.cls\n"
                                      f"INPUT {tmp_path}/main.tex\nOUTPUT main.pdf\n")
    assert brevity.build_inputs(tmp_path, ["main.tex"]) == ["main.tex", "refs.bib", "sections/a.tex"]


def test_verifying_twice_reads_the_same():
    orig = r"The colour of the cat is measured in order to compare it with the dog (Fig.~\ref{fig:cat})."
    [k], [] = brevity.verify([dict(original=orig, note="Two trims.", edits=[["in order to", "to"]])])
    [k2], [] = brevity.verify([k])
    assert k2["text"] == k["text"] and k["text"].startswith("Two trims. 'in order to' → 'to'")


def test_a_restating_sentence_may_carry_its_only_away_for_a_checker():
    orig = "Only the band matters, as Table 2 shows. The pack's band is the only quantity that matters here. It is read once."
    cut = [["The pack's band is the only quantity that matters here. ", ""]]
    [], [d] = brevity.verify([dict(original=orig, edits=cut)])
    assert "quantifier or hedge dropped: only" in d["lost"][0]
    [k], [] = brevity.verify([dict(original=orig, edits=cut)], strict=False)
    assert k["flags"] == [[cut[0][0], ["quantifier or hedge dropped: only"]]] and ": checked]" in k["text"]
    [], [d] = brevity.verify([dict(original=orig, edits=[["as Table 2 shows", "as shown"]])], strict=False)
    assert "numbers dropped" in d["lost"][0]                                     # the facts are never a checker's call


def test_a_blind_sample_of_the_dropped_edits_rides_among_the_kept_in_reading_order():
    orig = "We use the pool in order to read it. The pack is stored in order to replay it. All of the walkers stay."
    trim = [dict(file="r.tex", line=1, quote="We use", original=orig,
                 edits=[["We use the pool in order to", "We use the pool to"], ["stored in order to", "stored to"],
                        ["All of the walkers", "All walkers"]])]
    chk = [dict(trim[0], edits=[trim[0]["edits"][2]],
                judged=[dict(old=trim[0]["edits"][0][0], verdict="drop", why="x"), dict(old=trim[0]["edits"][1][0], verdict="drop", why="y"),
                        dict(old=trim[0]["edits"][2][0], verdict="keep", why="z")])]
    [r], [], n = brevity.blind(trim, chk, share=0.5, name="p1")
    assert n == 1 and len(r["edits"]) == 2 and r["pass"] == "p1"
    assert [m["checker"] for m in r["edit_meta"]].count("drop") == 1 and r["edits"][-1] == ["All of the walkers", "All walkers"]
    [r], [], n = brevity.blind(trim, [], share=1.0)                     # a paragraph the checker left out: all dropped
    assert n == 3 and [e[0] for e in r["edits"]] == [e[0] for e in trim[0]["edits"]]
    assert brevity.blind(trim, chk, share=0.0)[2] == 0


def test_an_applied_edit_undone_by_hand_is_seen():
    assert brevity.undone("we read it in order to\nreplay it\n", 1, "in order to", "to")
    assert not brevity.undone("we read it to replay it\n", 1, "in order to", "to")
    assert not brevity.undone("far\n" * 100 + "in order to\n", 1, "in order to", "to")         # too far from its line
    twice = "in order to read, and in order to replay"                   # one of two applied: undone once both are back
    assert not brevity.undone("in order to read, and to replay\n", 1, "in order to", "to", twice)
    assert brevity.undone(twice + "\n", 1, "in order to", "to", twice)
    assert not brevity.undone("the pack, stored once\n", 1, "the pack", "the pack, stored once")   # old inside new
