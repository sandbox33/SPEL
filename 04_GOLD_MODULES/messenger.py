"""
Holmes/modules/telegram/messenger.py
TelegramRouter: 4-channel routing with SISTEMA fallback.
[FIX S41]: TELEGRAM_CHAOS → TELEGRAM_CHAOS (nombre unificado con dna_sovereign.py y S41).
R34: secret names verified before use.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Optional

log = logging.getLogger("Holmes.TelegramRouter")

# Canal CAOS: env var unificada como TELEGRAM_CHAOS (no TELEGRAM_CHAOS)
# Todos los módulos del sistema usan TELEGRAM_CHAOS. No TELEGRAM_CHAOS.
_CHANNELS: dict[str, str] = {
    "SISTEMA": os.environ.get("TELEGRAM_SISTEMA", "-1003712424420"),
    "SIGNALS": os.environ.get("TELEGRAM_SENALES", "-1003733702589"),
    "BACKUP":  os.environ.get("TELEGRAM_BACKUP",  "-1003761735254"),
    "CAOS":    os.environ.get("TELEGRAM_CHAOS",    ""),   # [FIX] era TELEGRAM_CHAOS
}
_API_BASE = "https://api.telegram.org"
_TIMEOUT  = 8


class TelegramRouter:
    """
    Routes messages to the correct channel.
    Falls back to SISTEMA if target channel not configured.
    Non-blocking on failure — logs error, never raises.
    """

    def __init__(self, token: Optional[str] = None):
        self._token = token or os.environ.get("TELEGRAM_TOKEN", "")
        if not self._token:
            log.warning("TELEGRAM_TOKEN absent — TelegramRouter silenciado")

    def send(self, channel: str, text: str) -> bool:
        """Send to named channel. Falls back to SISTEMA if chat_id empty."""
        chat_id = _CHANNELS.get(channel.upper(), "")
        if not chat_id:
            chat_id = _CHANNELS["SISTEMA"]
            text    = f"[{channel} fallback → SISTEMA]\n{text}"
        return self._post(chat_id, text)

    def send_to_sistema(self, text: str) -> bool:
        return self.send("SISTEMA", text)

    def send_to_backup(self, text: str) -> bool:
        return self.send("BACKUP", text)

    def send_to_signals(self, text: str) -> bool:
        return self.send("SIGNALS", text)

    def send_to_caos(self, text: str) -> bool:
        return self.send("CAOS", text)

    def _post(self, chat_id: str, text: str) -> bool:
        if not self._token or not chat_id:
            return False
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       text[:4096],
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"{_API_BASE}/bot{self._token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return r.status == 200
        except Exception as e:
            log.error("TG send failed chat_id=%s: %s", chat_id[:6], e)
            return False
