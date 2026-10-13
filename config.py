"""The desk's configuration, read once by the server and by ``desk.py``: the paper's folder and its documents.

A desk serves one document or several built from the same source tree, a paper and its supplement: ``[paper]
docs`` lists them in tab order, each a ``.tex`` (or the one ``.docx``) with an optional title, and ``main`` alone
names a single document, as before. Each document's PDF is its stem beside it. The build command may carry
``{doc}``, filled with each document's file name in turn; without it, it runs once.

    [paper]
    dir = "../paper"
    docs = [{file = "main.tex", title = "paper"}, {file = "supplement.tex", title = "supplement"}]
    build = "latexmk -pdf -synctex=1 -interaction=nonstopmode -halt-on-error {doc}"
"""
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:                                # Python 3.10: the same parser as the tomli backport
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        raise SystemExit("paperdesk reads its config with tomllib (Python 3.11+); on an older Python run: pip install tomli")


class Doc:
    """One document of the desk: ``name`` (the file's stem, what comments and the API call it), ``file``, ``title``,
    ``pdf`` and the command that builds it."""
    def __init__(self, paper: Path, file, title, build):
        self.file = str(file); self.name = Path(self.file).stem; self.title = title or self.name
        self.pdf = paper / (self.name + ".pdf")
        self.build = build.replace("{doc}", self.file) if build else None

    def __repr__(self):
        return f"Doc({self.name})"


def load(config_path: Path):
    """``(cfg, paper, docs)``: the parsed TOML, the paper's folder and its :class:`Doc` list, first document first."""
    cfg = tomllib.loads(Path(config_path).read_text()) if Path(config_path).exists() else {}
    section = cfg.get("paper", {})
    paper = (Path(config_path).resolve().parent / section.get("dir", ".")).resolve()
    build = section.get("build")
    entries = section.get("docs") or [section.get("main", "main.tex")]
    docs = []
    for e in entries:
        if isinstance(e, str):
            docs.append(Doc(paper, e, None, build))
        else:
            docs.append(Doc(paper, e["file"], e.get("title"), e.get("build") or build))
    names = [d.name for d in docs]
    if len(set(names)) != len(names):
        raise SystemExit(f"two documents share a stem: {names}")
    return cfg, paper, docs


def doc_of(docs, comment):
    """The document a comment belongs to: its ``doc`` field, or the first document for a record made before desks
    had several."""
    name = comment.get("doc") or docs[0].name
    return next((d for d in docs if d.name == name), docs[0])
