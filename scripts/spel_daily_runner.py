"""
SPEL — Daily Runner v1.0
Una sola celda que hace todo:
  1. Calcula Score de Oro para los 4 activos core
  2. Calcula señales forex para IQ Option
  3. Manda reporte completo a Telegram
  4. Guarda log del día

Uso en Colab (después de spel_session_start.py):
    exec(open('/content/drive/MyDrive/SPEL-v2.0/scripts/spel_daily_runner.py').read())
    run_daily()           # reporte completo a Telegram
    run_forex_alert(15)   # solo forex en 15M a Telegram
"""

import os, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT           = Path('/content/drive/MyDrive/SPEL-v2.0')
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── CONFIGURAR TOKENS (rellenar o usar Colab Secrets) ────────
# Si no usas Colab Secrets, pegar aquí directamente:
# TELEGRAM_TOKEN = "tu_token_aqui"
# TELEGRAM_CHAT  = "tu_chat_id_aqui"

def _tg(texto: str) -> bool:
    """Manda mensaje a Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("⚠️  Telegram no configurado — imprimir solo en pantalla")
        print(texto)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT, "text": texto, "parse_mode": "Markdown"}
    )
    return r.status_code == 200


def run_daily(capital: float = 10.0):
    """
    Reporte diario completo:
      - Score de Oro de los 4 activos core
      - Señales forex para las próximas sesiones
      - Alerta si hay señal viable
    """
    now_ect = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M ECT')
    msg_parts = [f"📊 *SPEL Reporte Diario*\n_{now_ect}_\n"]

    # ── Activos core ──────────────────────────────────────────
    msg_parts.append("*── Activos Core ──*")
    try:
        core_results = score_all(capital=capital, verbose=False)
        for asset, r in core_results.items():
            icon  = '🟢' if r.viable else ('🟡' if r.score_oro >= 60 else '⛔')
            msg_parts.append(
                f"{icon} *{asset}*: score={r.score_oro} | "
                f"{r.direction} | {r.modo} | "
                f"Gödel={'✅' if r.godel_active else '○'}"
            )
            if r.viable:
                msg_parts.append(
                    f"   ⭐ Kelly={r.kelly_fraction:.4f} | "
                    f"Entry viable con ${capital*r.kelly_fraction:.2f}"
                )
    except NameError:
        msg_parts.append("⚠️ score_all() no disponible — cargar spel_score_engine.py primero")

    # ── Señales forex ─────────────────────────────────────────
    msg_parts.append("\n*── Forex IQ Option ──*")
    try:
        forex_results = run_forex_dashboard(15, _silent=True)
        best_forex = sorted(forex_results.items(),
                            key=lambda x: x[1].get('score', 0), reverse=True)
        for pair, sig in best_forex[:3]:
            icon  = '🟢' if sig['score'] >= 75 else ('🟡' if sig['score'] >= 60 else '⛔')
            msg_parts.append(
                f"{icon} *{sig['pair']}*: score={sig['score']} | "
                f"{sig['direction']} | {sig['signal'][:20]}"
            )
            if sig['operar']:
                msg_parts.append(
                    f"   Entry={sig['entry']} | SL={sig['stop']} | TP={sig['target']}"
                    f" | R:R 1:2"
                )
    except NameError:
        msg_parts.append("⚠️ run_forex_dashboard() no disponible — cargar spel_forex_iq.py")

    # ── Próximas sesiones ─────────────────────────────────────
    now_utc   = datetime.now(timezone.utc)
    hour_utc  = now_utc.hour
    hour_ect  = (hour_utc - 5) % 24

    msg_parts.append(f"\n*── Próximas sesiones ──*")
    if hour_ect < 8:
        mins_to_london = (8 - hour_ect) * 60
        msg_parts.append(f"⏰ Londres abre en ~{mins_to_london}min (08:00 ECT)")
        msg_parts.append("   EUR/USD y GBP/USD serán los más activos")
    elif 8 <= hour_ect < 12:
        msg_parts.append("🟢 *AHORA: Overlap Londres-NY* — mejor momento")
        msg_parts.append("   EUR/USD, GBP/USD, USD/CHF operables")
    elif hour_ect < 15:
        msg_parts.append("🟡 Sesión NY activa — NVDA y XAU operables")
    else:
        msg_parts.append("⛔ Sesión cerrada — revisar mañana 08:00 ECT")

    msg_parts.append(f"\n_SHA BTC: {json.load(open(ROOT/'meta/SHA_REGISTRY.json'))['BTC']['sha_v5']}_")

    full_msg = "\n".join(msg_parts)
    sent = _tg(full_msg)
    if sent:
        print("✅ Reporte enviado a Telegram")
    return full_msg


def run_forex_alert(tf_minutes: int = 15, min_score: int = 60):
    """
    Manda a Telegram SOLO los pares con score >= min_score.
    Ideal para configurar como alerta horaria.
    """
    now_ect = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%H:%M ECT')

    try:
        results = run_forex_dashboard(tf_minutes, _silent=True)
    except NameError:
        _tg("⚠️ spel_forex_iq.py no cargado")
        return

    operables = {p: s for p, s in results.items() if s['score'] >= min_score}

    if not operables:
        # Sin señales — no molestar con Telegram
        print(f"Sin señales ≥{min_score} a las {now_ect}")
        return

    lines = [f"⚡ *SPEL Forex Alert* — {now_ect} | {tf_minutes}M\n"]
    for pair, sig in sorted(operables.items(),
                             key=lambda x: x[1]['score'], reverse=True):
        icon = '🟢' if sig['score'] >= 75 else '🟡'
        lines.append(f"{icon} *{sig['pair']}*")
        lines.append(f"   Score: {sig['score']}/100 | {sig['direction']}")
        if sig['operar']:
            lines.append(f"   Entry: {sig['entry']}")
            lines.append(f"   Stop:  {sig['stop']} ({sig['stop_pips']:.1f} pips)")
            lines.append(f"   Target:{sig['target']} ({sig['target_pips']:.1f} pips)")
            lines.append(f"   R:R 1:2 | {sig['layers']['session']['session']}")
        lines.append("")

    msg = "\n".join(lines)
    sent = _tg(msg)
    if sent:
        print(f"✅ Alerta enviada: {len(operables)} par(es) con score ≥{min_score}")
    return msg


def setup_telegram(token: str, chat_id: str):
    """Configura Telegram en esta sesión."""
    global TELEGRAM_TOKEN, TELEGRAM_CHAT
    TELEGRAM_TOKEN = token
    TELEGRAM_CHAT  = str(chat_id)
    # Mandar mensaje de prueba
    ok = _tg("✅ *SPEL conectado* — recibirás señales aquí")
    if ok:
        print("✅ Telegram configurado correctamente")
    else:
        print("❌ Error — verificar token y chat_id")


# Patch silencioso para run_forex_dashboard (acepta _silent kwarg)
try:
    _orig_forex = run_forex_dashboard
    def run_forex_dashboard(tf_minutes=15, _silent=False, **kw):
        import io, sys
        if _silent:
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                result = _orig_forex(tf_minutes, **kw)
            finally:
                sys.stdout = old_stdout
            return result
        return _orig_forex(tf_minutes, **kw)
except NameError:
    pass  # spel_forex_iq.py no cargado aún — OK

print("✅ Daily Runner v1.0 cargado")
print("   setup_telegram('TOKEN', 'CHAT_ID')  → configurar")
print("   run_daily()                          → reporte completo")
print("   run_forex_alert(15)                  → solo alertas forex")
