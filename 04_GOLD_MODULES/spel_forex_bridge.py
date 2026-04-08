"""
spel_forex_bridge_fix.py — FOREX_CHAOS_ENDPOINT NameError patch (S48 Gamma)
CRASH: NameError: name 'FOREX_CHAOS_ENDPOINT' is not defined

APPLY: Copy the _ENDPOINT_CONFIG block and _resolve_endpoint() function
into spel_forex_bridge.py, replacing any bare FOREX_CHAOS_ENDPOINT reference.

R37: no torch  |  EF-23: untouched  |  R42: no hardcoded tokens
"""

import os
from typing import Optional

# ─── Endpoint configuration — resolves safely in ALL environments ─────────────
# Priority: GH Secret → Colab env → module-level default (noop sentinel)
# The noop sentinel logs the attempt and returns a structured no-op response
# instead of crashing the entire forex cycle.

_ENDPOINT_CONFIG: dict = {
    # Primary chaos endpoint — injected by GH Actions secret or Colab env
    "primary": os.getenv(
        "FOREX_CHAOS_ENDPOINT",
        "https://sentinel.spel.internal/chaos/noop"   # safe default — never raises
    ),
    # Fallback: direct TG alert if primary unreachable
    "fallback_mode": "TG_CAOS",
    # Timeout for HTTP calls to chaos endpoint
    "timeout_s": 6,
}


def _resolve_endpoint(key: str = "primary") -> str:
    """
    Return the chaos endpoint URL. Always returns a string, never raises.
    Replaces all bare FOREX_CHAOS_ENDPOINT references in the codebase.

    Usage (drop-in replacement):
        # Before (crashes):
        response = requests.post(FOREX_CHAOS_ENDPOINT, json=payload)
        # After (safe):
        response = requests.post(_resolve_endpoint(), json=payload, timeout=_ENDPOINT_CONFIG['timeout_s'])
    """
    return _ENDPOINT_CONFIG.get(key, _ENDPOINT_CONFIG["primary"])


# ─── Hardened call wrapper (replaces raw requests.post to chaos endpoint) ─────

def _call_chaos_endpoint(
    payload: dict,
    endpoint: Optional[str] = None,
    timeout: int = 6,
) -> dict:
    """
    Post payload to chaos endpoint. Returns structured result dict.
    NEVER raises — degrades to TG fallback silently.

    Returns:
        {"status": "ok"|"noop"|"error", "source": str, "response": dict|None}
    """
    import urllib.request, urllib.error, json as _json

    url = endpoint or _resolve_endpoint()
    is_noop = "noop" in url or "sentinel.spel.internal" in url

    if is_noop:
        # Noop sentinel: don't make the HTTP call, just log
        _tg_chaos_alert(f"FOREX_CHAOS noop — endpoint not configured. Payload keys: {list(payload.keys())}")
        return {"status": "noop", "source": "default_sentinel", "response": None}

    try:
        data = _json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": "ok", "source": url, "response": _json.loads(body) if body else {}}

    except urllib.error.URLError as e:
        _tg_chaos_alert(f"FOREX_CHAOS endpoint unreachable: {url} → {e}")
        return {"status": "error", "source": url, "response": None}
    except Exception as e:
        _tg_chaos_alert(f"FOREX_CHAOS unexpected error: {e}")
        return {"status": "error", "source": url, "response": None}


def _tg_chaos_alert(msg: str) -> None:
    """Minimal TG alert to CAOS channel. No external deps."""
    import urllib.request, json as _json, os as _os
    tok  = _os.getenv("TELEGRAM_TOKEN", "")
    chat = _os.getenv("TELEGRAM_CAOS", "") or _os.getenv("TELEGRAM_SISTEMA", "")
    if not tok or not chat:
        print(f"[FOREX_CHAOS] {msg}")
        return
    try:
        payload = _json.dumps({"chat_id": chat, "text": f"⚡ FOREX CHAOS\n{msg[:3800]}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        print(f"[FOREX_CHAOS TG fail] {msg}")


# ─── PATCH INSTRUCTIONS FOR spel_forex_bridge.py ─────────────────────────────
#
# 1. Add at top of spel_forex_bridge.py (after stdlib imports, before class):
#
#    from spel_forex_bridge_fix import _resolve_endpoint, _call_chaos_endpoint
#    # OR paste the three functions above directly into the file
#
# 2. Replace every occurrence of bare FOREX_CHAOS_ENDPOINT with _resolve_endpoint():
#
#    grep -n "FOREX_CHAOS_ENDPOINT" 04_GOLD_MODULES/spel_forex_bridge.py
#    # Then replace:
#    #   FOREX_CHAOS_ENDPOINT → _resolve_endpoint()
#    #   requests.post(FOREX_CHAOS_ENDPOINT, ...) → _call_chaos_endpoint(payload)
#
# 3. In spel_universe.yml (already done) — add to env block:
#    FOREX_CHAOS_ENDPOINT: ${{ secrets.FOREX_CHAOS_ENDPOINT || 'https://sentinel.spel.internal/chaos/noop' }}
#
# 4. In GH repo → Settings → Secrets → Actions:
#    Add secret: FOREX_CHAOS_ENDPOINT = <your real endpoint URL>
#    If you don't have one yet, leave it unset — the noop sentinel handles it gracefully.
#
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    # Quick self-test
    print(f"Endpoint: {_resolve_endpoint()}")
    result = _call_chaos_endpoint({"test": True, "session": "S49"})
    print(f"Result:   {result}")
