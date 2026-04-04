# spel_sentinel_core.py - Holmes OS V4.0 - Reconstructed S41
# R37-CLEAN: NO torch top-level. Hinc Omnia Cerno
import os, sys, json, hashlib, time, re, logging
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("spel.sentinel")
SENTINEL_VERSION = "V4.0-S41"
PATROL_INTERVAL  = int(os.environ.get("PATROL_INTERVAL", "900"))
BASE_DIR         = Path(os.environ.get("SPEL_BASE_DIR", "."))

@dataclass
class SentinelStatus:
    timestamp_utc: str; is_healthy: bool; r37_clean: bool
    sha_ok: bool; checkpoint_ok: bool; anomalies: List[str] = field(default_factory=list)
    adapter_status: str = "UNKNOWN"; paper_day: int = 0
    correlation_id: str = ""
    def to_dict(self):
        return {
            "correlation_id": self.correlation_id or hashlib.sha256(self.timestamp_utc.encode()).hexdigest()[:12],
            "decision": "AUDIT_PASS" if self.is_healthy else "AUDIT_FAIL",
            "adapter_status": self.adapter_status, "paper_day": self.paper_day,
            "anomalies": self.anomalies, "timestamp_utc": self.timestamp_utc,
            "session": os.environ.get("HOLMES_SESSION","?"), "_version": SENTINEL_VERSION,
        }

def check_r37(base):
    _re = __import__("re")
    _PAT = _re.compile(r"^\s{0}(import torch|from torch)", _re.MULTILINE)
    v = []
    for _g in ["scripts/*.py","codigo/core/*.py"]:
        for _f in sorted(base.glob(_g)):
            try:
                hits = _PAT.findall(_f.read_text(encoding="utf-8",errors="replace"))
                if hits: v.append(f"{_f.name}:{len(hits)}")
            except Exception: pass
    return not v, v

def check_sha(base):
    p = base/"meta/SHA_REGISTRY.json"
    if not p.exists(): return False, "MISSING"
    if p.stat().st_size < 50: return False, f"EMPTY_{p.stat().st_size}B"
    try: json.loads(p.read_text()); return True, "OK"
    except Exception as e: return False, str(e)

def check_checkpoints(base):
    CKPT = {"BTC":"BTC_LSTM_v3c_F1_0.4994.pt","XAU":"XAU_LSTM_v3_godel_valloss0.4386.pt",
            "NIFTY50":"NIFTY50_LSTM_v3_godel_valloss0.3784.pt","NVDA":"NVDA_LSTM_v3_godel_valloss0.3857.pt"}
    missing=[]
    for a,f in CKPT.items():
        p=base/"checkpoints"/f
        if not p.exists():
            if a=="BTC" and (base/"checkpoints/BTC_LSTM_v3c_F1_0.4821.pt").exists(): continue
            missing.append(f"{a}:{f}")
    return not missing, missing

def run_patrol(base=BASE_DIR):
    ts=datetime.now(timezone.utc).isoformat(); anomalies=[]
    r37_ok,r37v=check_r37(base)
    for v in r37v[:5]: anomalies.append(f"R37:{v}")
    sha_ok,sha_msg=check_sha(base)
    if not sha_ok: anomalies.append(f"SHA_REGISTRY:{sha_msg}")
    ck_ok,ck_miss=check_checkpoints(base)
    for m in ck_miss: anomalies.append(f"CKPT_MISSING:{m}")
    return SentinelStatus(ts,r37_ok and sha_ok,r37_ok,sha_ok,ck_ok,anomalies,
                          "OK" if (r37_ok and sha_ok) else "DEGRADED",
                          correlation_id=hashlib.sha256(ts.encode()).hexdigest()[:12])

def write_signal(base,status):
    vp=base/"Holmes/vault/signal_packet_latest.json"
    vp.parent.mkdir(parents=True,exist_ok=True)
    vp.write_text(json.dumps(status.to_dict(),indent=2))
    with open(vp,"r+b") as fh: os.fsync(fh.fileno())
    return vp

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(name)s %(levelname)s %(message)s")
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("--patrol",action="store_true")
    p.add_argument("--continuous",action="store_true"); args=p.parse_args()
    if args.patrol or args.continuous:
        while True:
            s=run_patrol(); vp=write_signal(BASE_DIR,s)
            logger.info(f"Patrol: {s.to_dict()['decision']} anomalies={len(s.anomalies)}")
            if not args.continuous: break
            time.sleep(PATROL_INTERVAL)
    else:
        s=run_patrol(); print(json.dumps(s.to_dict(),indent=2))
        sys.exit(0 if s.is_healthy else 1)
