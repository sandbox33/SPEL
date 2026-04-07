#!/usr/bin/env python3
"""
spel_commons.py -- SPEL 3.0 Canonical Utilities v1.0
S47: Centralizes 5 functions duplicated across 9+ modules.

Import contract:
    from spel_commons import atomic_write, load_secrets, sha12, detect_root, tg_direct

sha12 convention (CRITICAL -- matches sha_detective.py partial-read):
    Files > 128KB: SHA-256 of header(64KB) + footer(64KB).
    Files <= 128KB: SHA-256 of full content.
    This matches the convention used when WRITING to SHA_REGISTRY.
    ALL modules must use this function -- not a full-file SHA -- to avoid
    false SHA_MISMATCH alerts on large parquets.

R21: secrets from env > file. Placeholder detection included.
R32: atomic_write uses tmp -> replace. Never partial state.
R37/Ley2: No heavy imports at module level.
EF-23: Never import or touch gdelt_foundation or critical_loss_optimized.
"""

import os
import json
import hashlib
import re
import threading
from pathlib import Path
from typing import Optional


# ---- atomic_write (R32) ----------------------------------------------------

def atomic_write(path, data: bytes) -> None:
    """Write bytes atomically: tmp -> replace. Never partial state (R32)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


# ---- load_secrets (R21) ----------------------------------------------------

_PH_RE = re.compile(r'<YOUR_|YOUR_|CHANGE_ME|PLACEHOLDER', re.I)

def load_secrets(path=None) -> dict:
    """
    Load secrets with priority: os.environ > file.
    Placeholder values treated as absent (R21).
    Returns dict of validated secret values.
    """
    file_secrets: dict = {}
    if path is not None:
        try:
            raw = json.loads(Path(path).read_text())
            file_secrets = {k: v for k, v in raw.items()
                            if v and not _PH_RE.search(str(v))}
        except Exception:
            pass

    def _get(k: str) -> str:
        v = os.environ.get(k, '')
        if v and not _PH_RE.search(v): return v
        return file_secrets.get(k, '')

    return {k: _get(k) for k in [
        'TELEGRAM_TOKEN', 'TELEGRAM_SISTEMA', 'TELEGRAM_SENALES',
        'TELEGRAM_CHAOS', 'ALPACA_API_KEY', 'ALPACA_SECRET_KEY',
        'GITHUB_TOKEN',
    ]}


# ---- sha12 (canonical partial-read -- matches sha_detective.py) -------------

def sha12(path, chars: int = 12) -> str:
    """
    SHA-256 truncated to `chars` characters.
    Convention (CRITICAL -- must match sha_detective.py):
      Files > 128KB: hash header(64KB) + footer(64KB) only.
      Files <= 128KB: hash full content.
    All SHA_REGISTRY writes must use this function.
    Using full-file SHA on a large parquet = different hash = false mismatch.
    """
    path = Path(path)
    h    = hashlib.sha256()
    try:
        size = path.stat().st_size
        CHUNK = 64 * 1024  # 64KB
        if size > 2 * CHUNK:
            with open(path, 'rb') as f:
                h.update(f.read(CHUNK))
                f.seek(-CHUNK, os.SEEK_END)
                h.update(f.read(CHUNK))
        else:
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
    except Exception:
        return 'ERR'
    return h.hexdigest()[:chars]


# ---- detect_root -----------------------------------------------------------

def detect_root() -> Path:
    """
    3-way ROOT detection (S46 PATH_COLLISION fix).
    Priority: GITHUB_ACTIONS > SPEL_BASE_DIR > Colab Drive path.
    """
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return Path(os.environ.get('GITHUB_WORKSPACE', '.'))  .resolve()
    _env = os.environ.get('SPEL_BASE_DIR', '')
    if _env:
        return Path(_env).resolve()
    _p = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
    return _p if _p.exists() else Path('.').resolve()


# ---- tg_direct (non-blocking fire-and-forget) -------------------------------

def tg_direct(token: str, chat_id: str, text: str,
               timeout: float = 5.0) -> bool:
    """
    Non-blocking Telegram dispatch via daemon thread.
    Returns immediately. Thread joins with timeout.
    Safe in GH Actions subprocess (no asyncio dependency).
    """
    import urllib.request as _ur
    if not token or not chat_id:
        return False

    def _send():
        try:
            _ur.urlopen(_ur.Request(
                f'https://api.telegram.org/bot{token}/sendMessage',
                data=json.dumps({'chat_id': chat_id, 'text': text[:4096],
                                  'parse_mode': 'HTML'}).encode(),
                headers={'Content-Type': 'application/json'}),
                timeout=timeout)
        except Exception:
            pass

    t = threading.Thread(target=_send, daemon=True)
    t.start()
    t.join(timeout=timeout + 0.5)
    return True
