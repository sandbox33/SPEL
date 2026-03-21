"""
SPEL v37 — MASTER AUDITOR v2  (reconstruido S32)
10 checks: A Secrets · B SHA · C GodelOR · D Checkpoints
           E META · F Forex · G GitHub · H YML · I Score · J Drift
"""
from google.colab import drive, userdata
try: drive.mount("/content/drive")
except ValueError: pass

import os,sys,json,base64,hashlib,importlib.util,urllib.request
import numpy as np
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(os.environ.get("SPEL_BASE_DIR","/content/drive/MyDrive/SPEL-v2.0"))
for p in [str(ROOT/"scripts"),str(ROOT/"codigo/core")]:
    if p not in sys.path: sys.path.insert(0,p)
for k in ["TELEGRAM_TOKEN","TELEGRAM_SENALES","TELEGRAM_SISTEMA",
          "TELEGRAM_BACKUP","TELEGRAM_CHAT_ID","GITHUB_TOKEN"]:
    v=userdata.get(k)
    if v: os.environ[k]=v

TOKEN=os.environ.get("TELEGRAM_TOKEN",""); TG_SEN=os.environ.get("TELEGRAM_SENALES","")
TG_SIST=os.environ.get("TELEGRAM_SISTEMA",""); TG_BAK=os.environ.get("TELEGRAM_BACKUP","")
GH_TOK=os.environ.get("GITHUB_TOKEN",""); REPO="sandbox33/SPEL"

PROXY_P90_MAP={"NVDA":1.189820,"XAU":1.350316}
CANONICAL_P90={"BTC":1.170901,"XAU":1.350316,"NIFTY50":1.186823,"NVDA":1.189820}
ASSETS=["BTC","XAU","NIFTY50","NVDA"]
CKPT_NAMES={"BTC":"BTC_LSTM_v3c_F1_0.4994.pt","XAU":"XAU_LSTM_v3_godel_valloss0.4386.pt",
            "NIFTY50":"NIFTY50_LSTM_v3_godel_valloss0.3784.pt","NVDA":"NVDA_LSTM_v3_godel_valloss0.3857.pt"}
TENSOR_COLS=["high","low","log_return","entropy_shannon","entropy_decay_lambda",
             "entropy_psych_vix","fibonacci_lag_1","fibonacci_lag_2","fibonacci_lag_3",
             "fibonacci_lag_5","fibonacci_lag_8","fibonacci_lag_13","fibonacci_lag_21",
             "goldstein_geo","n_events_ohlcv","vitality_tesla","mass_panic_index",
             "fear_momentum","vix_norm","nash_frozen_7d"]
SEP="="*62

def sha12(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda:f.read(65536),b""): h.update(c)
    return h.hexdigest()[:12]

def tg_send(cid,txt):
    if not TOKEN or not cid: return None
    p=json.dumps({"chat_id":cid,"text":txt}).encode()
    req=urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=p,headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=15) as r: return r.status
    except: return None

def gh_get(path):
    req=urllib.request.Request(f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers={"Authorization":f"token {GH_TOK}","Accept":"application/vnd.github.v3+json"})
    with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())

def gh_decode(d): return base64.b64decode(d["content"].replace("\n","")).decode()

def sync_snapshot():
    if not GH_TOK: return None
    try:
        d=gh_get("meta/forex_macro_snapshot.json"); t=gh_decode(d)
        (ROOT/"meta/forex_macro_snapshot.json").write_text(t); return json.loads(t)
    except: return None

def load_registry():
    raw=json.loads((ROOT/"meta/SHA_REGISTRY.json").read_text())
    meta=json.loads((ROOT/"meta/SPEL_META.json").read_text())
    cr=meta.get("checkpoint_registry",{}); lb=meta.get("lookbacks",{})
    out={}
    for a in ASSETS:
        d=raw.get(a,{})
        out[a]={"sha":d.get("sha_v5","???"),"p90":float(d.get("p90_entropy",CANONICAL_P90[a])),
                "rows":d.get("n_rows","?"),"checkpoint":cr.get(a,{}).get("filename","?"),
                "lookback":lb.get(a,"?"),"scaler_mean":cr.get(a,{}).get("scaler_mean",[]),
                "scaler_std":cr.get(a,{}).get("scaler_std",[])}
    return out

print(SEP); print("SPEL BOOTSTRAP OK — "+datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")); print(SEP)

def run_master_audit(notify=True,abort_on_critical=True,session_tag="S32"):
    try: import polars as pl; HP=True
    except: HP=False
    ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rep={"ts":ts,"session":session_tag,"checks":{},"critical":[],"warnings":[],"ok":[]}
    def check(n,passed,detail="",critical=False):
        rep["checks"][n]={"passed":passed,"detail":detail,"critical":critical}
        if passed: rep["ok"].append(n); print("  OK  "+n+("  "+detail if detail else ""))
        else:
            (rep["critical"] if critical else rep["warnings"]).append(n)
            print(("  CRIT " if critical else "  WARN ")+n+": "+detail)
    print("\n"+SEP); print("MASTER AUDIT — "+ts+" ["+session_tag+"]"); print(SEP)

    # A
    print("\n[A] SECRETS")
    for k,v in [("TELEGRAM_TOKEN",TOKEN),("TG_SENALES",TG_SEN),("TG_SISTEMA",TG_SIST),
                ("TG_BACKUP",TG_BAK),("GH_TOKEN",GH_TOK)]:
        check("secret."+k,bool(v),critical=(k in ("TELEGRAM_TOKEN","GH_TOKEN")))

    # B
    print("\n[B] SHA PARQUETS"); reg={}
    try:
        reg=load_registry()
        for a in ASSETS:
            pd=ROOT/"data_lake"/a/"ohlcv"/"aggregated"
            ps=sorted(pd.glob("*.parquet")) if pd.exists() else []
            if not ps: check("sha."+a,False,"NOT FOUND",critical=True); continue
            p=ps[-1]
            if p.stat().st_size<100: check("sha."+a,False,"GHOST",critical=True); continue
            sr=sha12(p); se=reg[a]["sha"]
            check("sha."+a,sr==se,sr+" reg="+se)
    except Exception as e: check("sha.registry_load",False,str(e),critical=True)

    # C
    print("\n[C] GODEL OR%")
    if HP and reg:
        for a in ASSETS:
            pd=ROOT/"data_lake"/a/"ohlcv"/"aggregated"
            ps=sorted(pd.glob("*.parquet")) if pd.exists() else []
            if not ps: continue
            try:
                df=pl.read_parquet(ps[-1]); p90=reg.get(a,{}).get("p90",CANONICAL_P90[a])
                mask=df["entropy_shannon"]>=p90
                if "vitality_tesla" in df.columns: mask=mask|(df["vitality_tesla"]==9)
                pct=float(mask.mean())*100
                check("godel_or."+a,30<=pct<=48,f"{pct:.1f}% p90={p90:.4f}",critical=not(30<=pct<=48))
            except Exception as e: check("godel_or."+a,False,str(e))

    # D
    print("\n[D] CHECKPOINTS")
    for a,fn in CKPT_NAMES.items():
        p=ROOT/"checkpoints"/fn
        if not p.exists(): check("ckpt."+a,False,"NOT FOUND R29",critical=True)
        elif p.stat().st_size<1000: check("ckpt."+a,False,"GHOST",critical=True)
        else: check("ckpt."+a,True,f"{p.stat().st_size/1048576:.2f}MB")

    # E
    print("\n[E] SPEL_META")
    try:
        m=json.loads((ROOT/"meta/SPEL_META.json").read_text()); fc=m.get("feature_columns",[])
        check("meta.input_size_20",m.get("input_size",0)==20,str(m.get("input_size")),critical=True)
        check("meta.feature_cols_20",len(fc)==20,str(len(fc)),critical=True)
        check("meta.feature_cols_order",(fc[0]=="high") if fc else False,fc[0] if fc else "EMPTY",critical=True)
        check("meta.godel_thresholds",bool(m.get("godel_thresholds")),str(m.get("godel_thresholds",{})))
    except Exception as e: check("meta.load",False,str(e),critical=True)

    # F
    print("\n[F] FOREX SNAPSHOT"); snap=sync_snapshot()
    try:
        fms=json.loads((ROOT/"meta/forex_macro_snapshot.json").read_text())
        pairs=fms.get("pairs",{})
        if snap: print("  synced from GitHub: "+fms.get("updated","?")[:16])
        check("forex.exists",bool(pairs),"updated="+fms.get("updated","?")[:10])
        ents=set(round(p.get("entropy",0),4) for p in pairs.values())
        check("forex.PC1_uniform",len(ents)!=1,
              "ACTIVO="+str(list(ents)) if len(ents)==1 else "diferenciada")
        for pair,data in pairs.items():
            proxy=data.get("proxy","?"); ent=data.get("entropy",0)
            p90s=data.get("p90",0); p90r=PROXY_P90_MAP.get(proxy,p90s)
            exp=bool(ent>=p90r); act=data.get("godel_active",None)
            check("forex.godel."+pair,act==exp and act is not None,
                  f"got={act} exp={exp} ent={ent:.4f} p90={p90r:.4f}",
                  critical=(act!=exp or act is None))
    except Exception as e: check("forex.load",False,str(e),critical=True)

    # G
    print("\n[G] GITHUB SYNC")
    if GH_TOK:
        for fp,(ms,ic) in {"meta/SHA_REGISTRY.json":(1000,False),
            "meta/SPEL_META.json":(5000,False),"meta/forex_macro_snapshot.json":(500,False),
            "scripts/spel_score_engine.py":(10000,True),
            "scripts/spel_snapshot_updater.py":(1000,True),
            "scripts/SPEL_v37_MASTER_AUDITOR_v2.py":(5000,True),
            ".github/workflows/spel_daily.yml":(500,True)}.items():
            try:
                d=gh_get(fp); sz=d.get("size",0)
                check("gh."+fp.split("/")[-1],sz>=ms,str(sz)+"b",critical=ic and sz<ms)
            except urllib.error.HTTPError as e:
                check("gh."+fp.split("/")[-1],False,"HTTP "+str(e.code),critical=ic)
            except Exception as e:
                check("gh."+fp.split("/")[-1],False,str(e)[:40])

    # H
    print("\n[H] ACTIONS YML")
    if GH_TOK:
        try:
            d=gh_get(".github/workflows/spel_daily.yml")
            yml=base64.b64decode(d["content"].replace("\n","")).decode()
            check("yml.cron",'"45 12 * * 1-5"' in yml,critical=True)
            check("yml.ef17_fixed","TELEGRAM_CHAT_ID" not in yml,
                  "CHAT_ID present" if "TELEGRAM_CHAT_ID" in yml else "OK",
                  critical="TELEGRAM_CHAT_ID" in yml)
            check("yml.healthcheck","last_signal.json" in yml or "healthcheck" in yml.lower(),critical=True)
            check("yml.senales_ref","TELEGRAM_SENALES" in yml)
            check("yml.needs_update","needs: update_snapshot" in yml or "needs: [update_snapshot]" in yml,critical=True)
        except Exception as e: check("yml.load",False,str(e),critical=True)

    # I
    print("\n[I] SCORE ENGINE")
    se=ROOT/"scripts/spel_score_engine.py"; mod=None
    if not se.exists(): check("score_engine.exists",False,"NOT FOUND",critical=True)
    else:
        try:
            spec=importlib.util.spec_from_file_location("sce_audit",se)
            mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            check("score_engine.load",True)
        except Exception as e: check("score_engine.load",False,str(e)[:60],critical=True); mod=None
    if mod:
        src=se.read_text()
        check("score_engine.nan_filter","df.filter(pl.col('close').is_not_null(" in src,critical=True)
        if hasattr(mod,"score"):
            for a in ASSETS:
                try:
                    r=mod.score(a); raw=r.__dict__ if hasattr(r,"__dict__") else {}
                    scr=raw.get("score_oro",raw.get("score",-1))
                    god=raw.get("godel_active",None); via=raw.get("viable",None)
                    kelly=raw.get("kelly_fraction",raw.get("kelly",-1))
                    sha_p=raw.get("sha_parquet","?")
                    sha_ok=(sha_p==reg.get(a,{}).get("sha",""))
                    check("score."+a,isinstance(scr,(int,float)) and scr>=0,
                          f"score={scr} godel={god} viable={via} kelly={kelly} sha={'OK' if sha_ok else sha_p}",
                          critical=not(isinstance(scr,(int,float)) and scr>=0))
                except Exception as e: check("score."+a,False,str(e)[:60],critical=True)

    # J
    print("\n[J] SCALER DRIFT (Mahalanobis)")
    if HP and reg:
        try:
            meta=json.loads((ROOT/"meta/SPEL_META.json").read_text())
            cr=meta.get("checkpoint_registry",{}); fc=meta.get("feature_columns",[])
            fc_n=[c.replace("\u03bb","lambda").replace("\u039b","lambda") for c in fc]
            for a in ASSETS:
                cd=cr.get(a,{}); sm=np.array(cd.get("scaler_mean",[]),dtype=float); ss=np.array(cd.get("scaler_std",[]),dtype=float)
                if len(sm)!=20: continue
                pd=ROOT/"data_lake"/a/"ohlcv"/"aggregated"
                ps=sorted(pd.glob("*.parquet")) if pd.exists() else []
                if not ps: continue
                try:
                    df=pl.read_parquet(ps[-1])
                    cols=[c for c in fc_n if c in df.columns]
                    if len(cols)<15: check("scaler_drift."+a,False,f"only {len(cols)}/20 cols"); continue
                    df_w=(df.filter(pl.col("close").is_not_null()&~pl.col("close").is_nan())
                          .select(cols[:20]).tail(63))
                    X=np.nan_to_num(df_w.to_numpy().astype(float),nan=0.0,posinf=0.0,neginf=0.0)
                    n=min(len(X[0]),len(sm)); diff=np.nan_to_num((X.mean(axis=0)[:n]-sm[:n])/(ss[:n]+1e-10))
                    mah=float(np.sqrt(np.sum(diff**2)/n))
                    geo_idx=next((i for i,c in enumerate(cols[:n]) if "goldstein" in c),None)
                    geo_z=float(abs(diff[geo_idx])) if geo_idx is not None else 0.0
                    status="ALERT" if mah>=5 else ("WARN" if mah>=3 else "OK")
                    geo_flag=f" | goldstein_geo z={geo_z:.0f} (DATA_QUALITY)" if geo_z>30 else ""
                    check("scaler_drift."+a,status=="OK",
                          f"mahal={mah:.2f}σ [{status}] cols={len(cols)}{geo_flag}",
                          critical=(status=="ALERT"))
                except Exception as e: check("scaler_drift."+a,False,str(e)[:60])
        except Exception as e: check("scaler_drift.compute",False,str(e)[:60])

    # Summary
    print("\n"+SEP)
    nok=len(rep["ok"]); nw=len(rep["warnings"]); nc=len(rep["critical"])
    print("RESULT: "+("SYSTEM OK" if nc==0 else "CRITICAL FAILURES"))
    print(f"  OK={nok}  WARN={nw}  CRIT={nc}")
    if rep["critical"]: print("\n  CRITICAL:"); [print("    [!] "+c) for c in rep["critical"]]
    if rep["warnings"]: print("\n  WARNINGS:"); [print("    [~] "+w) for w in rep["warnings"]]
    if notify and TOKEN and TG_SIST:
        icon="OK" if nc==0 else "CRIT"
        lines=[f"SPEL AUDIT [{session_tag}] {ts}",f"Status: {icon}  OK={nok} WARN={nw} CRIT={nc}"]
        if rep["critical"]: lines.append("CRITICAL: "+", ".join(rep["critical"]))
        tg_send(TG_SIST,"\n".join(lines)); print("  TG SISTEMA: sent")
    rep["passed"]=nc==0; rep["n_ok"]=nok; rep["n_warn"]=nw; rep["n_crit"]=nc
    print(SEP)
    if abort_on_critical and nc>0: raise RuntimeError("AUDIT FAILED: "+str(rep["critical"]))
    return rep
