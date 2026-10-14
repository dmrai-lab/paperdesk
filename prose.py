"""The prose of a LaTeX paper as sentences, each with the place it stands and words a comment can quote.

The paper is every ``.tex`` file its documents reach through ``\\input`` and ``\\include``, in reading order. A
sentence is prose of at least six words: comments, display maths, tables, verbatim, sectioning commands and float
scaffolding are not prose, and a blank line, a heading or a display ends a paragraph. Each :class:`Sentence` carries
its file, the line it starts on, its paragraph, its text with the markup stripped (what is compared), and ``quote``:
its first words as they stand in the source, which ``anchor.find_quote`` finds at that line, so a finding about the
sentence becomes a ``desk.py add`` comment as it is.
"""
import re
from dataclasses import dataclass
from pathlib import Path

INPUT_RE = re.compile(r"(?<!\\)\\(?:input|include)\{([^}]+)\}")
NOT_PROSE_ENVS = r"equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|eqnarray\*?|displaymath|tabular\*?|tabularx|verbatim|lstlisting|minted|tikzpicture|algorithmic"
MIN_WORDS = 6
QUOTE_WORDS = 25
_ABBREV = ("e.g", "i.e", "et al", "Fig", "Figs", "Eq", "Eqs", "Sec", "Secs", "Ref", "Refs", "Tab", "vs", "cf", "approx", "resp", "No")
_END = re.compile(r"[.!?](?=\s+[A-Z0-9\\(\[$])")


@dataclass
class Sentence:
    file: str           # relative to the paper's folder
    line: int           # where it starts
    para: int           # paragraph number within the file
    index: int          # sentence number within the file
    text: str           # markup stripped: what is compared
    quote: str          # its first words verbatim from the source (no comment inside), for ``desk.py add``


def tree(paper: Path, roots):
    """The ``.tex`` files the root documents reach, each once, in reading order (roots included)."""
    out, seen = [], set()

    def visit(rel):
        rel = rel if rel.endswith(".tex") else rel + ".tex"
        p = paper / rel
        if rel in seen or not p.is_file():
            return
        seen.add(rel); out.append(rel)
        for m in INPUT_RE.finditer(_mask_comments(p.read_text())):
            visit(m.group(1).strip())
    for r in roots:
        visit(str(r))
    return out


def _blank(m):
    """A match replaced by spaces, its line breaks kept, so every offset and line number survives the masking."""
    return re.sub(r"[^\n]", " ", m.group(0))


def _mask_comments(text):
    return re.sub(r"(?<!\\)%[^\n]*", _blank, text)


def mask(text):
    """The source with everything that is not prose blanked out, offsets and line breaks unchanged."""
    t = _mask_comments(text)
    if "\\begin{document}" in t:                             # a root file: its preamble is not prose
        i = t.index("\\begin{document}")
        t = re.sub(r"[^\n]", " ", t[:i]) + t[i:]
    t = re.sub(r"\\begin\{(" + NOT_PROSE_ENVS + r")\}.*?\\end\{\1\}", _blank, t, flags=re.S)
    t = re.sub(r"\\\[.*?\\\]|\$\$.*?\$\$", _blank, t, flags=re.S)
    t = re.sub(r"\\(part|chapter|section|subsection|subsubsection|paragraph|label|includegraphics|input|include|begin|end"
               r"|bibliography|bibliographystyle|maketitle|appendix|centering)\*?(\[[^\]]*\])?(\{(?:[^{}]|\{[^{}]*\})*\})?", _blank, t)
    t = re.sub(r"\\(caption|item)\*?(\[[^\]]*\])?", _blank, t)  # a caption's words are prose, an item's too
    return t


def clean(s):
    """The words of a stretch of source: citations and references dropped, inline maths as ``X``, markup gone."""
    s = re.sub(r"\\(?:cite[tp]?|citealp|parencite|textcite|ref|eqref|autoref|cref|Cref|label|url)\*?(\[[^\]]*\])*\{[^}]*\}", "", s)
    s = re.sub(r"\$[^$]*\$|\\\(.*?\\\)", " X ", s)
    for _ in range(3):
        s = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^{}]*)\}", r"\2", s)
    s = re.sub(r"\\[a-zA-Z]+\*?|\\.", " ", s)
    s = re.sub(r"[{}~]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ends(seg):
    """Offsets just past each sentence end in a paragraph: a stop followed by a capital, a digit or markup, unless the
    word before it is an abbreviation."""
    out = []
    for m in _END.finditer(seg):
        before = re.search(r"([A-Za-z.]+)$", seg[:m.start()])
        if before and before.group(1).rstrip(".") in _ABBREV:
            continue
        out.append(m.end())
    return out


def _quote(raw):
    """The first words of a sentence verbatim, stopped before any comment so the words stand contiguous in the file."""
    m = re.search(r"(?<!\\)%", raw)
    words = (raw[:m.start()] if m else raw).split()
    return " ".join(words[:QUOTE_WORDS])


def sentences(paper: Path, file):
    """Every prose sentence of one file, in order."""
    raw = (paper / file).read_text()
    masked = mask(raw)
    out, para = [], 0
    for block in re.finditer(r"(?:[^\n]*\S[^\n]*(?:\n|$))+", masked):
        para += 1
        start, seg = block.start(), block.group(0)
        cuts = [0] + _ends(seg) + [len(seg)]
        for a, b in zip(cuts, cuts[1:]):
            lead = len(seg[a:b]) - len(seg[a:b].lstrip())
            s, e = start + a + lead, start + b
            text = clean(masked[s:e])
            if len(text.split()) < MIN_WORDS:
                continue
            q = _quote(raw[s:e])
            if len(q.split()) < 3:
                continue
            out.append(Sentence(file, raw.count("\n", 0, s) + 1, para, len(out), text, q))
    return out


def paper_sentences(paper: Path, roots):
    """Every prose sentence the documents reach, file by file in reading order."""
    return [s for f in tree(paper, roots) for s in sentences(paper, f)]
