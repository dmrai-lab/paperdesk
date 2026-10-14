# paperdesk, for the agent at the terminal

You are the editor of the document this desk serves; the reviewer comments on the built PDF from a browser.

1. Start the server if it is not running: `python serve.py` (`./tunnel.sh` for a phone). Its first lines say what it
   serves and whether anything is watching.
2. **Before anything else, arm `python desk.py watch` in a monitor that wakes you on each output line, and re-arm it
   when it expires.** Without it you never learn of a comment: the page shows `unwatched` to the reviewer and the
   comments wait in `comments.jsonl`. The watch prints one line per new comment, per reviewer reply, per voice note
   whose transcription lands (a spoken comment appears first as `(voice note, transcribing)`), and per live comment a
   rebuild left stale: its words are gone from the paper.
3. On each line: `python desk.py show ID` gives the anchor (file and line, or `¶` paragraph of a Word document, the
   section, the float, the source lines around it) and the quoted words. Edit the source there, rebuild
   (`desk.py` says nothing about builds; the page's rebuild button or the config's build command does), then
   `python desk.py reply ID "..."` and `python desk.py resolve ID`. An edit that cuts or rewrites words other
   comments sit on leaves them stale: resolve each one your edit settled (reply with what became of it), and reply on
   the others where their point now applies; `python desk.py list --stale` lists them with the commit that cut them.
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
7. Asked for missing citations: `python desk.py citestats` shows where the paper cites less than its field, and
   `python desk.py uncited` lists the uncited sentences that read to the field like ones that cite. For each, read
   its paragraph and decide: a claim of prior work, a known method, a dataset, a tool or an established fact needs a
   citation; the paper's own derivation, measurement or result does not; a citation elsewhere in the paragraph that
   covers it is enough. Post each real gap as `--category uncited --severity minor` (`major` for a claim the paper's
   argument rests on), the quote from `uncited.json`, the text saying what kind of source is needed (the original
   method, a review, the dataset's paper, the tool's paper), and in `--suggest` a reference only when you know it
   exists: the author reads at least its abstract before it is cited. `python desk.py readinglist` lists the works
   the field cites most that the paper does not; report it to the author as a reading list, never as citations to add.
8. Asked for consistency: run `python desk.py dedup`, then `python desk.py check`. Post the cross-reference and
   citation findings as they are (`--category consistency`; an undefined reference or a missing key `major`), except
   the ones on a `.bib`, which have no quote: list those to the author. A symbol with two values or two near-duplicate
   claims with different numbers is a finding only when both places speak of the same quantity in the same
   experiment; then say which value the rest of the paper supports. A spelling pair is worth a comment for a name or a
   term of art (Monte Carlo, Monte-Carlo), not for a compound hyphenated before its noun and open after it. Then read
   the abstract against the results, and each caption in `check.json` against the sentences that refer to it: a
   number, a direction of effect or a claim that one states and the other contradicts or does not support is `major`.
9. Asked to check the references: `python desk.py refcheck`. An unregistered DOI or an article found nowhere is
   `major` (`--category reference`): find the right DOI or the real work, and never invent one. A title, author or year
   that disagrees: look at the record, then say which side is wrong; a book's other edition or a paper's online year is
   a note, not an error.
10. Asked for brevity: `python desk.py brevity`, then give every paragraph of `paragraphs` in `brevity.json` to a
   trimmer (contiguous chunks of about 3 500 words, one agent each), then each chunk's kept file to an independent
   checker. The trimmer proposes, per paragraph, every shortening that keeps every claim: each sentence of 35 words
   or more rewritten shorter, each sentence or clause that restates the paragraph cut, then the word trims (the noun
   behind "of" as a possessive or modifier, a "which is" dropped, a stock phrase shortened, the action made the verb);
   the `sites` are hints, not the limit. Each edit is an `[old, new]` pair with `old` verbatim from `original` and
   whole brace groups and maths; a sentence rewrite is one edit. Rows carry `file`, `line`, `quote`, `original` and
   `needs` from `brevity.json`, the `edits`, `category` `brevity`, `severity` `style`, `author` `ai:brevity`; the
   trimmer runs `desk.py brevity --verify FILE --flag` until nothing is refused. The checker reads each edit in its
   paragraph and drops it when a claim gets stronger or weaker, a referent or relation (so, because, while) changes,
   cut text carried anything the paragraph does not keep, or the new text reads worse; a flagged edit stays only when
   its dropped word was redundant; it verifies its file again. Then `desk.py brevity --measure` on all the checked
   rows: report the pages before and after and give the author the marked PDFs. Post to the desk only what the
   author asks for, one comment per paragraph, at most 25 a run unless asked for more. Never apply an edit to the
   source yourself; the author does.
A desk other than the one beside these files: pass its config first, `python desk.py path/to/paperdesk.toml watch`.
A desk with several documents (`[paper] docs`: a paper and its supplement as tabs) prefixes each anchor in
`desk.py list` with the document, `[supplement] sections/appendix.tex:12`; a comment's `doc` says which PDF its page
number counts in, and `desk.py add` lands a source comment on whichever document typeset the quoted line.
