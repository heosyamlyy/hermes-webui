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
    assert 'id="chatTodosBody"' in idx
    # ONE progress label (reviewer re-gate 2026-10-07T18:08:02Z): the separate
    # counter span duplicated the same active count, so it must be gone.
    assert 'id="chatTodosCounter"' not in idx
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
    end = ui.find("function _chatTodosSummary(", start)
    assert start != -1 and end != -1
    block = ui[start:end]

    assert "_syncChatTodosExpanded(false)" in block
    # The enable path must also (re)start collapsed.
    assert "tray.hidden=!checked" in block


def test_chat_todos_toggle_does_not_rebuild_the_transcript():
    # [SHOULD-FIX] "Turning the tray on re-renders the whole transcript ...
    # took 256 ms at 300 messages." The tray is an independent strip, so
    # toggling it must not call renderMessages().
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosToggleEnabled(")
    end = ui.find("function _chatTodosSummary(", start)
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
    # No hardcoded English summaries survive.
    assert "'All done'" not in block
    assert "running`" not in block

    idx = _read_static("static/index.html")
    # ONE progress label: the summary span is generated text, so applyLocaleToDOM
    # must not own it — no data-i18n on it (reviewer re-gate 2026-10-07T18:08:02Z)
    # — and the duplicated counter span is gone.
    assert 'id="chatTodosSummary" data-i18n' not in idx
    assert 'id="chatTodosCounter"' not in idx
    assert "todos_tray_open_count" not in ui
    assert "todos_tray_open_count" not in _read_static("static/i18n.js")


def test_chat_todos_closed_chevron_points_down():
    # Every other collapsed disclosure in the app points down when closed
    # (reviewer re-gate 2026-10-07T18:08:02Z).
    idx = _read_static("static/index.html")
    head = idx[idx.find('id="chatTodosChevron"') :]
    snippet = head[: head.find("</span>")]
    assert '<polyline points="6 9 12 15 18 9"/>' in snippet  # down when closed
    assert '<polyline points="18 15 12 9 6 15"/>' not in snippet
    css = _read_static("static/style.css")
    # ...and it flips to up when the tray is open.
    assert ".chat-todos.open .chat-todos-chevron{transform:rotate(180deg);}" in css


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
        # Tray strings moved behind t() (maintainer review 2026-10-07).
        "todos_tray_summary_active",
        "todos_tray_summary_done",
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


def test_chat_todos_desktop_tray_is_an_in_flow_strip():
    # Reviewer re-gate 2026-10-07T18:08:02Z: "Put the desktop tray in the flow,
    # like the phone layout already is." A collapsed ~35px strip owns the top
    # band of .messages-shell and pushes the scroller down; nothing floats over
    # the transcript and the alignment variants are gone.
    css = _read_static("static/style.css")
    assert ".chat-todos{flex:0 0 auto;width:100%;" in css
    assert ".chat-todos{position:absolute" not in css
    assert "data-align" not in css
    assert (
        ".chat-todos-head{display:flex;align-items:center;gap:8px;width:100%;min-height:35px;" in css
    )
    # Expanded, the body grows in place inside the same flex item.
    assert (
        ".chat-todos-body{max-height:240px;overflow-y:auto;border-top:1px solid var(--border);" in css
    )
    # The floating Start jump pill is anchored to the shell's top-right, so it
    # must drop below the strip instead of painting over it — by the strip's
    # LIVE height, because a fixed offset only cleared the collapsed band and
    # let the pill cover the expanded task rows (re-gate 2026-10-07T20:13:25Z).
    assert (
        ".messages-shell.chat-todos-visible #jumpToSessionStartBtn{top:calc(var(--chat-todos-h,36px) + 7px);}"
        in css
    )
    # The shell marker class is driven from the render path, not by hand, and it
    # also publishes the strip's measured height for that offset.
    ui = _read_static("static/ui.js")
    assert "function _syncChatTodosShellClass(visible){" in ui
    assert "shell.classList.toggle('chat-todos-visible',!!visible)" in ui
    assert "shell.style.setProperty('--chat-todos-h',Math.round(h)+'px')" in ui
    # The in-flow strip resizes the transcript, so every layout-changing path
    # also re-pins the reader (re-gate 2026-10-07T20:13:25Z, [SILENT]).
    assert "function _repinChatTodosTranscript(){" in ui
    assert "_repinChatTodosTranscript();" in ui
    assert ui.count("_repinChatTodosTranscript();") >= 5
    assert "if(typeof _repinMessagesAfterComposerResize==='function') _repinMessagesAfterComposerResize();" in ui


def test_chat_todos_desktop_has_no_alignment_setting():
    # Reviewer re-gate 2026-10-07T18:08:02Z: "Drop the alignment setting."
    idx = _read_static("static/index.html")
    ui = _read_static("static/ui.js")
    panels = _read_static("static/panels.js")
    for src in (idx, ui, panels):
        assert "chatTodosAlign" not in src
        assert "_pickChatTodosAlign" not in src
        assert "_syncChatTodosAlignRadios" not in src
    assert "chat-todos-align-group" not in idx
    assert "hermes-webui-chat-todos-align" not in ui


def test_workspace_todos_tab_follows_the_in_chat_tray():
    # Reviewer re-gate 2026-10-07T18:08:02Z, item 6: while the in-chat tray is
    # on, the workspace "Show Todos tab" surface must follow it so the two
    # settings cannot contradict each other.
    panels = _read_static("static/panels.js")
    start = panels.find("function _applyWorkspaceTodosTabVisibility(){")
    assert start != -1
    end = panels.find("\nfunction ", start + 10)
    assert end != -1
    block = panels[start:end]
    assert "const trayOn=(typeof chatTodosEnabled==='function')&&chatTodosEnabled();" in block
    assert "const want=!!window._workspaceTodosTab&&!trayOn;" in block
    assert "if(tab) tab.hidden=!want;" in block
    assert "settingsWorkspaceTodosTabField" in block
    # ui.js re-applies it whenever the tray preference changes.
    ui = _read_static("static/ui.js")
    assert "_applyWorkspaceTodosTabVisibility()" in ui
    idx = _read_static("static/index.html")
    assert 'id="settingsWorkspaceTodosTabField"' in idx


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
        "recolhível": "pt",
        "duplicação": "pt",
        "tâches": "fr",
        "Activée": "fr",
        "latérale": "fr",
        "úkolů": "cs",
        "sbalitelný": "cs",
        "duplicitě": "cs",
        "üst kısmında": "tr",
        "görev": "tr",
        "önlemek": "tr",
        "Pokaż": "pl",
        "Wyświetla": "pl",
        "bảng Todos": "vi",
        "trùng lặp": "vi",
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


# ── Re-gate 2026-10-07T14:33:07Z (head cfd6f65c) — the three remaining items ──
# Each pins one finding, and where the maintainer reproduced it "in Chromium"
# the probe runs the shipped function under node instead of asserting on text.


def test_chat_todos_settings_toggle_repaints_the_visibility_chips():
    # [SILENT] static/ui.js:10503 — "Enabling the tray through Settings hides
    # Todos while its visibility chip still reports ON." The tray owns the hide,
    # so the chip must be re-rendered whenever the tray preference changes.
    ui = _read_static("static/ui.js")
    start = ui.find("function _chatTodosToggleEnabled(checked){")
    end = ui.find("function _chatTodosSummary(", start)
    assert start != -1 and end != -1, "missing _chatTodosToggleEnabled block"
    handler = ui[start:end]
    enable_at = handler.find("_setChatTodosEnabled(checked);")
    repaint_at = handler.find("_renderTabVisibilityChips()")
    assert enable_at != -1 and repaint_at != -1, "chips are never repainted on toggle"
    assert enable_at < repaint_at, "repaint must follow the preference write"
    assert "typeof _renderTabVisibilityChips==='function'" in handler


def test_chat_todos_chip_enable_clears_an_independent_hidden_tabs_bit():
    # [SILENT] static/panels.js:7878 — "With Todos independently hidden and the
    # tray enabled, clicking the OFF visibility chip disables the tray but
    # leaves Todos hidden." The explicit chip-enable branch must drop the
    # independent hidden_tabs entry in the same click.
    panels = _read_static("static/panels.js")
    start = panels.find("function _toggleTabVisibilityChip(panel){")
    end = panels.find("function _toggleDashboardVisibilityChip", start)
    assert start != -1 and end != -1, "missing _toggleTabVisibilityChip block"
    handler = panels[start:end]
    forced_at = handler.find("if(_tabVisibilityChipForcedOff(panel)){")
    tray_off_at = handler.find("_chatTodosToggleEnabled(false)", forced_at)
    assert forced_at != -1 and tray_off_at != -1
    branch = handler[forced_at:tray_off_at]
    assert "_setHiddenTabs(" in branch, "forced-off branch never clears hidden_tabs"
    assert "_getHiddenTabs()" in branch


def test_apply_locale_repaints_the_chat_todos_summary():
    # [SILENT] static/index.html:446 — "Opening Settings erases the task summary:
    # applyLocaleToDOM() overwrites the dynamic summary through
    # data-i18n=\"tab_todos\"." The live value must be repainted after restamping.
    src = _read_static("static/i18n.js")
    start = src.find("function applyLocaleToDOM() {")
    end = src.find("// Apply saved locale immediately", start)
    assert start != -1 and end != -1, "missing applyLocaleToDOM"
    body = src[start:end]
    aria_at = body.find("[data-i18n-aria-label]")
    repaint_at = body.find("typeof renderChatTodos === 'function'")
    sync_at = body.find("syncWorkspacePanelUI()")
    assert repaint_at != -1, "applyLocaleToDOM never repaints the todos summary"
    assert aria_at < repaint_at < sync_at, "repaint must follow the locale restamp"


_TOGGLE_CHIPS_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
let chipsRendered = 0;
let enabledSet = null;
let expandedCalls = [];
let renderCalls = 0;
function _setChatTodosEnabled(v) { enabledSet = !!v; }
function _syncChatTodosExpanded(v) { expandedCalls.push(!!v); }
function renderChatTodos() { renderCalls++; }
function _scheduleAppearanceAutosave() {}
function $() { return null; }
var _renderTabVisibilityChips = function () { chipsRendered++; };
__HELPER__
_chatTodosToggleEnabled(true);
assert(enabledSet === true, 'tray preference must be written');
assert(chipsRendered === 1, 'enabling the tray must repaint the visibility chips');
assert(expandedCalls.length === 1 && expandedCalls[0] === false, 're-enable restarts collapsed');
assert(renderCalls === 1, 'tray contents are repainted');
_chatTodosToggleEnabled(false);
assert(enabledSet === false, 'disabling writes through');
assert(chipsRendered === 2, 'disabling must repaint the chips too');
console.log('ok');
"""


def test_chat_todos_toggle_repaints_chips_probe(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "function _chatTodosToggleEnabled(checked){", "function _chatTodosSummary("
    )
    script = _TOGGLE_CHIPS_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "toggle_chips_probe.js", script).strip() == "ok"


_CHIP_FORCED_OFF_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const _ALWAYS_VISIBLE_TABS = new Set(['chat', 'settings']);
let hidden = ['todos', 'notes'];
let applied = null;
let trayToggles = [];
let chips = 0;
let chatTodosOn = true;
function _getHiddenTabs() { return hidden.slice(); }
function _setHiddenTabs(v) { hidden = v.slice(); }
function _applyTabVisibility(h) { applied = h.slice(); }
function _renderTabVisibilityChips() { chips++; }
function _scheduleAppearanceAutosave() {}
function chatTodosEnabled() { return chatTodosOn; }
function _chatTodosToggleEnabled(v) { chatTodosOn = !!v; trayToggles.push(!!v); }
__HELPER__
_toggleTabVisibilityChip('todos');
assert(hidden.indexOf('todos') === -1, 'chip-enable must clear the independent hidden_tabs bit');
assert(hidden.indexOf('notes') !== -1, 'other hidden tabs must be untouched');
assert(trayToggles.length === 1 && trayToggles[0] === false, 'the tray is what gets disabled');
assert(chatTodosOn === false, 'tray preference is off');
assert(chips === 1, 'the chip row is re-rendered');
console.log('ok');
"""


def test_chat_todos_chip_enable_clears_hidden_tabs_probe(tmp_path):
    panels = _read_static("static/panels.js")
    helper = _extract(
        panels, "function _toggleTabVisibilityChip(panel){", "function _toggleDashboardVisibilityChip"
    )
    forced_off = _extract(
        panels, "function _tabVisibilityChipForcedOff(panel){", "function _renderTabVisibilityChips(){"
    )
    script = _CHIP_FORCED_OFF_PROBE.replace("__HELPER__", helper + "\n" + forced_off)
    assert _run_node(tmp_path, "chip_forced_off_probe.js", script).strip() == "ok"


_LOCALE_SUMMARY_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
let repainted = 0;
let workspaceSynced = 0;
function renderChatTodos() { repainted++; }
function syncWorkspacePanelUI() { workspaceSynced++; }
function syncAppTitlebar() {}
function t(k) { return k; }
const document = { querySelectorAll() { return []; } };
__HELPER__
applyLocaleToDOM();
assert(repainted === 1, 'applyLocaleToDOM must repaint the chat-todos summary');
assert(workspaceSynced === 1, 'the other post-restamp syncs still run');
console.log('ok');
"""


def test_apply_locale_repaint_probe(tmp_path):
    src = _read_static("static/i18n.js")
    helper = _extract(
        src, "function applyLocaleToDOM() {", "// Apply saved locale immediately"
    )
    script = _LOCALE_SUMMARY_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "locale_summary_probe.js", script).strip() == "ok"
