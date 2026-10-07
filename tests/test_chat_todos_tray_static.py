"""Static frontend tests for the in-chat todos tray (feat/todos-in-chat).

These verify that the chat-embedded task tray wiring stays intact:
  - the tray DOM exists in index.html next to the message shell
  - the settings checkbox exists and is wired in loadSettingsPanel
  - the scheduler fans out to renderChatTodos on every todo_state refresh
  - the render path escapes user content and marks terminal states
  - the rail-hide helper targets [data-panel="todos"] with nav-tab-hidden
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def _read_static(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_chat_todos_tray_markup_exists_in_message_shell():
    idx = _read_static("static/index.html")
    assert 'id="chatTodosPanel"' in idx
    assert 'id="chatTodosHead"' in idx
    assert 'id="chatTodosSummary"' in idx
    assert 'id="chatTodosCounter"' in idx
    assert 'id="chatTodosBody"' in idx
    # The tray must live inside the messages shell, before the messages node,
    # so it stays pinned at the top of the chat area (not inside the scroller).
    shell = idx.find('class="messages-shell"')
    tray = idx.find('id="chatTodosPanel"')
    messages = idx.find('id="messages"')
    assert shell != -1 and tray != -1 and messages != -1
    assert shell < tray < messages


def test_chat_todos_settings_checkbox_wired_in_settings_panel():
    idx = _read_static("static/index.html")
    panels = _read_static("static/panels.js")

    assert 'id="settingsChatTodosInChat"' in idx
    assert 'settings_label_chat_todos_in_chat' in idx
    assert 'settings_desc_chat_todos_in_chat' in idx
    assert "const chatTodosCb=$('settingsChatTodosInChat')" in panels
    assert "_chatTodosToggleEnabled(this.checked)" in panels
    assert "typeof chatTodosEnabled==='function'" in panels


def test_i18n_keys_registered_in_english_locale():
    i18n = _read_static("static/i18n.js")
    assert "settings_label_chat_todos_in_chat: 'Show task list in chat'" in i18n
    assert "settings_desc_chat_todos_in_chat: 'Show a collapsible task list" in i18n


def test_scheduler_fans_out_to_chat_todos_renderer():
    ui = _read_static("static/ui.js")
    block_start = ui.find("function scheduleTodosRefresh()")
    block_end = ui.find("function _resetTodosRenderCache()", block_start)
    assert block_start != -1 and block_end != -1
    scheduler = ui[block_start:block_end]
    # Both the non-RAF fallback and the RAF path must call renderChatTodos.
    assert scheduler.count("renderChatTodos()") >= 2


def test_chat_todos_renderer_escapes_content_and_marks_terminal_states():
    ui = _read_static("static/ui.js")
    start = ui.find("function renderChatTodos()")
    end = ui.find("function toggleChatTodos()", start)
    assert start != -1 and end != -1
    render = ui[start:end]

    # Guard against running in non-DOM contexts (node VM tests).
    assert "typeof $!=='function'" in render
    # User content must be escaped; never interpolated raw.
    assert "esc(content)" in render
    # Terminal states get muted + strikethrough.
    assert "line-through" in render
    assert "status==='completed'||status==='cancelled'" in render
    # Summary/counter derive from statuses, not from message text.
    assert "status!=='completed'" in ui and "status!=='cancelled'" in ui


def test_rail_hide_helper_targets_todos_panel_and_bounces_to_chat():
    ui = _read_static("static/ui.js")
    start = ui.find("function _syncChatTodosRailVisibility()")
    end = ui.find("function _chatTodosToggleEnabled", start)
    assert start != -1 and end != -1
    helper = ui[start:end]

    assert 'querySelectorAll(\'[data-panel="todos"]\')' in helper
    assert "nav-tab-hidden" in helper
    assert "switchPanel('chat'" in helper


def test_chat_todos_pref_is_opt_in_by_default():
    # Maintainer review 2026-10-07T10:30:07Z: "Default the tray to OFF (opt-in)
    # ... On upgrade every existing user loses the sidebar Todos tab and gets a
    # floating overlay in the transcript, while the code comment says opt-in."
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosReadPref()")
    end = ui.find("function _chatTodosWritePref", start)
    assert start != -1 and end != -1
    pref = ui[start:end]

    assert "if(v===null) return false;" in pref  # default: opt-in / OFF
    assert "return v==='1';" in pref
    assert "return true" not in pref

    idx = _read_static("static/index.html")
    # The boot IIFE may only pre-hide the sidebar Todos tab when the user
    # explicitly opted in; a missing key must leave the tab alone.
    assert "if(ct==='1'&&p.indexOf('todos')===-1)p.push('todos')" in idx
    assert "ct!=='0'" not in idx


def test_chat_todos_pref_persists_explicit_disabled():
    # Review blocker: toggling OFF then reloading must stay OFF. The writer must
    # store an explicit '0' instead of removing the key (which would collide
    # with the null => enabled first-use default).
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosWritePref")
    end = ui.find("function chatTodosEnabled()", start)
    assert start != -1 and end != -1
    writer = ui[start:end]

    assert "localStorage.setItem(CHAT_TODOS_LS_KEY,v?'1':'0')" in writer
    assert "removeItem(CHAT_TODOS_LS_KEY)" not in writer


def test_chat_todos_aria_initial_collapsed():
    idx = _read_static("static/index.html")
    # Tray markup is hidden + collapsed by default; ARIA must match.
    assert 'id="chatTodosHead"' in idx
    head_start = idx.find('id="chatTodosHead"')
    # The head button's initial aria-expanded must be false (collapsed) and
    # must own the body region for a11y tree correctness.
    head_snippet = idx[head_start - 200 : head_start + 400]
    assert 'aria-expanded="false"' in head_snippet
    assert 'aria-controls="chatTodosBody"' in head_snippet
    # toggleChatTodos must flip aria-expanded to stay in sync.
    ui = _read_static("static/ui.js")
    toggle_start = ui.find("function toggleChatTodos()")
    assert toggle_start != -1
    toggle = ui[toggle_start : toggle_start + 400]
    assert "_syncChatTodosExpanded(isOpen)" in toggle
    # ...through the one shared helper, so the settings toggle path cannot
    # leave a stale aria-expanded behind (maintainer review 2026-10-07).
    helper_start = ui.find("function _syncChatTodosExpanded(")
    assert helper_start != -1
    helper = ui[helper_start : helper_start + 500]
    assert "setAttribute('aria-expanded',open?'true':'false')" in helper


def test_chat_todos_toggle_off_then_on_clears_aria_expanded():
    # [SILENT] finding: expanding the tray, turning the preference off, then on
    # again removed `open` but left aria-expanded="true" on the header.
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosToggleEnabled(")
    end = ui.find("function _chatTodosReadAlign", start)
    assert start != -1 and end != -1
    block = ui[start:end]

    assert "_syncChatTodosExpanded(false)" in block
    # The enable path must also (re)start collapsed.
    assert "tray.hidden=!checked" in block


def test_chat_todos_toggle_does_not_rebuild_the_transcript():
    # [SHOULD-FIX] "Turning the tray on re-renders the whole transcript ...
    # took 256 ms at 300 messages." The tray is an absolutely positioned
    # overlay, so toggling it must not call renderMessages().
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosToggleEnabled(")
    end = ui.find("function _chatTodosReadAlign", start)
    assert start != -1 and end != -1
    block = ui[start:end]

    assert "renderMessages(" not in block


def test_chat_todos_dead_force_hidden_and_progress_css_are_removed():
    ui = _read_static("static/ui.js")
    css = _read_static("static/style.css")

    assert "_chatTodosForceHidden" not in ui
    assert ".chat-todos-progress" not in css


def test_chat_todos_content_wraps_long_unbroken_tokens():
    css = _read_static("static/style.css")
    assert (
        ".chat-todos-row .todos-content{flex:1 1 auto;min-width:0;font-size:12.5px;"
        "line-height:1.4;overflow-wrap:anywhere;}" in css
    )
    assert "@media(prefers-reduced-motion:reduce){.chat-todos-head{transition:none;}" in css


def test_chat_todos_tray_strings_are_localized():
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosSummary(")
    end = ui.find("function toggleChatTodos()", start)
    assert start != -1 and end != -1
    block = ui[start:end]

    assert "t('todos_tray_summary_done',total)" in block
    assert "t('todos_tray_summary_active',active,total)" in block
    assert "t('todos_tray_open_count',active)" in block
    # No hardcoded English summaries survive.
    assert "'All done'" not in block
    assert "running`" not in block

    idx = _read_static("static/index.html")
    # The radiogroup label is translated too.
    assert 'aria-label="Task list alignment" data-i18n-aria-label="settings_label_chat_todos_align"' in idx


def test_chat_todos_chip_reflects_the_tray_forced_hide():
    # [SHOULD-FIX] "With the tray on, the chip reports ON while the tab is
    # hidden, and two clicks leave it ON with the tab still hidden."
    panels = _read_static("static/panels.js")
    assert "function _tabVisibilityChipForcedOff(panel){" in panels
    assert "return panel==='todos'&&typeof chatTodosEnabled==='function'&&chatTodosEnabled();" in panels
    assert "var isOff=hidden.indexOf(panel)!==-1||_tabVisibilityChipForcedOff(panel);" in panels
    chip_start = panels.find("function _toggleTabVisibilityChip(panel)")
    chip_end = panels.find("function _toggleDashboardVisibilityChip", chip_start)
    assert chip_start != -1 and chip_end != -1
    handler = panels[chip_start:chip_end]
    assert "if(_tabVisibilityChipForcedOff(panel)){" in handler
    assert "_chatTodosToggleEnabled(false)" in handler


def test_chat_todos_hidden_tab_collision():
    # Desktop absolute tray: the sidebar Todos nav entry must stay hidden
    # whenever the in-chat tray is enabled, regardless of the per-profile
    # hidden_tabs setting. Otherwise a profile switch can restore it.
    panels = _read_static("static/panels.js")
    idx = _read_static("static/index.html")
    # panels.js re-applies the preference inside the applied-visibility pass.
    assert "if(panel==='todos'&&chatTodosOn) shouldHide=true;" in panels
    assert "chatTodosOn=(typeof chatTodosEnabled==='function'?chatTodosEnabled():false)" in panels
    # The synchronous boot IIFE in index.html must also hide the Todos tab
    # before first paint when the preference is default/enabled.
    assert "hermes-webui-chat-todos" in idx
    assert "p.indexOf('todos')===-1)p.push('todos')" in idx


def test_chat_todos_i18n_keys_in_all_locales():
    src = _read_static("static/i18n.js")
    # Extract en block keys that are chat-todos specific
    expected = {
        "settings_label_chat_todos_in_chat",
        "settings_desc_chat_todos_in_chat",
        "settings_label_chat_todos_align",
        "settings_option_chat_todos_align_left",
        "settings_option_chat_todos_align_center",
        "settings_option_chat_todos_align_right",
        # Tray strings moved behind t() (maintainer review 2026-10-07).
        "todos_tray_summary_active",
        "todos_tray_summary_done",
        "todos_tray_open_count",
    }
    # LOCALES segmentation: each locale starts at "  <code>: {" and ends before
    # the next locale header. Using header boundaries avoids a fragile
    # balanced-brace scan that trips on `${...}` template literals inside i18n
    # (many _label helpers contain them). The same contract is verified by the
    # per-locale parity tests (test_chinese_locale.py etc.) which use a full
    # quote-aware extractor — here we assert presence of the 6 chat-todos keys.
    header_re = re.compile(r"^\s+'?([a-zA-Z-]+)'?\s*:\s*\{", re.MULTILINE)
    locale_headers = [
        (m.start(), m.group(1))
        for m in header_re.finditer(src)
        if "_lang" in src[m.end() : m.end() + 800]
    ]
    assert len(locale_headers) >= 14, f"expected >=14 locales, got {locale_headers}"
    for i, (start, locale_key) in enumerate(locale_headers):
        end = locale_headers[i + 1][0] if i + 1 < len(locale_headers) else len(src)
        block = src[start:end]
        missing = sorted(k for k in expected if k not in block)
        assert not missing, f"{locale_key} missing chat-todos keys: {missing}"


def test_chat_todos_desktop_does_not_push_message_stream():
    # Desktop tray is absolutely positioned above the messages so the transcript
    # never wastes the vertical band beside the tray. Mobile falls back to
    # static in-flow layout. This is a screenshot gate: the assertions bind
    # the visual contract the screenshot verifies.
    css = _read_static("static/style.css")
    # Desktop: absolute, out-of-flow; alignment variants via data-align.
    assert ".chat-todos{position:absolute;" in css
    assert ".chat-todos[data-align=\"center\"]{left:50%;" in css
    assert ".chat-todos[data-align=\"right\"]{left:auto;right:16px;" in css
    # Mobile: back to static full-width in-flow so phones read naturally.
    assert "@media(max-width:768px)" in css
    mobile_block_start = css.find("@media(max-width:768px)")
    mobile_block = css[mobile_block_start : mobile_block_start + 1200]
    assert ".chat-todos{position:static;" in mobile_block


def test_rail_hide_helper_does_not_clobber_a_user_hidden_tab():
    """Greptile P1 (2026-10-07T07:24:18Z): disabling the in-chat tray must not
    force-show a Todos entry the user hid independently via hidden_tabs.

    The helper used to `classList.toggle('nav-tab-hidden', !!enabled)`, which
    REMOVED the class whenever the tray was off — resurrecting a tab the user
    had deliberately hidden. Tray-off must defer to the canonical visibility
    owner instead of asserting its own show/hide.
    """
    ui = _read_static("static/ui.js")
    start = ui.find("function _syncChatTodosRailVisibility()")
    end = ui.find("function _chatTodosToggleEnabled", start)
    assert start != -1 and end != -1
    helper = ui[start:end]

    # Never unconditionally reveal the tab...
    assert "classList.toggle('nav-tab-hidden',!!enabled)" not in helper
    assert "classList.remove('nav-tab-hidden')" not in helper
    # ...tray-off hands visibility back to the canonical owner (hidden_tabs)...
    assert "_applyTabVisibility(_getHiddenTabs())" in helper
    # ...and tray-on still suppresses the duplicate sidebar surface + bounces.
    assert "classList.add('nav-tab-hidden')" in helper
    assert "switchPanel('chat'" in helper


# ── Behavior probes: the real frontend functions, run under node ──────────
# The maintainer reproduced the imported-list regression "with the real
# frontend functions", so these extract the shipped source of the functions
# under test and run them instead of asserting on their text.


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    start = source.find(start_marker)
    assert start != -1, f"missing {start_marker!r}"
    end = source.find(end_marker, start)
    assert end != -1, f"missing {end_marker!r} after {start_marker!r}"
    return source[start:end]


def _run_node(tmp_path: Path, name: str, script: str) -> str:
    if shutil.which("node") is None:
        pytest.skip("node is required for the frontend behavior probe")
    script_path = tmp_path / name
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


_CURRENT_TODOS_PROBE = """
const S = {todos: [], todoStateMeta: null, messages: [], session: null};
__LEGACY__
__HELPER__
function assert(cond, msg) { if (!cond) throw new Error(msg); }

// Case A — the regression: hydration installs an empty S.todos for an imported
// session whose tasks only exist as role:"tool" messages, with no todoStateMeta.
S.session = {messages: [{role: 'tool', content: JSON.stringify({todos: [{id: 'a', content: 'imported', status: 'pending'}]})}]};
S.messages = S.session.messages;
S.todos = [];
S.todoStateMeta = null;
let got = _currentTodos();
assert(got.length === 1 && got[0].id === 'a', 'imported tool-message list must fall back to the legacy renderer');

// Case B — an explicit empty snapshot still wins (no spurious legacy revival).
S.todoStateMeta = {ts: 1, source: 'cold-load', version: 1};
assert(_currentTodos().length === 0, 'explicit empty snapshot must win');

// Case C — an explicit non-empty snapshot is returned as-is.
S.todos = [{id: 'x', content: 'X', status: 'pending'}];
got = _currentTodos();
assert(got.length === 1 && got[0].id === 'x', 'explicit snapshot must be returned');

// Case D — no snapshot AND no legacy messages => empty.
S.todoStateMeta = null;
S.todos = [];
S.session = {messages: []};
S.messages = [];
assert(_currentTodos().length === 0, 'no signal and no legacy list => empty');
console.log('ok');
"""


def test_current_todos_falls_back_to_legacy_for_imported_tool_lists(tmp_path):
    ui = _read_static("static/ui.js")
    panels = _read_static("static/panels.js")
    helper = _extract(ui, "function _currentTodos(){", "function _chatTodosSummary(")
    legacy = panels[panels.find("function _legacyTodosFromMessages() {"):]
    legacy = legacy[: legacy.find("\n}") + 2]
    assert legacy.rstrip().endswith("}")
    script = _CURRENT_TODOS_PROBE.replace("__LEGACY__", legacy).replace("__HELPER__", helper)
    assert _run_node(tmp_path, "current_todos_probe.js", script).strip() == "ok"


_SUMMARY_PROBE = """
const table = {
  todos_no_active: 'No active task list in this session.',
  todos_tray_summary_active: '{0} active · {1} total',
  todos_tray_summary_done: 'All done · {0} total',
};
function t(key, ...args) {
  let v = table[key];
  if (v === undefined) return key;
  if (args.length) v = String(v).replace(/\\{(\\d+)\\}/g, (m, i) => (args[i] !== undefined ? String(args[i]) : m));
  return v;
}
__HELPER__
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const mixed = _chatTodosSummary([{status: 'pending'}, {status: 'in_progress'}, {status: 'completed'}]);
assert(mixed.text === '2 active · 3 total', 'localized active/total summary');
assert(mixed.active === 2 && mixed.total === 3, 'counts derive from statuses');
const done = _chatTodosSummary([{status: 'completed'}, {status: 'cancelled'}]);
assert(done.text === 'All done · 2 total', 'localized all-done summary');
assert(done.active === 0 && done.total === 2, 'terminal-only counts');
assert(_chatTodosSummary([]).text === 'No active task list in this session.', 'empty summary uses i18n');
console.log('ok');
"""


def test_chat_todos_summary_uses_localized_placeholders(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(ui, "function _chatTodosSummary(", "function renderChatTodos(){")
    script = _SUMMARY_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "summary_probe.js", script).strip() == "ok"


_EXPANDED_PROBE = """
const head = {attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }};
const tray = {
  classes: new Set(),
  classList: {
    add(c) { tray.classes.add(c); },
    remove(c) { tray.classes.delete(c); },
    contains(c) { return tray.classes.has(c); },
  },
};
function $(id) { return id === 'chatTodosPanel' ? tray : (id === 'chatTodosHead' ? head : null); }
__HELPER__
function assert(cond, msg) { if (!cond) throw new Error(msg); }
_syncChatTodosExpanded(true);
assert(tray.classes.has('open'), 'expand adds .open');
assert(head.attrs['aria-expanded'] === 'true', 'expand sets aria-expanded=true');
_syncChatTodosExpanded(false);
assert(!tray.classes.has('open'), 'collapse removes .open');
assert(head.attrs['aria-expanded'] === 'false', 'collapse sets aria-expanded=false');
console.log('ok');
"""


def test_sync_chat_todos_expanded_keeps_aria_in_sync(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(ui, "function _syncChatTodosExpanded(", "function _chatTodosToggleEnabled(")
    script = _EXPANDED_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "expanded_probe.js", script).strip() == "ok"


def test_chat_todos_locales_keep_diacritics():
    # [SHOULD-FIX] "Eight locales ship diacritic-stripped text (it, es, pt, fr,
    # cs, tr, pl, vi). Vietnamese is unreadable as shipped."
    src = _read_static("static/i18n.js")
    required = {
        "attività": "it",
        "attivo": "it",
        "área": "es",
        "duplicación": "es",
        "Alineación": "es",
        "recolhível": "pt",
        "duplicação": "pt",
        "tâches": "fr",
        "Activée": "fr",
        "latérale": "fr",
        "úkolů": "cs",
        "sbalitelný": "cs",
        "Zarovnání": "cs",
        "duplicitě": "cs",
        "üst kısmında": "tr",
        "görev": "tr",
        "önlemek": "tr",
        "Pokaż": "pl",
        "Wyświetla": "pl",
        "Wyrównanie": "pl",
        "Środek": "pl",
        "bảng Todos": "vi",
        "trùng lặp": "vi",
        "Căn chỉnh": "vi",
        "Giữa": "vi",
    }
    for needle, locale in required.items():
        assert needle in src, f"{locale} lost its diacritics: {needle!r}"
    # Robustness floor: the share of non-ASCII characters in the vi block must
    # not collapse back to pure ASCII.
    start = src.find("\n  vi: {")
    assert start != -1
    vi_block = src[start : src.find("\n  },", start)]
    non_ascii = sum(1 for ch in vi_block if ord(ch) > 127)
    assert non_ascii > 200, f"vi locale looks diacritic-stripped ({non_ascii} non-ASCII chars)"
