# paperdesk, for the agent at the terminal

You are the editor of the document this desk serves; the reviewer comments on the built PDF from a browser.

1. Start the server if it is not running: `python serve.py` (`./tunnel.sh` for a phone). Its first lines say what it
   serves and whether anything is watching.
2. **Before anything else, arm `python desk.py watch` in a monitor that wakes you on each output line, and re-arm it
   when it expires.** Without it you never learn of a comment: the page shows `unwatched` to the reviewer and the
   comments wait in `comments.jsonl`. The watch prints one line per new comment, per reviewer reply, and per voice note
   whose transcription lands (a spoken comment appears first as `(voice note, transcribing)`).
3. On each line: `python desk.py show ID` gives the anchor (file and line, or `¶` paragraph of a Word document, the
   section, the float, the source lines around it) and the quoted words. Edit the source there, rebuild
   (`desk.py` says nothing about builds; the page's rebuild button or the config's build command does), then
   `python desk.py reply ID "..."` and `python desk.py resolve ID`.
4. `python desk.py list` shows what is open; `python desk.py transcribe` recovers voice notes that got no words.
5. Review agents comment from the source, not the page: `python desk.py add --file F --quote "verbatim words" --severity
   S --category C --author ai:ROLE "text"` (or `add --jsonl FILE`); see `python desk.py` for the full form. Their
   comments start `proposed`; triage with `desk.py list --proposed`, `accept ID ...`, `dismiss ID ... "why"`.
6. Asked for duplicates: `python desk.py dedup` ranks the sentences most alike from different places. For each
   candidate decide whether it is the same claim, a partial overlap or two distinct claims; a definition and its later
   use are not a repeat, nor is a caption saying what a panel shows while the text reports its numbers; a caption
   restating the result is. For a real repeat pick the occurrence that
   stays by the sections' roles (the introduction motivates, the theory derives, the methods say how, the results
   report, the discussion interprets; the supplement holds what the paper only cites) and post one comment on each
   occurrence that should go: `--category duplicate --severity minor` (`major` when the copies disagree, which says
   so), the quote from `dedup.json`, the text naming where the claim stays, and `--suggest` the cut or the shortened
   sentence.

A desk other than the one beside these files: pass its config first, `python desk.py path/to/paperdesk.toml watch`.
A desk with several documents (`[paper] docs`: a paper and its supplement as tabs) prefixes each anchor in
`desk.py list` with the document, `[supplement] sections/appendix.tex:12`; a comment's `doc` says which PDF its page
number counts in, and `desk.py add` lands a source comment on whichever document typeset the quoted line.
