# paperdesk

A local review desk for a LaTeX paper you are writing with someone who edits the source for you, whether a
co-author at a terminal or a coding agent such as Claude Code.

You read the built PDF as it is, in the browser, on a laptop or a phone. You select a few words, or tap a
figure, and write or speak a comment. Every comment is anchored by SyncTeX to the source that typeset that
spot: the file, the line, the enclosing section, the figure or table label if you clicked inside one, and
the words you selected. The person editing reads the comments from the terminal with that anchor in front
of them, edits at that spot, answers, and marks the comment resolved; the answer shows up in your sidebar.
Nobody searches the manuscript for the sentence.

No LaTeX-to-HTML conversion, no database, no account: one Python file serves the page, one JSON line per
comment holds the record, and the same file feeds the page and the terminal.

## Setup

Requirements: Python 3.11+, a LaTeX distribution with `latexmk` and `synctex` (any TeX Live), and the
`synctex` output, which means either `\synctex=1` in your preamble or `-synctex=1` on your engine (the
example build command passes it).

```
git clone https://github.com/dmrai-lab/paperdesk
cd paperdesk
cp paperdesk.example.toml paperdesk.toml      # point [paper] at your sources, name [people]
python serve.py                               # http://127.0.0.1:8765
```

Optional:

- **Voice notes**: `pip install faster-whisper` (and `ffmpeg` on the path). The microphone button then records
  in the browser, the browser's own dictation fills the text live where it has one (Chrome, Safari), and the
  audio is uploaded and transcribed on your machine by Whisper's small model on the CPU, told the jargon in
  `[voice] glossary`. Measured on synthesised technical sentences: the small model with a glossary matches
  large-v3 (about 10 % word error, mostly formatting) at a third of the time. A secure origin is needed for
  the microphone, which localhost and the tunnel both are.
- **From anywhere**: `./tunnel.sh` opens a Cloudflare quick tunnel (`cloudflared` on the path) and prints a
  random `trycloudflare.com` address. Put a user and password in `auth.json` (`{"user": "...", "password": "..."}`)
  and the server asks for them on every route.

## Reading and answering from the terminal

```
python desk.py list            # the open comments with their anchors and quotes
python desk.py show 3          # one comment in full, with the source lines around its anchor
python desk.py reply 3 "..."   # answer it, as the editor named in paperdesk.toml
python desk.py resolve 3
python desk.py watch           # one line per new comment, reply or transcribed voice note; for a monitor
```

With a coding agent: point it at this folder, have it run `desk.py watch` under a file or process monitor
so each new comment reaches it as an event, and let it answer with `desk.py reply` and `resolve`. The
anchor tells it where to edit; the quoted words pin the sentence.

## What is where

- `serve.py`: the server (standard library only), the SyncTeX resolution, the build endpoint, the voice upload
  and transcription.
- `desk.py`: the terminal client.
- `static/`: the page, with pdf.js from a CDN; pages render as they scroll into view, zoom by buttons or a
  two-finger pinch, and fold to a phone layout below 820 px.
- `comments.jsonl`: the record, one JSON object per line: id, status, text, quote, page, the point in TeX
  points from the page's top-left, the selection rectangle, the resolved anchor with the source lines around
  it, the replies, and for a voice note the audio file and how it was transcribed. Commit it if you want the
  review in the paper's history; it is ignored by default.
- `audio/`, `models/`: voice notes and the Whisper model, both ignored.

## Limits worth knowing

SyncTeX resolves a point to a source line, which for a long paragraph is the paragraph's line; the quoted
words pin the sentence. A click on a figure resolves to its `\includegraphics` line and the label of its
float. Comments are per paper, per folder: one desk serves one `paperdesk.toml`.

MIT licence.
