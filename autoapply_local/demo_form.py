"""Offline demo form: setup's proof that Chrome, the adapter and a script driver work.

``write`` renders ``data/demo_form.json`` into a local Greenhouse-style page in
the runtime root; ``run`` opens it in the tool's own Chrome over loopback CDP and
fills it with the Greenhouse script driver from the user's confirmed facts. The
page loads nothing, has no Submit control and stays open for the user to look
at. The resume field is reported, not uploaded: real uploads happen in apply.
"""

import html
import json
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from . import cdp as cdp_module
from .greenhouse_driver import GreenhouseDriver

PAGE = Path("demo") / "demo-form.html"


def spec():
    return json.loads(files(__package__).joinpath("data/demo_form.json").read_text(encoding="utf-8"))


def _attrs(attrs):
    return "".join(f' {html.escape(key)}="{html.escape(str(value))}"' for key, value in attrs.items())


def render(form=None):
    form = form or spec()
    rows = []
    for item in form["fields"]:
        if item["tag"] not in ("input", "textarea"):
            raise ValueError("The demo form holds only inputs and text areas")
        if item["attrs"].get("type") == "submit":
            raise ValueError("The demo form never has a Submit control")
        control = f"<{item['tag']}{_attrs(item['attrs'])}>" + ("</textarea>" if item["tag"] == "textarea" else "")
        rows.append(f'  <div class="field"><label for="{html.escape(item["attrs"]["id"])}">'
                    f'{html.escape(item["label"])}</label>\n  {control}</div>')
    title = html.escape(form.get("title", "Demo form"))
    intro = html.escape(form.get("intro", ""))
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>" + title + "</title>\n"
        "<style>body{font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem}"
        ".field{margin:1rem 0}label{display:block;font-weight:600}input,textarea{width:100%}</style>\n"
        "</head><body>\n<h1>" + title + "</h1>\n<p>" + intro + "</p>\n"
        '<form id="application" onsubmit="return false">\n' + "\n".join(rows) + "\n</form>\n</body></html>\n"
    )


def write(root):
    path = Path(root) / PAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8")
    return path


class _Tab:
    """``evaluate_script`` bound to the demo tab and the demo document only.

    Before every script the tab must still show the demo page, and the script
    itself re-checks ``location.href`` in the page, so an audit or fill never
    runs in a document the user navigated to (a real job form, for example).
    """

    def __init__(self, debug_url, cdp, url):
        self.debug_url, self.cdp, self.url = debug_url, cdp, url

    def evaluate_script(self, tab_id, function, *, operation="audit"):
        pages = [page for page in self.cdp.pages(self.debug_url) if str(page.get("id")) == str(tab_id)]
        if len(pages) != 1:
            raise ValueError("The demo tab was closed; run demo again")
        if pages[0].get("url") != self.url:
            raise ValueError("The demo tab shows another page; nothing was filled there. Run demo again")
        expression = (
            "(async () => {\nif (location.href !== " + json.dumps(self.url) + ") return {wrong_document: true};\n"
            "return await (" + function + ")();\n})()"
        )
        result = self.cdp.evaluate(pages[0]["ws"], expression, timeout=60)
        if isinstance(result, dict) and result.get("wrong_document"):
            raise ValueError("The demo tab shows another page; nothing was filled there. Run demo again")
        return result


def run(root, debug_url, profile, cdp=cdp_module):
    parsed = urlsplit(debug_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
        raise ValueError("The demo needs the tool's loopback Chrome debugging URL")
    url = write(root).resolve().as_uri()
    page = cdp.open_page(debug_url, url)
    job = {"id": "demo", "ats": "greenhouse", "company": "AutoApply Local demo", "role": "Demo form", "url": url}
    result = GreenhouseDriver().run(_Tab(debug_url, cdp, url), page["id"], profile, job)
    return {
        "status": result["status"],
        "ok": bool(result["filled"]),
        "url": url,
        "tab_id": page["id"],
        "filled": result["filled"],
        "unknown": [{"id": item["id"], "label": item.get("label"), "reason": item.get("reason")}
                    for item in result["unknown"]],
        "resume_field": result["resume_field"],
        "note": "Demo page only: nothing was sent and it has no Submit control. The tab stays open; "
        "the resume is uploaded during apply, not here.",
    }
