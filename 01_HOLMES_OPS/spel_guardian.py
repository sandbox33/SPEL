import json, hashlib, shutil
from pathlib import Path
from datetime import datetime, timezone

CANONICAL_P90 = {"BTC":2.002221,"XAU":1.904465,"NIFTY50":1.186823,"NVDA":1.900615}
CANONICAL_CKPT = {
    "BTC":     "BTC_LSTM_v3_S43_clean.pt",
    "XAU":     "XAU_LSTM_v3_S43_clean.pt",
    "NIFTY50": "NIFTY50_LSTM_v3_S43_clean.pt",
    "NVDA":    "NVDA_LSTM_v3_S43_clean.pt",
}
CORE_ASSETS = ("BTC","XAU","NIFTY50","NVDA")

class Guardian:
    def __init__(self, root):
        self.root     = Path(root)
        self.sha_path = self.root / "meta/SHA_REGISTRY.json"
        self.vault    = self.root / "Holmes/vault"
        self.ckpt_dir = self.root / "checkpoints"
        self.archive  = self.root / "_ARCHIVE_S41"
        self.qlog     = self.vault / "quarantine_log.json"

    def read_sha(self):
        raw = json.loads(self.sha_path.read_text())
        out = {}
        for a in CORE_ASSETS:
            val = raw.get(a)
            ab  = raw.get("assets",{}).get(a,{}) if isinstance(raw.get("assets"),dict) else {}
            if isinstance(val, dict) and "sha_v5" in val:
                out[a] = val
            elif isinstance(val, str):
                out[a] = {"sha_v5":val,"p90_entropy":CANONICAL_P90[a],"checkpoint_file":CANONICAL_CKPT[a]}
            else:
                out[a] = {"sha_v5":ab.get("sha256","UNKNOWN"),"p90_entropy":CANONICAL_P90[a],"checkpoint_file":CANONICAL_CKPT[a]}
        return out

    def write_sha(self, data, reason="guardian_write"):
        payload = json.dumps(data, indent=2, sort_keys=True).encode()
        tmp = self.sha_path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        tmp.replace(self.sha_path)
        self.sync_vault()
        return hashlib.sha256(payload).hexdigest()[:12]

    def sync_vault(self):
        self.vault.mkdir(parents=True, exist_ok=True)
        for fname in ("SHA_REGISTRY.json","SPEL_META.json"):
            src = self.root / "meta" / fname
            if src.exists(): shutil.copy2(src, self.vault / fname)

    def health_check(self):
        sha = self.read_sha()
        report = {"ts": datetime.now(timezone.utc).isoformat(), "assets": {}}
        for a in CORE_ASSETS:
            d  = sha.get(a, {})
            cp = self.ckpt_dir / d.get("checkpoint_file", CANONICAL_CKPT[a])
            ok = cp.exists() and cp.stat().st_size > 50_000
            report["assets"][a] = {
                "sha_v5":  d.get("sha_v5","MISSING")[:12],
                "p90":     d.get("p90_entropy", CANONICAL_P90[a]),
                "ckpt_ok": ok,
                "ckpt_kb": round(cp.stat().st_size/1024,1) if ok else 0,
            }
        report["status"] = "HEALTHY" if all(v["ckpt_ok"] for v in report["assets"].values()) else "DEGRADED"
        return report

    def purge_legacy_pt(self):
        import torch
        self.archive.mkdir(parents=True, exist_ok=True)
        qlog = json.loads(self.qlog.read_text()) if self.qlog.exists() else []
        purged = []
        for pt in self.ckpt_dir.glob("*.pt"):
            try:
                torch.load(str(pt), map_location="cpu", weights_only=True)
            except Exception as e:
                if "StandardScaler" in str(e) or "Unsupported global" in str(e) or "weights_only" in str(e):
                    dest = self.archive / (pt.stem + ".LEGACY_SCALER.pt")
                    shutil.move(str(pt), str(dest))
                    purged.append(pt.name)
                    qlog.append({"ts":datetime.now(timezone.utc).isoformat(),
                                 "action":"PURGE_LEGACY_PT","original":str(pt),"dest":str(dest)})
        self.qlog.parent.mkdir(parents=True, exist_ok=True)
        self.qlog.write_text(json.dumps(qlog, indent=2))
        return purged

    @staticmethod
    def copy_tree(src, dest_root):
        src, dest_root = Path(src), Path(dest_root)
        dest_base = dest_root / src.name
        stats = {"copied":0,"skipped":0}
        for f in src.rglob("*"):
            if not f.is_file(): continue
            rel  = f.relative_to(src)
            dest = dest_base / rel
            if dest.exists():
                h = hashlib.sha256
                if h(f.read_bytes()).hexdigest() == h(dest.read_bytes()).hexdigest():
                    stats["skipped"] += 1; continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(dest))
            stats["copied"] += 1
        return stats