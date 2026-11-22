"""Configuration loader for the Amazon Haul Price Monitor.

The Haul monitor is a separate service from the normal Amazon Price Error
Monitor. Keepa does NOT expose Haul prices, so the Haul price comes from a
scraper and Keepa is used only for the regular Amazon price history / graph.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Discord tiers: Haul-80 / Haul-70 / Haul-60 / Haul-50 (highest first).
DISCORD_TIERS = (80, 70, 60, 50)

load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(float(_get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return _get(name, str(default)).lower() in ("1", "true", "yes", "y", "on")


def _get_list_int(name: str) -> list[int]:
    out: list[int] = []
    for part in _get(name).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _get_list_str(name: str) -> list[str]:
    raw = _get(name)
    return [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]


# Persist DB / UI settings here. On Render set DATA_DIR=/var/data (disk mount).
_data_raw = _get("DATA_DIR")
DATA_DIR = Path(_data_raw) if _data_raw else (BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Keepa domain id -> country code used by the public price-history graph.
DOMAIN_CODE = {
    1: "com", 2: "co.uk", 3: "de", 4: "fr", 5: "co.jp",
    6: "ca", 8: "it", 9: "es", 10: "in", 11: "com.mx",
}
DOMAIN_HOST = {
    1: "www.amazon.com", 2: "www.amazon.co.uk", 3: "www.amazon.de",
    4: "www.amazon.fr", 5: "www.amazon.co.jp", 6: "www.amazon.ca",
    8: "www.amazon.it", 9: "www.amazon.es", 10: "www.amazon.in",
    11: "www.amazon.com.mx",
}

# US Amazon seller id used only for the regular-product fallback label.
AMAZON_SELLER_IDS = {
    1: "ATVPDKIKX0DER", 2: "A3P5ROKL5A1OLE", 3: "A3JWKAKR8XB7XF",
    6: "A3DWYIK6Y9EEQB", 11: "AVDBXBAVVSXLQ",
}


@dataclass
class Settings:
    # --- Keepa -------------------------------------------------
    keepa_api_key: str = ""
    keepa_domain: int = 1
    price_type: int = 1          # 1 = Marketplace New
    reference_days: int = 90     # Keepa avg90, matching the screenshots
    keepa_batch_size: int = 40
    max_new_keepa_lookups: int = 40   # per scan cycle (Starter API friendly)
    keepa_cache_days: int = 7

    # --- Discord ----------------------------------------------
    webhooks: dict[int, str] = field(default_factory=dict)
    pings: dict[int, str] = field(default_factory=dict)
    seller_label: str = "Haul"
    channel_prefix: str = "Haul"

    # --- Scan loop --------------------------------------------
    poll_interval_sec: int = 600
    min_discount: int = 50
    max_discount: int = 95       # 90%+ still routes to the 80 channel
    alert_cooldown_hours: int = 24
    no_repeat_same_day: bool = True
    allow_cheaper_repeat: bool = True
    max_alerts_per_tier: int = 5

    # --- Haul scraper -----------------------------------------
    haul_keywords: list[str] = field(default_factory=list)
    haul_extra_urls: list[str] = field(default_factory=list)
    haul_pages: int = 2
    haul_max_items: int = 1200
    haul_request_delay: float = 1.2
    haul_timeout_sec: int = 45
    haul_retries: int = 2
    proxy_url: str = ""
    scraper_user_agent: str = ""
    verify_haul_stock: bool = False

    # --- Quality / junk filters -------------------------------
    min_original_price: float = 15.0   # regular Amazon reference floor
    min_haul_price: float = 3.0
    max_haul_price: float = 25.0
    min_savings: float = 8.0
    min_tracking_days: int = 60
    require_brand: bool = True
    exclude_generic_brand: bool = True

    min_review_count: int = 0
    min_rating: float = 0.0
    include_categories: list[int] = field(default_factory=list)
    exclude_categories: list[int] = field(default_factory=list)
    include_brands: list[str] = field(default_factory=list)
    exclude_brands: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)

    # Quality gates (mirror the Amazon monitor)
    reject_promotions: bool = False
    reject_business: bool = False
    min_recent_discount: int = 0
    exclude_books: bool = True

    include_graph: bool = True
    log_level: str = "INFO"

    @property
    def tiers(self) -> list[int]:
        """Discount tiers that actually have a webhook configured, high -> low."""
        return sorted((t for t in DISCORD_TIERS if self.webhooks.get(t)), reverse=True)

    @property
    def domain_code(self) -> str:
        return DOMAIN_CODE.get(self.keepa_domain, "com")

    @property
    def domain_host(self) -> str:
        return DOMAIN_HOST.get(self.keepa_domain, "www.amazon.com")

    @property
    def amazon_seller_id(self) -> str:
        return AMAZON_SELLER_IDS.get(self.keepa_domain, "")


def load_settings() -> Settings:
    # Single webhook for all tiers (client consolidated to one channel).
    single_wh = _get("DISCORD_WEBHOOK")
    webhooks = {
        50: _get("DISCORD_WEBHOOK_50") or single_wh,
        60: _get("DISCORD_WEBHOOK_60") or single_wh,
        70: _get("DISCORD_WEBHOOK_70") or single_wh,
        80: _get("DISCORD_WEBHOOK_80") or single_wh,
    }
    channel_on = {
        50: _get_bool("CHANNEL_ENABLED_50", True),
        60: _get_bool("CHANNEL_ENABLED_60", True),
        70: _get_bool("CHANNEL_ENABLED_70", True),
        80: _get_bool("CHANNEL_ENABLED_80", True),
    }
    single_ping = _get("DISCORD_PING")
    pings = {
        50: _get("DISCORD_PING_50") or single_ping,
        60: _get("DISCORD_PING_60") or single_ping,
        70: _get("DISCORD_PING_70") or single_ping,
        80: _get("DISCORD_PING_80") or single_ping,
    }

    keywords = _get_list_str("HAUL_KEYWORDS")
    if not keywords:
        keywords = [
            "automotive", "car accessories", "tools", "home improvement",
            "electronics", "kitchen", "office", "pet supplies",
            "sports and outdoors", "industrial and scientific",
        ]

    s = Settings(
        keepa_api_key=_get("KEEPA_API_KEY"),
        keepa_domain=_get_int("KEEPA_DOMAIN", 1),
        price_type=_get_int("PRICE_TYPE", 1),
        reference_days=_get_int("REFERENCE_DAYS", 90),
        keepa_batch_size=max(1, min(100, _get_int("KEEPA_BATCH_SIZE", 40))),
        max_new_keepa_lookups=max(1, min(100, _get_int("MAX_NEW_KEEPA_LOOKUPS", 40))),
        keepa_cache_days=max(1, _get_int("KEEPA_CACHE_DAYS", 7)),

        webhooks={k: v for k, v in webhooks.items() if v and channel_on.get(k, True)},
        pings={k: v for k, v in pings.items() if v},
        seller_label=_get("SELLER_LABEL", "Haul") or "Haul",
        channel_prefix=_get("CHANNEL_PREFIX", "Haul") or "Haul",

        poll_interval_sec=_get_int("POLL_INTERVAL_SEC", 600),
        min_discount=_get_int("MIN_DISCOUNT", 50),
        max_discount=_get_int("MAX_DISCOUNT", 95),
        alert_cooldown_hours=_get_int("ALERT_COOLDOWN_HOURS", 24),
        no_repeat_same_day=_get_bool("NO_REPEAT_SAME_DAY", True),
        allow_cheaper_repeat=_get_bool("ALLOW_CHEAPER_REPEAT", True),
        max_alerts_per_tier=max(1, min(20, _get_int("MAX_ALERTS_PER_TIER", 5))),

        haul_keywords=keywords,
        haul_extra_urls=[u.strip() for u in _get("HAUL_EXTRA_URLS").replace(";", ",").split(",") if u.strip()],
        haul_pages=max(1, min(10, _get_int("HAUL_PAGES", 2))),
        haul_max_items=max(50, _get_int("HAUL_MAX_ITEMS", 1200)),
        haul_request_delay=max(0.2, _get_float("HAUL_REQUEST_DELAY", 1.2)),
        haul_timeout_sec=max(10, _get_int("HAUL_TIMEOUT_SEC", 45)),
        haul_retries=max(0, min(5, _get_int("HAUL_RETRIES", 2))),
        proxy_url=_get("PROXY_URL"),
        scraper_user_agent=_get("SCRAPER_USER_AGENT"),
        verify_haul_stock=_get_bool("VERIFY_HAUL_STOCK", False),

        min_original_price=_get_float("MIN_ORIGINAL_PRICE", 15.0),
        min_haul_price=_get_float("MIN_HAUL_PRICE", 3.0),
        max_haul_price=_get_float("MAX_HAUL_PRICE", 25.0),
        min_savings=_get_float("MIN_SAVINGS", 8.0),
        min_tracking_days=_get_int("MIN_TRACKING_DAYS", 60),
        require_brand=_get_bool("REQUIRE_BRAND", True),
        exclude_generic_brand=_get_bool("EXCLUDE_GENERIC_BRAND", True),

        min_review_count=_get_int("MIN_REVIEW_COUNT", 0),
        min_rating=_get_float("MIN_RATING", 0.0),
        include_categories=_get_list_int("INCLUDE_CATEGORIES"),
        exclude_categories=_get_list_int("EXCLUDE_CATEGORIES"),
        include_brands=_get_list_str("INCLUDE_BRANDS"),
        exclude_brands=_get_list_str("EXCLUDE_BRANDS"),
        title_keywords=_get_list_str("TITLE_KEYWORDS"),
        exclude_keywords=_get_list_str("EXCLUDE_KEYWORDS"),

        reject_promotions=_get_bool("REJECT_PROMOTIONS", False),
        reject_business=_get_bool("REJECT_BUSINESS", False),
        min_recent_discount=_get_int("MIN_RECENT_DISCOUNT", 0),
        exclude_books=_get_bool("EXCLUDE_BOOKS", True),

        include_graph=_get_bool("INCLUDE_GRAPH", True),
        log_level=_get("LOG_LEVEL", "INFO").upper() or "INFO",
    )

    # Optional UI overlay (data/ui_settings.json).
    from ui_settings import apply_ui_to_settings
    apply_ui_to_settings(s)
    return s
