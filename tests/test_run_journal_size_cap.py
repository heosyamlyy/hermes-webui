"""The run journal must stop growing at a cap instead of without bound.

The READ side has been bounded since #5854 (``_SESSION_REPLAY_MAX_BYTES`` =
4 MiB, ``_SESSION_REPLAY_MAX_ROWS`` = 4096, chunked reader). The WRITE side had
no cap, no rotation and no age prune -- the only deletion was session delete.
On 2026-10-08 ``~/.hermes/webui/sessions/_run_journal`` had reached 30 GB, of
which 12 GB belonged to a single run whose session was being flooded with
duplicated reasoning rows, on a filesystem already at 94%.

Invariants pinned here:
  * a run stops growing once it crosses the cap,
  * exactly one ``apperror`` marker records why,
  * TERMINAL events still get through, so a capped run closes cleanly and
    recovery is not left waiting on a run that never ends,
  * dropped events do not burn sequence numbers (no replay gaps),
  * the cap can be disabled (0) and is env-tunable.
"""

import json

import pytest

import api.run_journal as rj


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(rj, "_capped_run_paths", set())
    return tmp_path


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run_path(journal, sid="s1", rid="r1"):
    return rj._run_path(sid, rid, session_dir=journal)


def test_run_journal_stops_growing_at_the_cap(journal, monkeypatch):
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 4096)
    big = {"blob": "x" * 1024}
    for i in range(400):
        rj.append_run_event("s1", "r1", "token", dict(big, i=i), session_dir=journal)

    p = _run_path(journal)
    size = p.stat().st_size
    assert size < 4096 * 4, f"journal grew to {size} bytes despite a 4096-byte cap"

    evs = _events(p)
    markers = [e for e in evs if (e.get("payload") or {}).get("error") == "run_journal_size_cap"]
    assert len(markers) == 1, f"expected exactly one cap marker, got {len(markers)}"
    assert markers[0]["terminal"] is True, "the cap marker must be terminal so the run closes"


def test_terminal_events_still_recorded_after_cap(journal, monkeypatch):
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 2048)
    for i in range(200):
        rj.append_run_event("s1", "r1", "token", {"blob": "y" * 512, "i": i}, session_dir=journal)
    rj.append_run_event("s1", "r1", "stream_end", {"ok": True}, session_dir=journal)

    evs = _events(_run_path(journal))
    assert any(e["event"] == "stream_end" for e in evs), (
        "a terminal event was dropped by the cap -- the run would never close"
    )


def test_dropped_events_do_not_burn_sequence_numbers(journal, monkeypatch):
    """Replay reads by seq; gaps would look like lost events."""
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 2048)
    for i in range(200):
        rj.append_run_event("s1", "r1", "token", {"blob": "z" * 512, "i": i}, session_dir=journal)
    rj.append_run_event("s1", "r1", "stream_end", {"ok": True}, session_dir=journal)

    seqs = [e["seq"] for e in _events(_run_path(journal))]
    assert seqs == sorted(seqs), f"sequence numbers are not monotonic: {seqs[:20]}"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"gap in sequence numbers: {seqs}"


def test_dropped_event_return_value_is_well_formed(journal, monkeypatch):
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 1024)
    for i in range(50):
        rj.append_run_event("s1", "r1", "token", {"blob": "q" * 256, "i": i}, session_dir=journal)
    out = rj.append_run_event("s1", "r1", "token", {"blob": "q" * 256}, session_dir=journal)
    assert isinstance(out, dict)
    for key in ("version", "event_id", "seq", "run_id", "session_id", "event", "created_at"):
        assert key in out, f"dropped-event return value is missing {key!r}"
    assert out.get("dropped") == "run_journal_size_cap"


def test_cap_disabled_by_zero(journal, monkeypatch):
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 0)
    for i in range(100):
        rj.append_run_event("s1", "r1", "token", {"blob": "w" * 512, "i": i}, session_dir=journal)
    evs = _events(_run_path(journal))
    assert len(evs) == 100, f"cap=0 must disable the cap, got {len(evs)} events"
    assert not any((e.get("payload") or {}).get("error") == "run_journal_size_cap" for e in evs)


def test_normal_sized_run_is_untouched(journal, monkeypatch):
    """The cap must be invisible to ordinary runs."""
    monkeypatch.setattr(rj, "_RUN_JOURNAL_RUN_MAX_BYTES", 256 * 1024 * 1024)
    for i in range(50):
        rj.append_run_event("s1", "r1", "token", {"i": i}, session_dir=journal)
    rj.append_run_event("s1", "r1", "stream_end", {"ok": True}, session_dir=journal)
    evs = _events(_run_path(journal))
    assert len(evs) == 51
    assert not any((e.get("payload") or {}).get("error") == "run_journal_size_cap" for e in evs)
