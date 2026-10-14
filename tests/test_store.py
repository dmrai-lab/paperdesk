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
