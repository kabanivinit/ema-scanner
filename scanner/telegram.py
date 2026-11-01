"""Telegram Bot API sender (token is never printed)."""
from __future__ import annotations

import os
import re
import time

import requests

API = "https://api.telegram.org"


class Telegram:
    def __init__(self, token: str, chat_id: str, post=None, sleep=None):
        self.token, self.chat_id = token, chat_id
        self._post = post or (lambda *a, **k: requests.post(*a, **k))
        self._sleep = sleep or (lambda s: time.sleep(s))
        self.last_error = ""

    @classmethod
    def from_env(cls):
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        return cls(token, chat) if token and chat else None

    def _scrub(self, text) -> str:
        return str(text).replace(self.token, "***")

    def _call(self, method: str, payload=None, files=None, tries: int = 4):
        url = f"{API}/bot{self.token}/{method}"
        for n in range(tries):
            try:
                if files:
                    r = self._post(url, data=payload, files=files, timeout=120)
                else:
                    r = self._post(url, json=payload, timeout=30)
            except Exception as e:                              # noqa: BLE001
                self.last_error = self._scrub(e)[:200]
                self._sleep(2 * (n + 1))
                continue
            try:
                body = r.json()
            except Exception:                                   # noqa: BLE001
                body = {}
            if r.status_code == 200 and body.get("ok"):
                return body
            self.last_error = self._scrub(body.get("description", f"HTTP {r.status_code}"))[:200]
            if r.status_code == 429:
                wait = int((body.get("parameters") or {}).get("retry_after", 5))
                self._sleep(min(wait + 1, 60))
                continue
            if r.status_code >= 500:
                self._sleep(2 * (n + 1))
                continue
            return None                                         # 4xx: retrying will not help
        return None

    def get_me(self):
        body = self._call("getMe", tries=2)
        return body["result"] if body else None

    def send(self, text: str) -> bool:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if self._call("sendMessage", payload):
            return True
        if "parse entities" in self.last_error.lower():         # fall back to plain text
            payload.pop("parse_mode")
            payload["text"] = re.sub(r"<[^>]+>", "", text)
            return bool(self._call("sendMessage", payload))
        return False

    def send_document(self, path: str, caption: str = "") -> bool:
        with open(path, "rb") as fh:
            body = self._call("sendDocument", {"chat_id": self.chat_id, "caption": caption[:900]},
                              files={"document": fh})
        return bool(body)


class ConsoleSender:
    """Used for --dry-run: prints what would be sent."""
    last_error = ""

    def send(self, text: str) -> bool:
        print("\n----- (dry run) Telegram message -----")
        print(re.sub(r"<[^>]+>", "", text).replace("&amp;", "&"))
        return True

    def send_document(self, path: str, caption: str = "") -> bool:
        print(f"\n----- (dry run) would send file: {path} -----")
        return True
