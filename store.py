"""The comment record, ``comments.jsonl``: one JSON object per line, written whole and atomically.

The server and every ``desk.py`` process (several reviewers adding at once among them) change it under one lock: a
thread lock for the server's own threads and an ``flock`` on ``comments.lock`` beside it for the other processes, so
no read-modify-write loses another's comment.
"""
import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_THREADS = threading.Lock()


def load(store: Path):
    if not store.exists():
        return []
    return [json.loads(l) for l in store.read_text().splitlines() if l.strip()]


def save(store: Path, items):
    tmp = store.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in items))
    os.replace(tmp, store)


@contextmanager
def locked(store: Path):
    """The record, exclusively: ``with locked(STORE) as items: ...; save(STORE, items)``."""
    with _THREADS, open(store.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield load(store)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def next_id(items):
    return 1 + max([c["id"] for c in items], default=0)


REASONS = ("changes meaning", "reads worse", "too terse", "loses emphasis", "not my voice")   # why an edit was left
PER_EDIT = ("edit_state", "edit_reason", "edit_new", "edit_meta")   # a shortening's lists, one entry per edit


def per_edit(c):
    """The shortening's per-edit lists, each as long as its edits: ``edit_state`` (None, "accept", "reject" or
    "applied"), ``edit_reason`` (why one was left), ``edit_new`` (the author's own wording of an accepted one) and
    ``edit_meta`` (what the pipeline knew of it, the checker's verdict among it, never shown on the page)."""
    n = len(c.get("edits") or [])
    for k in PER_EDIT:
        c[k] = (list(c.get(k) or []) + [None] * n)[:n]
    return c


def decide(c, k, state, who, reason=None, new=None):
    """Edit ``k`` of a shortening (every undecided one when ``k`` is "all") accepted, rejected or undecided again
    (``state`` "accept", "reject" or None; an applied edit stays applied), a rejection with its ``reason`` and an
    acceptance with the author's own wording ``new``. The comment is open work once any edit is accepted, dismissed once
    every edit is rejected, and proposed while none is decided. Returns the record of each edit whose decision changed,
    for the decisions log."""
    per_edit(c)
    st, why, mine = c["edit_state"], c["edit_reason"], c["edit_new"]
    changed = []
    for i in (range(len(st)) if k == "all" else [int(k)]):
        if st[i] == "applied" or (k == "all" and st[i] is not None):
            continue
        s = state if state in ("accept", "reject") else None
        r = reason if s == "reject" and reason else None
        w = new if s == "accept" and new is not None and new.strip() != c["edits"][i][1].strip() else None
        if (st[i], why[i], mine[i]) != (s, r, w):
            st[i], why[i], mine[i] = s, r, w
            changed.append(i)
    if any(s in ("accept", "applied") for s in st):
        if c["status"] in ("proposed", "dismissed"):
            c["status"] = "open"; c["accepted_by"] = who
    elif st and all(s == "reject" for s in st):
        c["status"] = "dismissed"
    elif c["status"] in ("open", "dismissed") and not any(st):
        c["status"] = "proposed"; c.pop("accepted_by", None)
    return [record(c, i, "rest" if k == "all" else "one", who) for i in changed]


def wording(c, k):
    """The text edit ``k`` puts in place of its old words: the author's own when they rewrote it."""
    return (c.get("edit_new") or [None] * (k + 1))[k] or c["edits"][k][1]


def record(c, k, via, who, state=None):
    """One line of the decisions log: edit ``k`` of shortening ``c`` in its paragraph as it stood, what the pipeline
    proposed and knew of it, and what the author made of it (``via`` "one", "rest", "apply" or "source")."""
    per_edit(c)
    a = c.get("anchor") or {}; old, new = c["edits"][k]
    flags = next((f for o, f in c.get("flags") or [] if o == old), None)
    return dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), id=c["id"], k=k, file=a.get("file"), line=a.get("line"),
                doc=c.get("doc"), **{"pass": c.get("pass")}, original=c.get("original"), old=old, new=new,
                wording=c["edit_new"][k], state=state or c["edit_state"][k], via=via, reason=c["edit_reason"][k],
                meta=c["edit_meta"][k], flags=flags, who=who)


def log(store: Path, records):
    """Append the records to ``decisions.jsonl`` beside the comments: every decision ever made, never rewritten, so a
    later pass can learn from the author's choices (``desk.py decisions``)."""
    if records:
        with open(store.with_name("decisions.jsonl"), "a") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
