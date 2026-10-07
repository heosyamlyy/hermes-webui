"""Real grouped sidebar geometry: active anchoring and search-preview invalidation.

No server or agent state. Uses production controls, grouping renderer, rows,
measured virtualization, scroll listener and CSS; only sorted groups are seeded.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.browser_lineage_scroll_selection import script  # noqa: E402
from tests._sidebar_child_status_helpers import ROOT  # noqa: E402


def geometry_script(source):
    component = script(source)
    preview = re.search(r'^function _sessionSearchContentPreview\(.*?^\}', source, re.M | re.S).group()
    component = component.replace("function _sessionSearchContentPreview(){return '';}", preview)
    # Include the actual profile/archive controls preceding the date groups.
    start = source.index('  // Profile filter toggle (show sessions from other profiles).')
    end = source.index('  // Empty state for active project filter', start)
    component = component.replace(' list.replaceChildren();', ' list.replaceChildren();\n' + source[start:end])
    return component + """
const _otherProfileCount=3,archivedCount=12;
function groupScene(){
 scene('detailed','plain');
 const rows=groups[0].items;
 groups=['★ Pinned','Today','Yesterday','This week','Last week','Older'].map((label,i)=>({
   label,isPinned:i===0,items:rows.slice(i*20,(i+1)*20)}));
 repaint();
}
function geometry(id){
 const l=$('sessionList'),e=l.querySelector('.session-date-body>.session-item[data-sid="'+id+'"]');
 const r=e?.getBoundingClientRect(),lr=l.getBoundingClientRect();
 return {id,top:r?r.top-lr.top:null,bottom:r?r.bottom-lr.top:null,height:l.clientHeight,
   scrollTop:l.scrollTop,rows:l.querySelectorAll('.session-date-body>.session-item').length,
   headers:l.querySelectorAll('.session-date-header').length};
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--before-ref')
    parser.add_argument('--case', choices=['anchor', 'previews', 'all'], default='all')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def source(path):
        if args.before_ref:
            return subprocess.check_output(['git', 'show', f'{args.before_ref}:{path}'], cwd=ROOT, text=True)
        return (ROOT / path).read_text()

    results, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, touch in [(1280, False), (390, True)]:
            context = browser.new_context(viewport={'width': width, 'height': 800}, has_touch=touch)
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content('<input id="sessionSearch" hidden><div id="sessionList" style="width:300px;height:180px;overflow:auto;background:var(--sidebar)"></div>')
            page.add_style_tag(content=source('static/style.css'))
            page.add_script_tag(content=geometry_script(source('static/sessions.js')))
            page.add_script_tag(content=source('static/i18n.js'))
            page.evaluate('document.documentElement.dataset.skin="graphite"')
            if touch:
                page.evaluate('$("sessionList").style.width="180px"')
            if args.case in ['anchor', 'all']:
                for entry in ['reload', 'search-return']:
                    page.evaluate('activeSidForSidebar="other";groupScene()')
                    if entry == 'search-return':
                        page.evaluate('window.savedGroups=groups;searchQueryRaw="Conversation 100";$("sessionSearch").value=searchQueryRaw;'
                                      'groups=[{label:"Older",items:groups.at(-1).items.filter(s=>s.session_id==="p100")}];repaint()')
                        target = page.locator('.session-item[data-sid="p100"] .session-title')
                        target.tap() if touch else target.click()
                        page.evaluate('activeSidForSidebar="p100";repaint();searchQueryRaw="";$("sessionSearch").value="";groups=savedGroups;repaint()')
                    else:
                        page.evaluate('activeSidForSidebar="p100";delete $("sessionList").dataset.sessionVirtualActiveAnchor;repaint()')
                    page.wait_for_timeout(100)
                    state = page.evaluate('geometry("p100")')
                    page.screenshot(path=str(args.output / f'{width}-{entry}.png'))
                    failures = []
                    if state['top'] is None or state['top'] < 0 or state['bottom'] > state['height']:
                        failures.append('active row not fully within short grouped viewport')
                    if state['rows'] >= 80:
                        failures.append('unbounded DOM')
                    results.append(dict(scene=entry, width=width, state=state, failures=failures))
            if args.case in ['previews', 'all']:
                for transition in ['hide', 'show']:
                    page.evaluate('activeSidForSidebar="other";scene("detailed","plain");'
                                  'searchQueryRaw="needle";$("sessionSearch").value=searchQueryRaw;'
                                  'for(const s of groups[0].items){s.match_type="content";s.match_preview="A needle in the content";}')
                    page.evaluate('hide=>{_hideSearchPreviewsAfterSelect=hide;repaint();}', transition == 'show')
                    initial = page.locator('.session-search-preview').count()
                    assert (initial > 0) == (transition == 'hide')
                    # Measure p0 while rendered, then make it offscreen.
                    page.evaluate('$("sessionList").scrollTop=3500')
                    page.wait_for_timeout(100)
                    assert page.locator('.session-date-body>.session-item[data-sid="p0"]').count() == 0
                    before = page.evaluate('visible()')
                    page.evaluate('_hideSearchPreviewsAfterSelect=!_hideSearchPreviewsAfterSelect;repaint()')
                    page.wait_for_timeout(100)
                    after = page.evaluate('visible()')
                    state = page.evaluate('''() => {
                        const l=$('sessionList'),row=l.querySelector('.session-date-body>.session-item');
                        const height=row.getBoundingClientRect().height+(parseFloat(getComputedStyle(row).marginBottom)||0);
                        return {prefix:l._sessionVirtualLayout.offsets[10],expectedPrefix:10*height,
                          previewCount:l.querySelectorAll('.session-search-preview').length,
                          cached:l._sessionVirtualLayout.measured.get('p0'),rows:l.querySelectorAll('.session-date-body>.session-item').length};
                    }''')
                    page.screenshot(path=str(args.output / f'{width}-previews-{transition}.png'))
                    failures = []
                    if abs(state['prefix'] - state['expectedPrefix']) > 1:
                        failures.append('offscreen preview measurement corrupts spacer heights')
                    if state['rows'] >= 80:
                        failures.append('unbounded DOM')
                    if after['rows'][0]['id'] != before['rows'][0]['id'] or abs(after['rows'][0]['y'] - before['rows'][0]['y']) > 1:
                        failures.append('preview transition moves top conversation/offset')
                    assert (state['previewCount'] > 0) == (transition == 'show')
                    results.append(dict(scene='previews-' + transition, width=width, before=before, after=after, state=state, failures=failures))
            context.close()
        browser.close()
    (args.output / 'report.json').write_text(json.dumps(dict(results=results, errors=errors), indent=2))
    print(json.dumps(dict(cases=len(results), failures=sum(bool(r['failures']) for r in results), errors=errors)))
    if errors or any(r['failures'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
