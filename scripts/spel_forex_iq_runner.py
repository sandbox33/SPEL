import os, sys, requests
from datetime import datetime, timezone, timedelta

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
now   = (datetime.now(timezone.utc)-timedelta(hours=5)).strftime("%H:%M ECT")

sys.path.insert(0, "scripts")
exec(open("scripts/spel_forex_iq.py").read())

results  = {p: get_forex_signal(p, verbose=False)
            for p in ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD"]}
operables= {p:s for p,s in results.items() if s["score"]>=60}
lines    = [f"SPEL Forex {now}"]

if operables:
    for pair, sig in sorted(operables.items(),
                            key=lambda x: x[1]["score"], reverse=True):
        icon = "FUERTE" if sig["score"]>=75 else "MEDIA"
        lines.append(f"{icon} {sig['pair']}: {sig['score']}/100 {sig['direction']}")
        if sig.get("operar"):
            lines.append(f"  Entry={sig['entry']} SL={sig['stop']} TP={sig['target']}")
else:
    lines.append("Sin confluencia")

requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    json={"chat_id": CHAT, "text": "\n".join(lines)}
)
print("Done")
