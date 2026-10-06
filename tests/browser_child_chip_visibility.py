"""Real sidebar renderer/CSS clipping and accessible-name regression gate."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests._sidebar_child_status_helpers import ROOT, component_script  # noqa: E402

SCENE = r"""
const _loadingSessionId=null;
function scene(own,reference,search){
  searchQueryRaw=search?'task':'';
  const parent={session_id:'parent',title:'Parent task with a long conversation title',message_count:3,
    has_unread:own.includes('unread'),attention:own.includes('approval')?{kind:'approval',count:1}:own.includes('clarify')?{kind:'clarify',count:1}:null};
  const child=(id,state)=>({session_id:id,title:id+' task',message_count:3,parent_session_id:'parent',
    relationship_type:'child_session',raw_source:'subagent',session_source:'other',
    is_streaming:state==='running',attention:state==='approval'?{kind:'approval',count:1}:null,
    archived:reference,_lineage_root_id:reference?id:undefined});
  const children=[child('waiting','approval'),child('working','running')];
  const result=renderFixture(reference?[parent]:[parent,...children],[parent,...children],false,'other');
  document.querySelector('#fixture').replaceChildren(result.element);
  document.querySelectorAll('*').forEach(el=>{el.scrollLeft=0;});
  const chip=document.querySelector('.session-child-count'),mark=chip.querySelector('.session-child-count-state');
  const r=mark.getBoundingClientRect(),clip=document.querySelector('.session-text').getBoundingClientRect();
  const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
  const activity=document.querySelector('.session-child-activity-indicator');
  const ar=activity?.getBoundingClientRect();
  return {visible:hit===mark&&r.left>=clip.left&&r.right<=clip.right,
    titleWidth:document.querySelector('.session-title').getBoundingClientRect().width,
    chipWidth:chip.getBoundingClientRect().width,clipWidth:clip.width,
    aria:chip.getAttribute('aria-label'),tip:chip.title,
    expanded:chip.getAttribute('aria-expanded'),rows:document.querySelectorAll('.session-child-session').length,
    activity:!!activity,activityVisible:!ar||ar.left>=clip.left&&ar.right<=clip.right,
    runningLabel:t('session_child_running'),unreadLabel:t('session_child_unread')};
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before-ref')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def source(path):
        if args.before_ref:
            return subprocess.check_output(['git', 'show', f'{args.before_ref}:{path}'], cwd=ROOT, text=True)
        return (ROOT / path).read_text()

    results = []
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for viewport, touch in [(1280, False), (768, False), (390, True)]:
            context = browser.new_context(viewport={'width': viewport, 'height': 800}, has_touch=touch)
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content('<main id="fixture" style="padding:8px;box-sizing:border-box;background:var(--sidebar)"></main>')
            page.add_style_tag(content=source('static/style.css'))
            page.add_script_tag(content=component_script(source('static/sessions.js')).replace("const animateRefresh=false, searchQueryRaw='';", "const animateRefresh=false;let searchQueryRaw='';"))
            page.add_script_tag(content=source('static/i18n.js'))
            for name in ['_sessionSearchRanges', '_appendHighlightedText']:
                match = re.search(r'^function ' + name + r'\(.*?^\}', source('static/sessions.js'), re.M | re.S)
                assert match, name
                page.add_script_tag(content=match.group())
            page.add_script_tag(content=SCENE)
            locales = page.evaluate('Object.keys(LOCALES)')
            for width in [180, 300]:
                page.locator('#fixture').evaluate('(el,w)=>el.style.width=w+"px"', width)
                for locale in locales:
                    page.evaluate('locale=>setLocale(locale)', locale)
                    for own in ['idle', 'unread', 'approval', 'clarify', 'unread-approval', 'unread-clarify']:
                        for reference in [False, True]:
                            for search in [False, True]:
                                data = page.evaluate('args=>scene(...args)', [own, reference, search])
                                failures = []
                                if not data['visible'] or not data['activityVisible']:
                                    failures.append('status clipped before actionability scrolling')
                                if data['titleWidth'] < 20:
                                    failures.append('title minimum lost')
                                if data['aria'] != data['tip'] or data['runningLabel'] not in (data['aria'] or ''):
                                    failures.append('concurrent running missing from accessible name')
                                if data['runningLabel'] == 'session_child_running' or data['unreadLabel'] == 'session_child_unread':
                                    failures.append('child locale keys missing')
                                if not reference and data['expanded'] != ('true' if search else 'false'):
                                    failures.append('search expansion misreported')
                                if reference and width == 300 and data['titleWidth'] < 80:
                                    failures.append('archived chip crowds title')
                                results.append(dict(viewport=viewport, width=width, locale=locale, own=own, reference=reference, search=search, data=data, failures=failures))
                                if locale == 'en' and own == 'approval' and not search:
                                    page.screenshot(path=str(args.output / f'{viewport}-{width}-{"archived" if reference else "interactive"}.png'))
            context.close()
        browser.close()
    report = dict(cases=len(results), failures=sum(bool(r['failures']) for r in results), errors=errors, results=results)
    (args.output / 'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ['cases', 'failures', 'errors']}))
    if report['failures'] or errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
