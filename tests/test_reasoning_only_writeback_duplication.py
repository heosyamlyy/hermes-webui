"""Settled reasoning-only assistant rows must not multiply across writebacks.

Production failure reproduced here (2026-10-08): hermes-webui.service was
OOM-killed three times, anon-rss ~115 GB on a 119 GB box (`global_oom`, which
also threatened docker). Session ``27b42e26d196.json`` had reached 6.23 GiB
holding 1,975,499 assistant rows against 31 user rows. Measured on the
quarantined original: 1,598,124 of 1,598,546 sampled empty-content rows share a
single ``id`` (117), with only 10 distinct ids and 39 distinct content
signatures -- i.e. the bulk are identical copies of ONE message. It was never a
retry loop, despite ``finish_reason: "incomplete"`` on those rows.

Three behaviours interact, and must be read together:

1. ``_restore_display_reasoning_metadata`` re-inserts ``copy.deepcopy(prev_msg)``
   for every historical reasoning-only row on every writeback;
2. ``_message_identity`` returned ``None`` for a *settled* reasoning-only row --
   the ``_partial`` branch directly above it already fixes this exact bug class,
   but only for partials -- so the merge could not key them;
3. the merge's assistant duplicate guard is adjacency-only
   (``_message_identity(merged[-1]) == key``), and the re-inserted copies land
   NON-adjacently, so even a correct identity leaks one copy per writeback.

Measured growth of reasoning-only rows over writeback cycles:
  - before any fix:        1, 2, 4, 8, 16, 32, 64, 128, 256   (2^N)
  - identity fix only:     1, 2, 3, 4, 5, ...                 (linear, +1/cycle)
  - identity + seen guard: 1, 1, 1, 1, 1, ...                 (constant)

2^N explains 1,975,499 rows from ~31 turns; linear would not.

These tests assert the *invariant* (bounded growth across repeated writebacks),
not merely that ``_message_identity`` returns something. They also pin what the
fix must not break: tool_call/tool-result pairing, distinct Thinking rows
staying distinct, and identical visible answers in separate turns both
remaining visible.
"""

import copy

from api.streaming import (
    _is_reasoning_only_assistant_message,
    _merge_display_messages_after_agent_result,
    _message_identity,
    _restore_display_reasoning_metadata,
)

USER = {"role": "user", "content": "go", "id": 10, "timestamp": 1791472680.0}
ANSWER = {"role": "assistant", "content": "ok", "id": 11, "timestamp": 1791472681.0}

# Field-for-field shape of the rows that filled the production sidecar:
# empty content, stable id, finish_reason=incomplete, reasoning text duplicated
# into reasoning_content, plus a multi-KB codex_reasoning_items payload.
# `_partial` is deliberately absent -- that is what made these rows unkeyable.
def reasoning_row(mid=117, text="**Responding to notifications**\n\nI need to produce a response."):
    return {
        "role": "assistant",
        "content": "",
        "id": mid,
        "timestamp": 1791472688.5774581,
        "finish_reason": "incomplete",
        "reasoning": text,
        "reasoning_content": text,
        "codex_reasoning_items": [
            {
                "type": "reasoning",
                "id": f"rs_{mid}",
                "encrypted_content": "gAAA" * 200,
                "summary": [{"type": "summary_text", "text": text[:40]}],
            }
        ],
    }


def writeback(display, result, msg_text="go"):
    """Exactly the composition used in production (api/streaming.py:2085-2092)."""
    return _merge_display_messages_after_agent_result(
        display,
        copy.deepcopy(result),
        _restore_display_reasoning_metadata(display, copy.deepcopy(result)),
        msg_text,
    )


def count_reasoning(messages):
    return sum(1 for m in messages if _is_reasoning_only_assistant_message(m))


def test_reasoning_rows_stay_constant_across_many_writebacks():
    """The core invariant. Fails hard before the fix (2^N), constant after.

    At 30 cycles the unfixed code would produce ~2^30 rows; the loop would not
    even finish, so the assertion is checked every cycle to fail fast.
    """
    result = [copy.deepcopy(USER), copy.deepcopy(ANSWER)]
    display = [copy.deepcopy(USER), copy.deepcopy(ANSWER), reasoning_row()]

    for cycle in range(1, 31):
        display = writeback(display, result)
        n = count_reasoning(display)
        assert n == 1, (
            f"after {cycle} writeback cycle(s) the single Thinking row became {n} copies "
            f"(transcript is {len(display)} rows) -- re-inserted copies are not deduped"
        )
    assert len(display) == 4, f"transcript grew to {len(display)} rows over 30 cycles"


def test_growth_is_not_exponential_over_ten_cycles():
    """Explicitly pins the 2^N signature that caused the OOM."""
    result = [copy.deepcopy(USER), copy.deepcopy(ANSWER)]
    display = [copy.deepcopy(USER), copy.deepcopy(ANSWER), reasoning_row()]
    counts = []
    for _ in range(10):
        display = writeback(display, result)
        counts.append(count_reasoning(display))
    assert counts == [1] * 10, f"reasoning-row counts per cycle were {counts}, expected all 1"


def test_distinct_thinking_rows_are_preserved():
    """Dedup must key on identity, not collapse every Thinking row into one."""
    result = [copy.deepcopy(USER), copy.deepcopy(ANSWER)]
    display = [
        copy.deepcopy(USER),
        copy.deepcopy(ANSWER),
        reasoning_row(117, "thought A"),
        reasoning_row(118, "thought B"),
        reasoning_row(119, "thought C"),
    ]
    for _ in range(10):
        display = writeback(display, result)
    kept = sorted(m["reasoning"] for m in display if _is_reasoning_only_assistant_message(m))
    assert kept == ["thought A", "thought B", "thought C"], f"distinct rows lost: {kept}"


def test_tool_call_and_result_pairing_survives_repeated_writebacks():
    """An assistant row with tool_calls has empty content but is NOT a Thinking row.

    369 such rows existed in the production session; treating them as junk would
    have destroyed real tool-calling turns and orphaned their tool results.
    """
    tool_call = {
        "role": "assistant",
        "content": "",
        "id": 20,
        "tool_calls": [{"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}],
        "timestamp": 1791472690.0,
    }
    tool_result = {
        "role": "tool",
        "content": "command output",
        "tool_call_id": "call_1",
        "id": 21,
        "timestamp": 1791472691.0,
    }
    assert _is_reasoning_only_assistant_message(tool_call) is False
    assert _message_identity(tool_call) is not None, "tool-calling row must keep an identity"

    result = [copy.deepcopy(USER), tool_call, tool_result, copy.deepcopy(ANSWER)]
    display = [copy.deepcopy(USER), tool_call, tool_result, copy.deepcopy(ANSWER), reasoning_row()]
    for _ in range(10):
        display = writeback(display, result)

    calls = [m for m in display if m.get("role") == "assistant" and m.get("tool_calls")]
    results = [m for m in display if m.get("role") == "tool"]
    assert len(calls) == 1, f"tool-calling row count drifted to {len(calls)}"
    assert len(results) == 1, f"tool result row count drifted to {len(results)}"
    assert calls[0]["tool_calls"][0]["id"] == results[0]["tool_call_id"], "call/result pairing broken"
    assert count_reasoning(display) == 1


def test_identical_visible_answers_in_separate_turns_both_remain():
    """Guards the deliberate behaviour the adjacency-only guard exists for.

    The seen-based dedup added for Thinking rows must NOT widen to rows that
    carry visible text, or repeated answers would silently vanish.
    """
    result = [
        {"role": "user", "content": "ping", "id": 1},
        {"role": "assistant", "content": "pong", "id": 2},
        {"role": "user", "content": "ping", "id": 3},
        {"role": "assistant", "content": "pong", "id": 4},
    ]
    # A genuinely fresh conversation: no prior display AND no prior context.
    # (Passing a non-empty previous_context with an empty previous_display is a
    # compaction shape, not a fresh turn, and legitimately yields no rows.)
    display = _merge_display_messages_after_agent_result(
        [], [], _restore_display_reasoning_metadata([], copy.deepcopy(result)), "ping"
    )
    answers = [m for m in display if m.get("role") == "assistant" and m.get("content") == "pong"]
    assert len(answers) == 2, f"identical answers in separate turns collapsed to {len(answers)}"


def test_settled_reasoning_row_has_stable_distinct_identity():
    """Unit-level backing for the invariant; localises a regression if removed."""
    a = reasoning_row(117, "same thought")
    b = reasoning_row(117, "same thought")
    c = reasoning_row(118, "same thought")   # different row, same text
    d = reasoning_row(117, "other thought")
    assert _message_identity(a) is not None
    assert _message_identity(a) == _message_identity(b)
    assert _message_identity(a) != _message_identity(c), "distinct ids must stay distinct"
    assert _message_identity(a) != _message_identity(d), "distinct text must stay distinct"


def test_reasoning_identity_never_collides_with_a_visible_answer():
    assert _message_identity(reasoning_row(1, "text")) != _message_identity(
        {"role": "assistant", "content": "text", "id": 1}
    )


def test_partial_rows_keep_their_original_identity_branch():
    """The pre-existing _partial fix must be untouched by the new branch."""
    partial = {"role": "assistant", "content": "", "_partial": True, "reasoning": "thinking"}
    ident = _message_identity(partial)
    assert ident is not None and ident[3].startswith("__partial__")
