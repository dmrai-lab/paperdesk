# paperdesk

A local review desk for one document, a LaTeX paper or a Word file, or for a paper and its supplement side by
side: the built PDF as it is, a comment from any selection of words or a click on a figure, and every comment
anchored to the source -- file, line or paragraph, enclosing section, float and label -- beside the words selected.
The person editing the source reads the comments from the terminal with the anchor and never searches the
manuscript for the sentence. A LaTeX paper is anchored by SyncTeX; a Word document by the text itself (below).

    python serve.py                # http://127.0.0.1:8765 (paperdesk.toml names the paper; --host 0.0.0.0 to expose; Python 3.11+, or pip install tomli)
    python serve.py other/paperdesk.toml   # a second desk: its comments, audio and auth live beside its config
    python desk.py watch           # KEEP RUNNING: one line per new comment, reply or transcription (the agent's inbox)
    python desk.py list            # the open comments with their anchors
    python desk.py show 3          # one comment with the source lines around its anchor
    python desk.py reply 3 "..."   # answer it; the reply shows in the desk
    python desk.py resolve 3

Pages render as they come into view and are dropped when far off, so a phone holds a few canvases; the "+" and "−"
buttons and a two-finger pinch zoom the pages, which re-render at the new scale. A comment or a reply can be spoken:
the microphone button records (MediaRecorder; a secure origin is needed, which the tunnel gives), the browser's own
dictation fills the text live where it has one (Chrome, Safari), and the audio is uploaded and transcribed on the box
by faster-whisper's small model (CPU, `models/` here) into the comment's text, the dictation kept beside it; the
player sits under the comment in the desk. **Voice needs `pip install faster-whisper` on the box**: without it a
spoken note is stored with its audio but no words, the server says so at start, the status endpoint and the record
button carry the warning, the page alerts when such a note is saved, and `python desk.py transcribe` writes the words
of every wordless note once the model is there (the first note after an install waits for the weights to download;
the server fetches them at start when it can). The model runs on a CUDA device when CTranslate2 sees one (float16;
about five times realtime on a mid-range card) and on the CPU otherwise (int8, half the cores; a little slower than
realtime, so a minute of speech is a minute of waiting); `[voice] device`, `compute_type` and `cpu_threads` override
the choice, and the startup line says where it landed. With a GPU, `model = "large-v3"` is affordable and worth it
where a misheard word could change an edit.

The desk's "rebuild" runs the paper's build command (with SyncTeX) and reloads the PDF when it lands. Comments and
replies are `comments.jsonl` here, one JSON object per line: id, status, text, quote, page, the point in TeX points
from the page's top-left, the selection rectangle, the resolved anchor with the source lines around it, and the
replies. The paper needs `\synctex=1` in its preamble or `-synctex=1` on its engine. The server and the client are
standard library only on Python 3.11 and later; Python 3.10 needs `pip install tomli` for the config, and nothing else.
A comment whose anchor cannot be resolved is still saved, unanchored, with the reason; a voice note is written to
disk before anything else is done with the request, and a request that fails is shown on the page with its reason.

## A paper and its supplement

A desk serves several documents built from one source tree when `[paper] docs` lists them (`config.py`):

    [paper]
    dir = "../paper"
    docs = [{file = "main.tex", title = "paper"}, {file = "supplement.tex", title = "supplement"}]
    build = "latexmk -pdf -synctex=1 -interaction=nonstopmode -halt-on-error {doc}"

The page shows them as tabs in the top bar, each with its count of open comments, one document on show at a
time and each remembering its own reading position. A comment carries the document it was made on (`doc` in
`comments.jsonl`; a record without it is the first document's), its pin appears on that document only, its card
names the document, and "go to" switches tabs when needed. The rebuild button builds every document in order,
`{doc}` standing for each file in turn (a build command without it runs once). `desk.py list` prefixes each anchor
with `[document]` on such a desk, and a review agent's comment from the source lands on whichever document typeset
the quoted line (an appendix file on the supplement), tried in tab order.

Cross-references work when the paper makes them links: with `xr-hyper` in place of `xr`, loaded before
`hyperref` (`\RequirePackage{xr-hyper}` above `\documentclass` when the class loads hyperref itself) and
`\externaldocument{supplement}` as before, every `\ref` into the other document becomes a remote link to its
PDF, which pdf.js reports as `supplement.pdf#dest`; the desk opens that tab and scrolls to the destination. `xr`
alone links to a destination of the same name in the current document, which is the wrong place or none.

## Working with a coding agent

The editor at the terminal is often an agent (Claude Code, or any other), and an agent perceives nothing between its
turns: without a watcher the reviewer's comments land in `comments.jsonl` and nobody knows. So the loop is, in order:

1. `python serve.py` (and `./tunnel.sh` for a phone).
2. **Arm `python desk.py watch` inside whatever wakes the agent** (Claude Code: a Monitor on that command, re-armed
   when it expires) before anything else. It prints one line per new comment, per reply by the reviewer, and per
   voice note whose words land, since a spoken comment first appears as `(voice note, transcribing)` and gets its
   text a moment later. Its heartbeat is what the server and the page call "watched": the status line on the page
   reads `unwatched` while nothing is running, so the reviewer sees the dead drop too, and the server says so at
   start.
3. On each line: `desk.py show ID`, edit the source at the anchor, rebuild, `desk.py reply ID "..."`,
   `desk.py resolve ID`.

`AGENTS.md` in this folder states the same loop for an agent that reads the folder (`CLAUDE.md` includes it).

## Review agents

Agents can review the paper too, and their comments land on the same desk beside yours, anchored the same way. An
agent reads the `.tex`, not the page, so it anchors from the source: it quotes words verbatim from a file and the
desk finds them, takes the line they start on, and asks `synctex view` where that line was typeset, so the comment
gets a pin on the PDF like a clicked one. A quote that is not in the file is refused, which keeps an agent from
commenting on words the paper does not contain.

    python desk.py add --file sections/theory.tex --quote "words copied from the file" \
        --severity major --category correctness --suggest "replacement text" --author ai:theory "what is wrong and why"
    python desk.py add --jsonl review.jsonl     # one {"file", "quote", "text", "severity", ...} object per line
    python desk.py list --proposed [--author ai:theory]
    python desk.py accept 41 42                 # proposed -> open: work to do
    python desk.py dismiss 43 "already stated in Sec. 2"

`POST /api/comments` takes the same keys (`file`, `quote`, `text`, optional `line`, `severity` of
error/major/minor/style, `category`, `suggestion`, `author`) in place of `page`/`x_pt`/`y_pt`. An agent's comment
starts `proposed`: the page shows it with a square pin (dashed until accepted) and its tags, a filter shows yours,
the agents' or those to triage, and accept or dismiss settles it. A dismissed comment is kept, so each reviewer's
hit rate can be counted. `desk.py watch` stays quiet about proposed comments and speaks when the reviewer accepts
one on the page. Writes to `comments.jsonl` take a file lock, so several agents can add at once. Source anchoring
is for LaTeX desks.

## Claims made twice

A long paper edited in many rounds says some things more than once, and the copies drift apart under separate edits.
`desk.py dedup` finds them: every prose sentence of the documents (and every pair of consecutive sentences, since a
claim often spans two) is embedded by a paraphrase-trained sentence model, the paper's mean embedding is removed so
that the topic every sentence shares does not make everything look alike, and pairs from different places are ranked
by cosine. Word overlap is printed beside it: high on both is a copy, a high cosine on few shared words a paraphrase.
Claims made three times or more are listed as clusters.

    pip install sentence-transformers           # CPU is enough: a 50-page paper embeds in about a minute
    python desk.py dedup --top 60               # ranked pairs and clusters; dedup.json holds each side's quote

The ranking is recall: a definition in the theory and its use in the results come out close by design. Whoever judges
the candidates (an agent, `AGENTS.md`) posts each real repeat on the occurrence that should go, as a `proposed`
comment of category `duplicate` naming where the claim stays, so the reviewer settles it on the page like any other.
The sentence model is `[dedup] model` in the config (default `sentence-transformers/all-mpnet-base-v2`) and its CPU
threads `[dedup] threads` (8); embeddings are cached by sentence in `.dedup_cache.npz`, so a run after an edit embeds
only what changed.

## Citations against the field

Prose written with an agent tends to state the field's context without saying who established it. Two commands
measure that against papers of the paper's own field, named by arXiv identifier in the config:

    [citations]
    corpus = ["1907.12685", "2001.06529", "2007.04403", ...]    # 15 to 30 papers like yours; PDF-only submissions are skipped

    python desk.py citestats            # citation keys per 1000 words, per section class, against the field's median and quartiles
    python desk.py uncited --top 80     # the uncited sentences the field would have cited; uncited.json holds each one's quote

Both read the corpus's LaTeX sources (fetched once from arXiv into `.citecorpus/`, three seconds apart as arXiv
asks; nothing of the paper is sent anywhere) through the same sentence splitter as the paper, so both sides are
counted alike. Sections are classed by their titles: introduction, theory, methods, results, discussion, conclusion,
appendix (after `\appendix`, or any document after the first), the front matter, and the statements (availability,
funding, contributions), which are left out. `citestats` adds the share of paragraphs that cite nothing.

`uncited` learns what the field cites from the corpus itself: each of its sentences is embedded by CiteBERT
(`copenlu/citebert`, SciBERT trained further on cite-worthiness), labelled by whether it cites, and a logistic
regression on the embedding and the section class learns the label. The area under the ROC curve on each corpus
paper held out says how well it learnt. An uncited sentence of the paper is flagged when it scores above the median
of the paper's own cited sentences, and whether its paragraph cites elsewhere is shown beside it. A flag is a
question, not a verdict: the paper's own results need no citation. Whoever judges (`AGENTS.md`) posts the real gaps
as `proposed` comments of category `uncited`. Needs `pip install sentence-transformers`; `[citations] model` and
`threads` as for `dedup`.

A paper can cite little and still miss no source sentence by sentence: it may simply say little about anyone else's
work. `readinglist` shows what that leaves out:

    python desk.py readinglist --min 3  # the works 3 or more of the field's papers cite that the paper does not

Each paper's references are read from its `.bbl` (biblatex or `\\bibitem`), an inline `thebibliography`, or its `.bib`
restricted to the keys it cites, and identified by DOI; a reference without one is looked up with Crossref's
bibliographic search and kept only when the title Crossref returns stands in the reference's own words. Only
reference strings go to Crossref (`[citations] mailto` puts an address on the requests for Crossref's faster
pool); the answers are cached in `.citecorpus/crossref.json`, so the first run takes some minutes and later ones
seconds. The list, with the field papers citing each work, is written to `readinglist.json`. It is a reading list:
which of those works the paper should engage with, and where, is the author's call.

## Consistency and the bibliography

Two passes need no model and no corpus:

    python desk.py check      # labels, citations, numbers and terms across the whole paper
    python desk.py refcheck   # every .bib entry against the record its DOI, arXiv id or title finds

`check` lists what a reader trips on and a build does not always say: a `\ref` no document defines (a supplement's
labels count only where `\externaldocument` loads them), a label defined twice, a figure or table the text never
mentions; a cited key no `.bib` holds, an entry nothing cites, a key defined twice, one work under two keys; a symbol
given two or three values in inline maths, a number of the abstract the rest of the paper never states in that unit
and to that rounding (numbers written as words count: "one percent" is 1%), the near-duplicate claims of
`dedup.json` whose sides state different numbers; an acronym defined twice in the body, used before its definition
or never after it, and words spelt two ways. Each finding names a place and a quote for `desk.py add`; all of it is
recall for a reader to judge. `check.json` also holds every float's caption beside the sentences that refer to it,
for the comparison no rule makes. Run `dedup` first for the duplicate numbers.

`refcheck` asks doi.org (Crossref, DataCite and the other agencies alike) what each DOI is, arXiv what each arXiv id
is, and Crossref, then arXiv, for an entry with neither, by its title. It reports an unregistered DOI, a work found
nowhere, and a title, first author or year that is not the record's, each on the first line that cites the entry.
Only the bibliography's own metadata goes out; answers are cached in `.refcheck.json`.

## A Word document

Point `main` at a `.docx` (or set `kind = "docx"`) and make `build` the conversion to PDF, which LibreOffice does
headless (`soffice --headless --convert-to pdf --outdir . report.docx`; no LibreOffice on the machine, then unpack
its .deb or .rpm bundle into a folder and name that `soffice`). The desk builds the PDF at start when it is missing
or older than the document, and the "rebuild" button converts again after an edit in Word. A click or a selection
resolves through the PDF's text: the words selected, or the line of text nearest the click, are searched in the
document's paragraphs read from `word/document.xml` (body order, table cells included), the longest run of those
words that occurs deciding the paragraph; identical paragraphs resolve by their rank on the page. The anchor is the
paragraph's number (`report.docx:¶12` in the desk and `desk.py`), its heading path from the `Heading n` styles, the
table it sits in or the image beside it, the matched words and their offset, and the neighbouring paragraphs.
`pdftotext` (poppler) reads the PDF's words with their boxes. Excel and PowerPoint are not covered: a workbook has
no PDF worth anchoring to, and a deck's PDF would need a per-shape resolver.

## From anywhere

`./tunnel.sh` opens a Cloudflare quick tunnel to the server and prints its address (a random `trycloudflare.com`
name that changes at every start; `tunnel.url` keeps the current one). The server asks for the user and password in
`auth.json` (not committed) whenever that file exists. The layout folds on a phone: the page fills the width, a
selection raises a floating "comment" button, a tap on a figure opens the popup directly, and the comments sit
behind the "comments" button.
