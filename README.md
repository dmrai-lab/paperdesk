# paperdesk

A local review desk for one document, a LaTeX paper or a Word file: the built PDF as it is, a comment from any
selection of words or a click on a figure, and every comment anchored to the source -- file, line or paragraph,
enclosing section, float and label -- beside the words selected. The person editing the source reads the comments
from the terminal with the anchor and never searches the manuscript for the sentence. A LaTeX paper is anchored by
SyncTeX; a Word document by the text itself (below).

    python serve.py                # http://127.0.0.1:8765 (paperdesk.toml names the paper; --host 0.0.0.0 to expose; Python 3.11+, or pip install tomli)
    python serve.py other/paperdesk.toml   # a second desk: its comments, audio and auth live beside its config
    python desk.py list            # the open comments with their anchors
    python desk.py show 3          # one comment with the source lines around its anchor
    python desk.py reply 3 "..."   # answer it; the reply shows in the desk
    python desk.py resolve 3
    python desk.py watch           # one line per new comment, for a monitor

Pages render as they come into view and are dropped when far off, so a phone holds a few canvases; the "+" and "−"
buttons and a two-finger pinch zoom the pages, which re-render at the new scale. A comment or a reply can be spoken:
the microphone button records (MediaRecorder; a secure origin is needed, which the tunnel gives), the browser's own
dictation fills the text live where it has one (Chrome, Safari), and the audio is uploaded and transcribed on the box
by faster-whisper's small model (CPU, `models/` here) into the comment's text, the dictation kept beside it; the
player sits under the comment in the desk. **Voice needs `pip install faster-whisper` on the box**: without it a
spoken note is stored with its audio but no words, the server says so at start, the status endpoint and the record
button carry the warning, the page alerts when such a note is saved, and `python desk.py transcribe` writes the words
of every wordless note once the model is there (the first note after an install waits for the weights to download;
the server fetches them at start when it can).

The desk's "rebuild" runs the paper's build command (with SyncTeX) and reloads the PDF when it lands. Comments and
replies are `comments.jsonl` here, one JSON object per line: id, status, text, quote, page, the point in TeX points
from the page's top-left, the selection rectangle, the resolved anchor with the source lines around it, and the
replies. The paper needs `\synctex=1` in its preamble or `-synctex=1` on its engine. The server and the client are
standard library only on Python 3.11 and later; Python 3.10 needs `pip install tomli` for the config, and nothing else.
A comment whose anchor cannot be resolved is still saved, unanchored, with the reason; a voice note is written to
disk before anything else is done with the request, and a request that fails is shown on the page with its reason.

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
