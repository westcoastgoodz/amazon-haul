"""UI / runtime settings overlay (data/ui_settings.json) on top of .env."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config import DATA_DIR, DISCORD_TIERS

DEFAULTS: dict[str, Any] = {
    "scan_interval_min": 10,
    "max_alerts_per_tier": 5,
    "min_discount": 50,
    "min_original_price": 15.0,
    "min_savings": 8.0,
    "min_haul_price": 3.0,
    "haul_keywords": "",
    "watchlist_asins": "",
    "include_categories": "",
    "exclude_categories": "",
    "include_brands": "",
    "exclude_brands": "",
    "title_keywords": "",
    "exclude_keywords": "",
    "min_review_count": 0,
    "min_rating": 0.0,
    "reject_promotions": False,
    "reject_business": False,
    "exclude_books": True,
    "_rev": 1,
    "keepa_api_key": "",
    "discord_webhook_50": "",
    "discord_webhook_60": "",
    "discord_webhook_70": "",
    "discord_webhook_80": "",
    "channel_enabled_50": True,
    "channel_enabled_60": True,
    "channel_enabled_70": True,
    "channel_enabled_80": True,
}

_SECRET_KEYS = (
    "keepa_api_key",
    "discord_webhook_50",
    "discord_webhook_60",
    "discord_webhook_70",
    "discord_webhook_80",
)


def _settings_path() -> Path:
    return DATA_DIR / "ui_settings.json"


def _env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _persist(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_ui_settings() -> dict[str, Any]:
    data = dict(DEFAULTS)
    path = _settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw:
                        data[k] = raw[k]
        except (OSError, json.JSONDecodeError):
            pass

    data["scan_interval_min"] = max(5, min(120, int(data["scan_interval_min"])))
    data["max_alerts_per_tier"] = max(1, min(20, int(data["max_alerts_per_tier"])))
    data["min_discount"] = max(40, min(95, int(data["min_discount"])))
    data["min_original_price"] = max(0.0, float(data["min_original_price"]))
    data["min_savings"] = max(0.0, float(data["min_savings"]))
    data["min_haul_price"] = max(0.0, float(data["min_haul_price"]))

    data["_rev"] = 1
    for k in _SECRET_KEYS:
        data[k] = str(data.get(k) or "").strip()
    for tier in DISCORD_TIERS:
        ck = f"channel_enabled_{tier}"
        data[ck] = bool(data.get(ck, True))

    if path.is_file():
        try:
            _persist(data)
        except OSError:
            pass
    return data


def save_ui_settings(updates: dict[str, Any]) -> dict[str, Any]:
    data = load_ui_settings()
    for k, v in updates.items():
        if k in DEFAULTS:
            data[k] = v
    data["scan_interval_min"] = max(5, min(120, int(data.get("scan_interval_min") or 10)))
    data["max_alerts_per_tier"] = max(1, min(20, int(data.get("max_alerts_per_tier") or 5)))
    data["_rev"] = 1
    _persist(data)
    return load_ui_settings()


def apply_ui_to_settings(settings) -> None:
    """Mutate Settings with UI overlay when ui_settings.json exists.

    Render env wins for scan speed / alert volume when set:
    POLL_INTERVAL_SEC, MAX_ALERTS_PER_TIER.
    """
    path = _settings_path()
    if not path.is_file():
        return

    ui = load_ui_settings()

    env_poll = _env_int("POLL_INTERVAL_SEC")
    if env_poll is not None and env_poll >= 60:
        settings.poll_interval_sec = env_poll
    else:
        settings.poll_interval_sec = int(ui["scan_interval_min"]) * 60

    env_max = _env_int("MAX_ALERTS_PER_TIER")
    if env_max is not None:
        settings.max_alerts_per_tier = max(1, min(20, env_max))
    else:
        settings.max_alerts_per_tier = int(ui["max_alerts_per_tier"])

    settings.min_discount = int(ui["min_discount"])
    settings.min_original_price = float(ui["min_original_price"])
    settings.min_savings = float(ui["min_savings"])
    settings.min_haul_price = float(ui["min_haul_price"])
    keywords = str(ui.get("haul_keywords") or "").strip()
    if keywords:
        settings.haul_keywords = [k.strip().lower() for k in keywords.replace(";", ",").split(",") if k.strip()]

    def _list_int(v):
        out = []
        for part in str(v or "").replace(";", ",").split(","):
            part = part.strip()
            if part:
                try:
                    out.append(int(part))
                except ValueError:
                    pass
        return out

    def _list_str(v):
        return [p.strip().lower() for p in str(v or "").replace(";", ",").split(",") if p.strip()]

    ic = str(ui.get("include_categories") or "").strip()
    if ic:
        settings.include_categories = _list_int(ic)
    ec = str(ui.get("exclude_categories") or "").strip()
    if ec:
        settings.exclude_categories = _list_int(ec)
    ib = str(ui.get("include_brands") or "").strip()
    if ib:
        settings.include_brands = _list_str(ib)
    eb = str(ui.get("exclude_brands") or "").strip()
    if eb:
        settings.exclude_brands = _list_str(eb)
    tk = str(ui.get("title_keywords") or "").strip()
    if tk:
        settings.title_keywords = _list_str(tk)
    ek = str(ui.get("exclude_keywords") or "").strip()
    if ek:
        settings.exclude_keywords = _list_str(ek)
    settings.min_review_count = int(ui.get("min_review_count") or 0)
    settings.min_rating = float(ui.get("min_rating") or 0.0)
    settings.reject_promotions = bool(ui.get("reject_promotions", False))
    settings.reject_business = bool(ui.get("reject_business", False))
    settings.exclude_books = bool(ui.get("exclude_books", True))

    wl = str(ui.get("watchlist_asins") or "").strip()
    if wl:
        settings.watchlist_asins = [
            a.strip().upper() for a in wl.replace(";", ",").replace("\n", ",").split(",")
            if a.strip()
        ]

    key = str(ui.get("keepa_api_key") or "").strip()
    if key:
        settings.keepa_api_key = key

    webhooks = dict(settings.webhooks or {})
    for tier in DISCORD_TIERS:
        wh = str(ui.get(f"discord_webhook_{tier}") or "").strip()
        if wh:
            webhooks[tier] = wh
    settings.webhooks = {
        k: v
        for k, v in webhooks.items()
        if v and bool(ui.get(f"channel_enabled_{k}", True))
    }
    settings.channel_enabled = {  # type: ignore[attr-defined]
        t: bool(ui.get(f"channel_enabled_{t}", True)) for t in DISCORD_TIERS
    }
