"""A script driver's own browser client over Chrome DevTools: no model call (plan aa-16).

The engine's MCP client runs every script and upload as a model turn. A
driver whose known fields are pure script (Workable) runs its audit, fill and
resume upload here instead, straight against the tool's own loopback Chrome,
so the model is used only for the native-click fields the script hands over.
The driver's scripts are click-guarded (``ScriptDriver.script``); this client
runs nothing but audit and fill scripts and the native file selection.
"""

from pathlib import Path

from . import cdp as cdp_module

OPERATIONS = ("audit", "fill")
ANY_FILE_INPUT = 'input[type="file"]'


class CDPScriptClient:
    def __init__(self, debug_url, resume_input=None, cdp=cdp_module):
        cdp_module._loopback(debug_url, ("http",))
        self.debug_url, self.resume_input, self.cdp = debug_url.rstrip("/"), resume_input, cdp

    def _page(self, tab_id):
        matches = [page for page in self.cdp.pages(self.debug_url) if str(page["id"]) == str(tab_id)]
        if len(matches) != 1:
            raise ValueError("Held browser tab is closed or ambiguous")
        return matches[0]

    def evaluate_script(self, tab_id, function, *, operation="audit"):
        if operation not in OPERATIONS:
            raise ValueError("Only audit and fill scripts run over DevTools")
        return self.cdp.evaluate(self._page(tab_id)["ws"], "(" + function + ")()")

    def upload_file(self, tab_id, path):
        path = Path(path).resolve()
        if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Selected resume must be a readable PDF")
        return self.cdp.set_file_input(self._page(tab_id)["ws"], self.resume_input or ANY_FILE_INPUT, str(path))
