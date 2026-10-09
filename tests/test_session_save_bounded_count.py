"""``Session.save()`` must not read and parse the whole sidecar to count messages.

Production failure (2026-10-08): ``Session.save()`` ran
``self.path.read_text(encoding='utf-8')`` + ``json.loads(...)`` on the ENTIRE
previous sidecar on every save, purely to compare message counts for the #1558
backup safeguard. Measured amplification is ~3.7x the file size in peak RSS
(``read_text`` alone ~3.0x, because the sidecar is written with
``ensure_ascii=False`` so the decoded str is UCS-2 at 2 bytes/char). With a
6.23 GiB sidecar that made ``save()`` the direct allocator behind three GLOBAL
oom-kills at ~115 GB RSS on a 119 GB box -- which is why the file's mtime kept
advancing right up to each kill: the session was saving itself mid-turn, not
being opened.

``save()`` already writes ``message_count`` into the metadata prefix BEFORE the
messages array (#5854), so a bounded 64 KiB read answers the question.

These tests pin:
  * the whole sidecar is never read during a save,
  * the count is right for a modern sidecar,
  * legacy sidecars (no ``message_count`` in the prefix) degrade to the cheap
    index rather than a full parse,
  * malformed / truncated / empty metadata returns -1 without raising,
  * the #1558 backup still fires on a shrinking save and is byte-identical,
  * a growing save still produces no backup,
  * the empty-active-snapshot refusal still works off the bounded count.
"""

import json
import pathlib

import pytest

import api.models as models
from api.models import Session, _sidecar_message_count_bounded


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", d)
    return d


def _write_session(session_dir, sid, n_messages, *, extra_top=None):
    s = Session(session_id=sid, messages=[{"role": "user", "content": f"m{i}"} for i in range(n_messages)])
    s.save(skip_index=True)
    if extra_top:
        data = json.loads((session_dir / f"{sid}.json").read_text())
        data.update(extra_top)
        (session_dir / f"{sid}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return s


# ── the core regression ────────────────────────────────────────────────────

def test_save_never_reads_the_whole_sidecar(session_dir, monkeypatch):
    """The allocation that caused the OOM must be gone from the save path."""
    sid = "nofullread"
    _write_session(session_dir, sid, 5)
    target = (session_dir / f"{sid}.json").resolve()

    reads = []
    real_read_text = pathlib.Path.read_text

    def spy(self, *a, **kw):
        if self.resolve() == target:
            reads.append(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", spy)

    s2 = Session(session_id=sid, messages=[{"role": "user", "content": f"m{i}"} for i in range(9)])
    s2.save(skip_index=True)

    assert reads == [], f"save() still read the full sidecar {len(reads)}x: {reads}"
    assert len(json.loads((session_dir / f"{sid}.json").read_text())["messages"]) == 9


# ── bounded count across sidecar shapes ────────────────────────────────────

def test_bounded_count_modern_sidecar(session_dir):
    _write_session(session_dir, "modern", 7)
    assert _sidecar_message_count_bounded(session_dir / "modern.json", "modern") == 7


def test_bounded_count_legacy_without_message_count(session_dir, monkeypatch):
    """Legacy prefix has no message_count -> cheap index, never a full parse."""
    _write_session(session_dir, "legacy", 4)
    p = session_dir / "legacy.json"
    data = json.loads(p.read_text())
    data.pop("message_count", None)
    data.pop("anchor_scene_index", None)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    monkeypatch.setattr(models, "_lookup_index_message_count", lambda sid: 4)
    # Session.load must NOT be used as a fallback here -- that is the OOM path.
    monkeypatch.setattr(
        Session, "load",
        classmethod(lambda cls, sid: pytest.fail("full Session.load() during bounded count")),
    )
    assert _sidecar_message_count_bounded(p, "legacy") == 4


def test_bounded_count_malformed_json_returns_minus_one(session_dir):
    p = session_dir / "broken.json"
    p.write_text('{"session_id": "broken", "message_count": ')   # truncated mid-value
    assert _sidecar_message_count_bounded(p, "broken") == -1


def test_bounded_count_empty_file_returns_minus_one(session_dir):
    p = session_dir / "empty.json"
    p.write_text("")
    assert _sidecar_message_count_bounded(p, "empty") == -1


def test_bounded_count_non_integer_message_count(session_dir):
    _write_session(session_dir, "weird", 3, extra_top={"message_count": "not-a-number"})
    # falls through to the index; with no index entry that is -1, and crucially no raise
    assert _sidecar_message_count_bounded(session_dir / "weird.json", "weird") in (-1, 3)


# ── #1558 backup behaviour must be preserved exactly ───────────────────────

def test_shrinking_save_writes_byte_identical_backup(session_dir):
    sid = "shrink"
    _write_session(session_dir, sid, 10)
    p = session_dir / f"{sid}.json"
    before = p.read_bytes()

    s2 = Session(session_id=sid, messages=[{"role": "user", "content": "m0"}])
    s2.save(skip_index=True)

    bak = p.with_suffix(".json.bak")
    assert bak.exists(), "shrinking save must leave a #1558 backup"
    assert bak.read_bytes() == before, "streamed backup is not byte-identical to the previous sidecar"
    assert len(json.loads(p.read_text())["messages"]) == 1


def test_growing_save_creates_no_backup(session_dir):
    sid = "grow"
    _write_session(session_dir, sid, 2)
    p = session_dir / f"{sid}.json"
    s2 = Session(session_id=sid, messages=[{"role": "user", "content": f"m{i}"} for i in range(6)])
    s2.save(skip_index=True)
    assert not p.with_suffix(".json.bak").exists(), "growing saves must not produce backups"


def test_refuses_to_overwrite_messages_with_empty_active_snapshot(session_dir):
    """The guard that consumes existing_msg_count must still fire."""
    sid = "guard"
    _write_session(session_dir, sid, 5)
    p = session_dir / f"{sid}.json"

    s2 = Session(session_id=sid, messages=[], active_stream_id="stream-1")
    s2.save(skip_index=True)

    assert len(json.loads(p.read_text())["messages"]) == 5, (
        "an empty snapshot with an active stream overwrote real messages"
    )
