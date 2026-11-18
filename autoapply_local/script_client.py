"""A script driver's own browser client over Chrome DevTools: no model call (plan aa-16).

The engine's MCP client runs every script and upload as a model turn. A
driver whose known fields are pure script (Workable) runs its audit, fill and
resume upload here instead, straight against the tool's own loopback Chrome,
so the model is used only for the native-click fields the script hands over.
The driver's scripts are click-guarded (``ScriptDriver.script``); this client
runs nothing but audit and fill scripts and the native file selection.

A held tab can leave its application form at any moment (the user navigates,
the ATS redirects to a sign-in or to another posting). Every script and the
resume upload are therefore bound to the job URL the tab is held for: they
reach only a document at that URL, checked inside the same DevTools session
that runs them, and are refused otherwise.
"""

from pathlib import Path

from . import cdp as cdp_module
from .identity import canonical_url

OPERATIONS = ("audit", "fill")
ANY_FILE_INPUT = 'input[type="file"]'


class CDPScriptClient:
    def __init__(self, debug_url, page_url, resume_input=None, cdp=cdp_module):
        """``page_url(tab_id)`` returns the job URL the tab is held for, or raises when it has none."""
        cdp_module._loopback(debug_url, ("http",))
        self.debug_url, self.page_url, self.resume_input, self.cdp = debug_url.rstrip("/"), page_url, resume_input, cdp

    def _bound(self, tab_id):
        """Predicate: is this document URL the application page the tab is held for."""
        try:
            wanted = canonical_url(self.page_url(tab_id))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Held browser tab has no job binding") from error

        def accepts(href):
            try:
                return canonical_url(href) == wanted
            except ValueError:
                return False

        return accepts

    def _on_page(self, call, tab_id, *args):
        accepts = self._bound(tab_id)
        try:
            return call(self._page(tab_id)["ws"], *args, page=accepts)
        except cdp_module.PageChangedError as error:
            raise ValueError("Held tab shows another page than the job's application form") from error

    def _page(self, tab_id):
        matches = [page for page in self.cdp.pages(self.debug_url) if str(page["id"]) == str(tab_id)]
        if len(matches) != 1:
            raise ValueError("Held browser tab is closed or ambiguous")
        return matches[0]

    def evaluate_script(self, tab_id, function, *, operation="audit"):
        if operation not in OPERATIONS:
            raise ValueError("Only audit and fill scripts run over DevTools")
        return self._on_page(self.cdp.evaluate, tab_id, "(" + function + ")()")

    def upload_file(self, tab_id, path):
        path = Path(path).resolve()
        if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Selected resume must be a readable PDF")
        return self._on_page(self.cdp.set_file_input, tab_id, self.resume_input or ANY_FILE_INPUT, str(path))
