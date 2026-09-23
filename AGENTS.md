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

A desk other than the one beside these files: pass its config first, `python desk.py path/to/paperdesk.toml watch`.
