"""
Saves and loads redaction profiles as JSON files in the profiles/ directory.
Each profile stores: enabled patterns, custom keywords, replacement text, case flag.
"""

import json
from pathlib import Path
from datetime import datetime

PROFILES_DIR = Path(__file__).parent.parent / "profiles"


class ProfileManager:
    def __init__(self):
        PROFILES_DIR.mkdir(exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self, name: str, data: dict) -> bool:
        try:
            payload = {
                "name": name,
                "created": datetime.now().isoformat(timespec="seconds"),
                "data": data,
            }
            with open(self._path(name), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load(self, name: str) -> dict | None:
        p = self._path(name)
        if not p.exists():
            # Try to find by display name (case-insensitive)
            for f in PROFILES_DIR.glob("*.json"):
                try:
                    with open(f, encoding="utf-8") as fh:
                        d = json.load(fh)
                    if d.get("name", "").lower() == name.lower():
                        return d["data"]
                except Exception:
                    pass
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)["data"]

    def list_profiles(self) -> list:
        profiles = []
        for p in PROFILES_DIR.glob("*.json"):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                profiles.append({
                    "name": d.get("name", p.stem),
                    "created": d.get("created", ""),
                    "keywords": len(d.get("data", {}).get("keywords", [])),
                })
            except Exception:
                pass
        return sorted(profiles, key=lambda x: x["created"], reverse=True)

    def delete(self, name: str) -> bool:
        p = self._path(name)
        if p.exists():
            p.unlink()
            return True
        # Fallback: search by display name
        for f in PROFILES_DIR.glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
                if d.get("name", "").lower() == name.lower():
                    f.unlink()
                    return True
            except Exception:
                pass
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sanitize(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)

    def _path(self, name: str) -> Path:
        return PROFILES_DIR / f"{self._sanitize(name)}.json"
