"""A shortening decided edit by edit: the comment's status follows the decisions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store  # noqa: E402


def shortening(n=3):
    return dict(id=1, status="proposed", edits=[[f"old {k}", f"new {k}"] for k in range(n)], edit_state=[None] * n)


def test_one_accepted_edit_makes_it_work_and_undoing_it_returns_it():
    c = shortening()
    store.decide(c, 1, "accept", "rutger")
    assert c["edit_state"] == [None, "accept", None] and c["status"] == "open" and c["accepted_by"] == "rutger"
    store.decide(c, 1, None, "rutger")
    assert c["status"] == "proposed" and "accepted_by" not in c


def test_rejecting_every_edit_dismisses_and_all_decides_only_the_undecided():
    c = shortening()
    store.decide(c, 0, "accept", "rutger"); store.decide(c, "all", "reject", "rutger")
    assert c["edit_state"] == ["accept", "reject", "reject"] and c["status"] == "open"
    c = shortening(); store.decide(c, "all", "reject", "rutger")
    assert c["status"] == "dismissed"


def test_an_applied_edit_stays_applied():
    c = shortening(); c["edit_state"] = ["applied", None, None]; c["status"] = "open"
    store.decide(c, 0, "reject", "rutger"); store.decide(c, "all", "reject", "rutger")
    assert c["edit_state"] == ["applied", "reject", "reject"] and c["status"] == "open"


def test_each_changed_decision_is_one_record_with_how_it_was_made(tmp_path):
    c = shortening(); c["original"] = "old 0 old 1 old 2"; c["edit_meta"] = [{"checker": "keep"}, {"checker": "drop"}]
    [r] = store.decide(c, 1, "reject", "rutger", reason="reads worse")
    assert (r["k"], r["state"], r["via"], r["reason"], r["meta"], r["original"]) == (1, "reject", "one", "reads worse", {"checker": "drop"}, "old 0 old 1 old 2")
    assert store.decide(c, 1, "reject", "rutger", reason="reads worse") == []           # nothing changed, nothing logged
    rest = store.decide(c, "all", "accept", "rutger")
    assert [(r["k"], r["via"]) for r in rest] == [(0, "rest"), (2, "rest")] and c["edit_reason"] == [None, "reads worse", None]
    store.log(tmp_path / "comments.jsonl", [r] + rest)
    assert len((tmp_path / "decisions.jsonl").read_text().splitlines()) == 3


def test_the_authors_wording_is_what_gets_applied_and_undeciding_drops_it():
    c = shortening()
    [r] = store.decide(c, 0, "accept", "rutger", new="my own 0")
    assert r["wording"] == "my own 0" and store.wording(c, 0) == "my own 0" and store.wording(c, 1) == "new 1"
    store.decide(c, 0, "accept", "rutger", new="new 0")                                 # the proposal itself: no wording
    assert c["edit_new"][0] is None
    store.decide(c, 0, "reject", "rutger", reason="too terse"); store.decide(c, 0, None, "rutger")
    assert c["edit_reason"][0] is None and c["edit_state"][0] is None


def test_per_edit_lists_follow_the_edits():
    c = dict(id=1, status="proposed", edits=[["a", "b"], ["c", "d"]], edit_state=["accept"])
    store.per_edit(c)
    assert c["edit_state"] == ["accept", None] and c["edit_reason"] == [None, None] and c["edit_meta"] == [None, None]
