import os,sys,json,requests
from datetime import datetime,timezone,timedelta

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT  = os.environ.get("TELEGRAM_SENALES", os.environ.get("TELEGRAM_CHAT_ID",""))
now   = (datetime.now(timezone.utc)-timedelta(hours=5)).strftime("%H:%M ECT")

sys.path.insert(0,"scripts")
SNAPSHOT = json.load(open("meta/forex_macro_snapshot.json"))["pairs"]
_code    = open("scripts/spel_forex_iq.py").read()
_g       = {}
exec(_code, _g)
_orig    = _g["get_gdelt_macro"]

def get_gdelt_macro_patched(pair):
    snap = SNAPSHOT.get(pair)
    if not snap: return _orig(pair)
    risk_on = ["EURUSD","GBPUSD","AUDUSD"]
    fear    = snap["fear_momentum"]
    godel   = snap["godel_active"]
    nash    = snap["nash_frozen"]
    if godel and fear>0.05:
        bias  = "SHORT" if pair in risk_on else "LONG"
        score = min(int(abs(fear)*100),35)
    elif godel and fear<-0.05:
        bias  = "LONG" if pair in risk_on else "SHORT"
        score = min(int(abs(fear)*100),35)
    else:
        bias,score = "NEUTRAL",10
    if nash>0.75: score = min(score+10,40)
    return {"score":score,"bias":bias,"godel_active":godel,
            "entropy":snap["entropy"],"p90":snap["p90"],
            "vitality":snap["vitality"],"compression":nash>0.75,
            "nash_frozen":nash,"proxy":snap["proxy"]}

_g2 = {**_g,"get_gdelt_macro":get_gdelt_macro_patched}
exec(_code,_g2)
get_forex_signal = _g2["get_forex_signal"]

results  = {p:get_forex_signal(p,verbose=False)
            for p in ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD"]}
operables= {p:s for p,s in results.items() if s["score"]>=60}

lines = [f"SPEL Forex {now}"]
if operables:
    for pair,sig in sorted(operables.items(),
                           key=lambda x:x[1]["score"],reverse=True):
        icon = "FUERTE" if sig["score"]>=75 else "MEDIA"
        lines.append(f"{icon} {sig['pair']}: {sig['score']}/100 {sig['direction']}")
        if sig.get("operar"):
            lines.append(f"  Entry={sig['entry']} SL={sig['stop']} TP={sig['target']}")
        l = sig["layers"]
        lines.append(f"  M:{l['macro']['score']} E:{l['struct']['score']} "
                     f"V:{l['vwap']['score']} S:{l['session']['score']}")
else:
    lines.append("Sin confluencia")

requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
              json={"chat_id":CHAT,"text":"\n".join(lines)})
print("Done")
