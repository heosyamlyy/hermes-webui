"""Real renderer/CSS: child activity ownership and primary title glyph fit."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests._sidebar_child_status_helpers import ROOT, component_script  # noqa: E402

SCENE = r"""
const _loadingSessionId=null;
let currentOwn='unread', childMode='concurrent';
function repaint(){scene(currentOwn,_expandedChildSessionKeys.has('parent'),activeSidForSidebar);}
function scene(own,expanded=false,selected='other',density='compact'){
  currentOwn=own;
  window._sidebarDensity=density;
  const parent={session_id:'parent',title:'Sidebar authentication investigation',message_count:3,
    has_unread:own==='unread',attention:own==='approval'?{kind:'approval',count:1}:null};
  const children=['fork','delegated'].map(kind=>({session_id:kind,title:kind+' authentication task',
    parent_session_id:'parent',relationship_type:'child_session',raw_source:'subagent',session_source:kind,
    message_count:3,is_streaming:childMode==='running'||(childMode==='concurrent'&&kind==='delegated'),
    has_unread:childMode==='unread'||childMode==='concurrent',
    attention:kind==='fork'&&['approval','concurrent'].includes(childMode)?{kind:'approval',count:1}:null}));
  const result=renderFixture([parent,...children],[parent,...children],expanded,selected);
  document.querySelector('#fixture').replaceChildren(result.element);
  document.querySelectorAll('*').forEach(e=>e.scrollLeft=0);
}
function measure(){
  const chip=document.querySelector('.session-child-count'),title=document.querySelector('.session-title');
  const label=chip.querySelector('.session-child-count-label'),activity=document.querySelector('.session-child-activity-indicator');
  const clip=document.querySelector('.session-text').getBoundingClientRect();
  const visible=e=>{const r=e.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    return r.left>=clip.left-.5&&r.right<=clip.right+.5&&!!h&&(h===e||e.contains(h)||chip.contains(h));};
  const range=document.createRange();range.setStart(title.firstChild,0);range.setEnd(title.firstChild,5);
  const firstFive=range.getBoundingClientRect(),tr=title.getBoundingClientRect();
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d'),style=getComputedStyle(title);
  ctx.font=style.font;
  // CSS ellipsis occupies painted space too; reserve it independently of the text box.
  const glyphsFit=firstFive.right+ctx.measureText('…').width<=tr.right+.5;
  range.setStart(label.firstChild,0);range.setEnd(label.firstChild,1);
  const count=range.getBoundingClientRect(),lr=label.getBoundingClientRect();
  const dot=document.querySelector('.session-item > .session-attention-indicator');
  return {contained:!!activity&&chip.contains(activity),activityCount:document.querySelectorAll('.session-child-activity-indicator').length,
    activityVisible:!!activity&&visible(activity),activityAnimation:activity&&getComputedStyle(activity,'::before').animationName,
    activityColor:activity&&getComputedStyle(activity).color,accent:getComputedStyle(dot).color,
    statusVisible:visible(chip.querySelector('.session-child-count-state')),
    statusClass:chip.querySelector('.session-child-count-state').className,
    titleWidth:tr.width,firstFiveWidth:firstFive.width,glyphsFit,
    countReadable:count.left>=lr.left-.5&&count.right<=lr.right+.5,label:label.textContent,
    aria:chip.getAttribute('aria-label'),running:t('session_child_running'),unread:t('session_child_unread'),
    ownClass:dot.className,ownAnimation:getComputedStyle(dot,'::before').animationName,
    height:document.querySelector('.session-item').getBoundingClientRect().height};
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

    results, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for viewport, touch in [(1280, False), (390, True)]:
            context = browser.new_context(viewport={'width': viewport, 'height': 800}, has_touch=touch)
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content('<main id="fixture" style="padding:8px;box-sizing:border-box;background:var(--sidebar)"></main>')
            page.add_style_tag(content=source('static/style.css'))
            page.add_script_tag(content=component_script(source('static/sessions.js')))
            page.add_script_tag(content=source('static/i18n.js'))
            page.add_script_tag(content=SCENE)
            for width in [180, 220, 240, 300]:
                page.locator('#fixture').evaluate('(e,w)=>e.style.width=w+"px"', width)
                for locale in page.evaluate('Object.keys(LOCALES)'):
                    page.evaluate('setLocale', locale)
                    for own in ['unread', 'approval']:
                        for dark in [False, True]:
                            page.evaluate('d=>{document.documentElement.dataset.skin="geist-contrast";document.documentElement.classList.toggle("dark",d)}', dark)
                            page.evaluate('scene', own)
                            data = page.evaluate('measure()')
                            failures = []
                            if not data['contained'] or not data['activityVisible'] or data['activityAnimation'] != 'spin':
                                failures.append('concurrent activity not visibly owned by chip')
                            if not data['glyphsFit']:
                                failures.append('fewer than five primary title glyphs plus ellipsis')
                            if not data['statusVisible'] or not data['countReadable']:
                                failures.append('secondary count or attention clipped')
                            if data['running'] not in data['aria'] or data['unread'] not in data['aria']:
                                failures.append('localized concurrent state inaccessible')
                            if f'is-{own if own == "unread" else "attention-approval"}' not in data['ownClass'] or data['ownAnimation'] != 'none':
                                failures.append('parent own dot changed')
                            results.append(dict(viewport=viewport,width=width,locale=locale,own=own,dark=dark,data=data,failures=failures))
                            if locale == 'en' and own == 'unread' and width in [180, 300]:
                                page.screenshot(path=str(args.output / f'{viewport}-{width}-{dark}.png'))
            page.locator('#fixture').evaluate('(e)=>e.style.width="180px"')
            for locale in page.evaluate('Object.keys(LOCALES)'):
                page.evaluate('setLocale', locale)
                for mode in ['running', 'approval', 'unread']:
                    page.evaluate('m=>{childMode=m;scene("approval")}', mode)
                    data = page.evaluate('measure()')
                    failures = []
                    if not data['glyphsFit'] or not data['countReadable'] or not data['statusVisible']:
                        failures.append('single-state chip crowds primary title or clips count/status')
                    if data['activityCount']:
                        failures.append('single-state chip duplicates activity')
                    results.append(dict(viewport=viewport,width=180,locale=locale,mode=mode,data=data,failures=failures))
            page.evaluate('childMode="concurrent";setLocale("en")')
            page.locator('#fixture').evaluate('(e)=>e.style.width="300px"')
            page.evaluate('scene("approval",false,"parent")')
            page.screenshot(path=str(args.output / f'{viewport}-selected-parent.png'))
            chip = page.locator('.session-child-count')
            if touch:
                chip.tap()
            else:
                chip.focus()
                chip.press('Enter')
            if page.locator('.session-child-session').count() != 2:
                errors.append('disclosure failed')
            page.screenshot(path=str(args.output / f'{viewport}-expanded.png'))
            page.locator('.session-child-session-delegated').click()
            if page.evaluate('opened.at(-1).sid') != 'delegated':
                errors.append('child navigation failed')
            page.locator('#fixture').evaluate('(e)=>e.style.width="180px"')
            page.evaluate('document.documentElement.classList.remove("dark");scene("unread",false,"other","detailed")')
            page.wait_for_timeout(200)
            page.screenshot(path=str(args.output / f'{viewport}-180-light-detailed.png'))
            page.evaluate('scene("idle",false,"other","detailed")')
            page.screenshot(path=str(args.output / f'{viewport}-180-light-time.png'))
            context.close()
        browser.close()
    report = dict(cases=len(results), failures=sum(bool(r['failures']) for r in results), errors=errors, results=results)
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: report[k] for k in ['cases','failures','errors']}))
    if (report['failures'] or errors) and not args.before_ref:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
