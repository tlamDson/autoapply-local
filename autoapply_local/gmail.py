"""Read-only native Gmail connector integration with explicit account consent.

Giả định của người thực thi: runtime capability discovery is the authority for
available accounts and read/search access; verification codes never enter state.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .storage import Store


def _contains(text, phrase):
    return (
        bool(phrase)
        and re.search(r"(?<!\w)" + re.escape(str(phrase).casefold()) + r"(?!\w)", text.casefold()) is not None
    )


def _kind(message):
    text = (str(message.get("subject", "")) + " " + str(message.get("body", ""))).casefold()
    if re.search(r"verification code|security code|one.time (?:password|code)|verify (?:your|current)", text):
        return "verification"
    if re.search(r"not moving forward|not selected|regret to inform|unsuccessful|application.*rejected", text):
        return "rejected"
    if re.search(r"offer of employment|pleased to offer|offer letter", text):
        return "offer"
    if re.search(r"schedule.*interview|interview invitation|invite.*interview", text):
        return "interview"
    if re.search(r"online assessment|coding assessment|complete.*assessment", text):
        return "assessment"
    if re.search(
        r"application (?:received|submitted)|received your application|thank you for applying|application.*confirmation",
        text,
    ):
        return "confirmation"
    return "unknown"


class MailChecker:
    def __init__(self, root, runtime, clock=None):
        self.root = Path(root).resolve()
        self.runtime = runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.store = Store(self.root)

    def _permission(self, config):
        if not config.get("gmail_opt_in"):
            return {"status": "disabled", "reason": "Gmail is opt-in"}
        account = config.get("gmail_account")
        granted = config.get("gmail_permissions", [])
        if not account or not {"search", "read"} <= set(granted):
            return {"status": "manual_required", "reason": "Choose an account and explicitly grant read/search"}
        try:
            gmail = self.runtime.discover_capabilities().get("gmail", {})
        except Exception:
            return {"status": "manual_required", "reason": "Native connector unavailable"}
        if account not in gmail.get("accounts", []) or not {"search", "read"} <= set(gmail.get("permissions", [])):
            return {"status": "manual_required", "reason": "Native connector account/read capability unavailable"}
        if type(config.get("gmail_lookback", 7)) is not int or config.get("gmail_lookback", 7) < 1:
            raise ValueError("Gmail lookback must be a positive number of days")
        return None

    def _search(self, config, query):
        return self.runtime.search_mail(
            account=config["gmail_account"], query=query, lookback_days=config.get("gmail_lookback", 7)
        )

    def _read(self, account, message_id):
        message = self.runtime.read_mail(account=account, message_id=message_id)
        if not isinstance(message, dict) or message.get("account") != account:
            raise PermissionError("Connector returned another account")
        return message

    def _match(self, message):
        body = str(message.get("body", ""))
        text = str(message.get("subject", "")) + " " + body
        jobs = self.store.jobs()
        exact = [record for record in jobs if _contains(body, record["url"]) or _contains(body, record["id"])]
        if exact:
            return exact
        precise = [record for record in jobs if _contains(text, record["company"]) and _contains(body, record["role"])]
        if precise:
            return precise
        return [record for record in jobs if _contains(text, record["company"])]

    def check(self, config):
        base = {"events": [], "ambiguous": []}
        refusal = self._permission(config)
        if refusal:
            return {**base, **refusal}
        account = config["gmail_account"]
        seen = {(event.get("account"), event.get("message_id")) for event in self.store.mail_events()}
        try:
            hits = self._search(config, "application OR interview OR assessment OR offer")
            for hit in hits:
                if hit.get("account") not in (None, account):
                    raise PermissionError("Search returned another account")
                message_id = hit["id"]
                if (account, message_id) in seen:
                    continue
                message = self._read(account, message_id)
                kind = _kind(message)
                if kind in ("verification", "unknown"):
                    continue
                matches = self._match(message)
                body = str(message.get("body", ""))
                if len(matches) != 1:
                    if matches:
                        base["ambiguous"].append(
                            {"message_id": message_id, "job_ids": [record["id"] for record in matches]}
                        )
                    continue
                record = matches[0]
                # A company-only greeting cannot identify an application, even
                # if the local queue currently happens to contain just one job.
                if not (
                    _contains(body, record["url"]) or _contains(body, record["id"]) or _contains(body, record["role"])
                ):
                    base["ambiguous"].append({"message_id": message_id, "job_ids": [record["id"]]})
                    continue
                event = {
                    "message_id": message_id,
                    "account": account,
                    "job_id": record["id"],
                    "kind": kind,
                    "received_at": message.get("received_at", self.clock()),
                }
                if kind == "confirmation":
                    self.store.mark_submitted(
                        record["id"],
                        {
                            "status": "confirmed",
                            "channel": "gmail",
                            "message_id": message_id,
                            "account": account,
                            "job_id": record["id"],
                            "url": record["url"],
                            "text": "Native connector: matched application confirmation",
                            "confirmed_at": event["received_at"],
                        },
                    )
                elif self.store.has_tracker_job(record["id"]):
                    self.store.update_tracker_status(record["id"], kind, event["received_at"])
                if self.store.record_mail_event(event):
                    base["events"].append(event)
                    seen.add((account, message_id))
        except Exception:
            return {
                **base,
                "status": "manual_required",
                "reason": "Native connector failed or account permission changed",
            }
        return {**base, "status": "ok"}

    def verification_code(self, config, job_id):
        if "current_job_verification" not in config.get("gmail_permissions", []):
            raise PermissionError("Current-job verification requires separate explicit permission")
        refusal = self._permission(config)
        if refusal:
            raise PermissionError(refusal["reason"])
        record = self.store.get(job_id)
        account = config["gmail_account"]
        for hit in self._search(config, f'"{job_id}" verification OR "{record["url"]}"'):
            if hit.get("account") not in (None, account):
                continue
            message = self._read(account, hit["id"])
            if _kind(message) != "verification":
                continue
            body = str(message.get("body", ""))
            if not (_contains(body, record["url"]) or _contains(body, record["id"])):
                continue
            match = re.search(
                r"(?:verification|security|one.time)\s+(?:code|password)\s*[:=-]?\s*([A-Za-z0-9]{4,10})(?![A-Za-z0-9])",
                body,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None
