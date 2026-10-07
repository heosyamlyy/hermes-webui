"""Reference-only chip in production density/attention/locale CSS contexts."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests._sidebar_child_status_helpers import ROOT, component_script  # noqa: E402
from tests.browser_child_finishing_ux import SCENE  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
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
            page.add_script_tag(content=SCENE + r"""
function referenceScene(state,density){
 scene(true,'approval','other');
 const s=fixtureSessions.find(s=>s.session_id==='parent');
 s.has_unread=state==='unread';s.is_streaming=state==='running';
 s.attention=['approval','clarify'].includes(state)?{kind:state,count:1}:null;
 window._sidebarDensity=density;
 const row=_attachChildSessionsToSidebarRows([s],[s],fixtureSessions)[0];
 document.querySelector('#fixture').replaceChildren(_renderOneSession(row));
 document.querySelectorAll('*').forEach(e=>e.scrollLeft=0);
}
function referenceGeometry(){
 const chip=document.querySelector('.session-child-count'),label=chip.querySelector('.session-child-count-label');
 const mark=chip.querySelector('.session-child-count-state'),clip=document.querySelector('.session-text').getBoundingClientRect();
 const r=mark.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
 const range=document.createRange();range.selectNodeContents(label);
 const lr=label.getBoundingClientRect(),textWidth=range.getBoundingClientRect().width;
 return {label:label.textContent,labelWidth:lr.width,textWidth,labelReadable:textWidth<=lr.width+.5,
   titleWidth:document.querySelector('.session-title').getBoundingClientRect().width,
   markVisible:r.left>=clip.left&&r.right<=clip.right&&!!hit&&(hit===mark||mark.contains(hit)),
   role:chip.getAttribute('role'),tooltip:chip.title};
}
""")
            page.evaluate('document.documentElement.dataset.skin="graphite";document.documentElement.classList.add("dark")')
            locales = page.evaluate('Object.keys(LOCALES)')
            for width in [180, 300, 360]:
                page.locator('#fixture').evaluate('(e,w)=>e.style.width=w+"px"', width)
                for locale in locales:
                    page.evaluate('setLocale', locale)
                    for density in ['compact', 'detailed']:
                        for state in ['idle', 'unread', 'approval', 'clarify', 'running']:
                            page.evaluate('([s,d])=>referenceScene(s,d)', [state, density])
                            data = page.evaluate('referenceGeometry()')
                            failures = []
                            if not data['markVisible'] or data['titleWidth'] < 24:
                                failures.append('status mark or title floor lost')
                            if width >= 300 and not data['labelReadable']:
                                failures.append('child-qualified label clipped at normal width')
                            if data['role'] != 'img':
                                failures.append('reference-only chip became an expander')
                            if locale == 'de' and width == 300 and density == 'compact' and state == 'approval':
                                page.screenshot(path=str(args.output / f'{viewport}-de-300-dark.png'))
                                page.evaluate('document.documentElement.classList.remove("dark")')
                                page.wait_for_timeout(180)
                                page.screenshot(path=str(args.output / f'{viewport}-de-300-light.png'))
                                page.evaluate('document.documentElement.classList.add("dark")')
                            results.append(dict(viewport=viewport, width=width, locale=locale, density=density,
                                                state=state, data=data, failures=failures))
            page.evaluate('setLocale("de");document.documentElement.classList.add("dark")')
            for width in [180, 300]:
                page.locator('#fixture').evaluate('(e,w)=>e.style.width=w+"px"', width)
                page.evaluate('referenceScene("approval","detailed")')
                page.wait_for_timeout(180)
                page.screenshot(path=str(args.output / f'{viewport}-de-{width}-detailed-dark.png'))
            context.close()
        browser.close()
    (args.output / 'report.json').write_text(json.dumps(dict(results=results, errors=errors), indent=2))
    print(json.dumps(dict(cases=len(results), failures=sum(bool(r['failures']) for r in results), errors=errors)))
    if errors or any(r['failures'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
