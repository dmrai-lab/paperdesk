"""The brevity patterns, the neighbour ranking on given embeddings, the words per section, and the gate."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brevity  # noqa: E402
import prose  # noqa: E402


def sent(text, para=1, index=0, section="Results", file="r.tex"):
    return prose.Sentence(file, index + 1, para, index, text, " ".join(text.split()[:5]), (), section, False, text)


def test_patterns():
    hits = lambda t: [n for n, _ in brevity.patterns(sent(t))]
    assert "of-chain" in hits("We report the colour of the cat of the neighbour today.")
    assert "weak-verb" in hits("We perform an evaluation of the error on every substrate.")
    assert "be-adjective" in hits("The floor is dependent on the walker count of the pack.")
    assert "delayed-subject" in hits("There are walkers that never touch a wall in the box.")
    assert "throat-clearing" in hits("It is worth noting that the walk is stored in order to replay it.")
    assert "redundant" in hits("Each and every walker is stored once in the pack.")
    assert "agent-passive" in hits("The signal is computed by the engine at replay time.")
    assert "hedge-stack" in hits("This may possibly suggest a bias in the floor.")
    assert "signpost" in hits("In this section we show the floor of every pack.")
    assert hits("Replay answers every acquisition by an inner product.") == []
    assert hits(" ".join(["word"] * brevity.LONG)) == ["long"]


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
    for word in ("cross-references", "1.28", "negation dropped: not", "only", "every", "at most", "emphasised"):
        assert word in lost, word
    assert any("not shorter" in x for x in brevity.gate("Two short words here now.", "Two short words here now, again."))
    kept, dropped = brevity.verify([dict(original=orig, suggestion=good), dict(original=orig, suggestion=bad)])
    assert len(kept) == 1 and kept[0]["saved"] > 0 and len(dropped) == 1 and dropped[0]["lost"]
