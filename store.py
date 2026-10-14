"""The comment record, ``comments.jsonl``: one JSON object per line, written whole and atomically.

The server and every ``desk.py`` process (several reviewers adding at once among them) change it under one lock: a
thread lock for the server's own threads and an ``flock`` on ``comments.lock`` beside it for the other processes, so
no read-modify-write loses another's comment.
"""
import fcntl
import json
import os
import threading
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


def decide(c, k, state, who):
    """Edit ``k`` of a shortening (every undecided one when ``k`` is "all") accepted, rejected or undecided again
    (``state`` "accept", "reject" or None; an applied edit stays applied). The comment is open work once any edit is
    accepted, dismissed once every edit is rejected, and proposed while none is decided."""
    st = c.setdefault("edit_state", [None] * len(c.get("edits") or []))
    for i in (range(len(st)) if k == "all" else [int(k)]):
        if st[i] != "applied" and (k != "all" or st[i] is None):
            st[i] = state if state in ("accept", "reject") else None
    if any(s in ("accept", "applied") for s in st):
        if c["status"] in ("proposed", "dismissed"):
            c["status"] = "open"; c["accepted_by"] = who
    elif st and all(s == "reject" for s in st):
        c["status"] = "dismissed"
    elif c["status"] in ("open", "dismissed") and not any(st):
        c["status"] = "proposed"; c.pop("accepted_by", None)
