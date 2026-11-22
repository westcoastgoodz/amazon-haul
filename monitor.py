"""Main monitoring loop for the Haul monitor: scrape Haul → Keepa baseline → alerts."""
from __future__ import annotations

import logging
import random
import time

from config import DATA_DIR, Settings
from discord_notifier import send_alert
from filters import passes_filters, passes_price_error_quality, score, tier_for_discount
from haul_scraper import HaulBlocked, HaulScraper
from keepa_client import KeepaClient, KeepaError, compact_product
from models import HaulListing, AlertItem, build_haul_alert
from storage import Storage
from ui_settings import apply_ui_to_settings

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, settings: Settings):
        apply_ui_to_settings(settings)
        self.s = settings
        self.keepa = KeepaClient(settings.keepa_api_key, settings.keepa_domain)
        self.store = Storage(DATA_DIR / "monitor.db")
        self.scraper = HaulScraper(settings)
        self._stop = False

    def reload_settings(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self.s = settings
        apply_ui_to_settings(self.s)
        # Rebuild scraper only if keywords/proxy changed — cheap enough to keep.
        self.scraper = HaulScraper(self.s)

    def request_stop(self) -> None:
        self._stop = True

    @property
    def stopping(self) -> bool:
        return bool(self._stop)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end and not self._stop:
            time.sleep(min(0.5, end - time.time()))

    def _prefilter(self, listing: HaulListing) -> bool:
        if not listing.asin or listing.price is None or listing.price <= 0:
            return False
        if listing.available is False:
            logger.debug("Skip %s: unavailable on Haul (tile)", listing.asin)
            return False
        if getattr(self.s, "verify_haul_stock", False) and listing.available is None:
            from haul_scraper import check_haul_stock

            stock = check_haul_stock(self.scraper, listing.asin, listing.url)
            if stock is False:
                logger.debug("Skip %s: Haul stock check failed", listing.asin)
                return False
            if stock is True:
                listing.available = True
        if not self.store.should_alert(
            listing.asin,
            listing.price,
            cooldown_hours=self.s.alert_cooldown_hours,
            no_repeat_same_day=bool(getattr(self.s, "no_repeat_same_day", True)),
            allow_if_cheaper=bool(getattr(self.s, "allow_cheaper_repeat", True)),
        ):
            return False
        return True

    def _enrich_batch(self, asins: list[str]) -> dict[str, dict]:
        """Return {asin: compact product}, using cache first."""
        result: dict[str, dict] = {}
        missing: list[str] = []
        for asin in asins:
            cached = self.store.get_cached_product(asin, max_age_days=self.s.keepa_cache_days)
            if cached is not None:
                result[asin] = cached
            else:
                missing.append(asin)

        # Budget new Keepa lookups.
        budget = self.s.max_new_keepa_lookups
        to_fetch = missing[:budget]
        if missing and len(missing) > budget:
            logger.info(
                "Keepa budget %d/%d — deferring %d ASINs to next cycle",
                budget, len(missing), len(missing) - budget,
            )

        for i in range(0, len(to_fetch), self.s.keepa_batch_size):
            if self._stop:
                break
            chunk = to_fetch[i : i + self.s.keepa_batch_size]
            try:
                products = self.keepa.get_products(chunk, stats_days=self.s.reference_days)
            except KeepaError as exc:
                logger.warning("Keepa product fetch failed: %s", exc)
                continue
            for p in products or []:
                asin = (p.get("asin") or "").strip().upper()
                if not asin:
                    continue
                compact = compact_product(p)
                self.store.set_cached_product(asin, compact)
                result[asin] = compact
            if i + self.s.keepa_batch_size < len(to_fetch) and not self._stop:
                self._sleep(0.6)

        return result

    def run_once(self, listings: list[HaulListing] | None = None, force: bool = False) -> int:
        self.reload_settings()
        alerted = 0
        sent_asins: set[str] = set()

        if listings is None:
            try:
                listings, stats = self.scraper.fetch_all()
            except HaulBlocked as exc:
                logger.error("Haul scan blocked: %s", exc)
                return 0
            except Exception as exc:  # noqa: BLE001
                logger.exception("Haul scan failed: %s", exc)
                return 0
            logger.info(
                "Haul scan: %d unique listings (priced=%d, pages=%d/%d, blocked=%d, missing_price=%d)",
                stats.get("unique", 0),
                stats.get("priced", 0),
                stats.get("pages_ok", 0),
                stats.get("pages_requested", 0),
                stats.get("pages_blocked", 0),
                stats.get("pages_missing_price", 0),
            )

        if self._stop:
            return 0

        # Pre-filter by price + dedupe (unless forced).
        candidates: list[HaulListing] = []
        for lst in listings or []:
            if self._stop:
                break
            if not lst.asin or lst.price is None:
                continue
            if lst.available is False:
                logger.debug("Skip %s: unavailable on Haul", lst.asin)
                continue
            if force:
                candidates.append(lst)
                continue
            # Lightweight title blocklist before Keepa (we don't have an item yet).
            # We don't have an item yet, but we can skip obvious junk by title.
            title_l = (lst.title or "").lower()
            if any(b in title_l for b in ("phone case", "screen protector", "keychain", "sticker")):
                continue
            if not self._prefilter(lst):
                continue
            candidates.append(lst)

        # Sort by Haul price desc so higher-value items get Keepa lookups first.
        candidates.sort(key=lambda x: x.price or 0, reverse=True)

        # Enrich.
        asins = [c.asin for c in candidates]
        products = self._enrich_batch(asins)
        if self._stop:
            return 0

        # Build alerts.
        built: list[AlertItem] = []
        for lst in candidates:
            if self._stop:
                break
            product = products.get(lst.asin)
            item = build_haul_alert(lst, product, self.s)
            if item is None:
                continue
            ok, _reason = passes_filters(item, self.s)
            if not ok:
                logger.debug("Skip %s: %s", lst.asin, _reason)
                continue
            ok, _reason = passes_price_error_quality(item, product, self.s)
            if not ok:
                logger.debug("Skip %s (quality): %s", lst.asin, _reason)
                continue
            built.append(item)

        # Group by tier.
        by_tier: dict[int, list[AlertItem]] = {}
        for item in built:
            t = tier_for_discount(item.discount, self.s)
            if t is None:
                continue
            by_tier.setdefault(t, []).append(item)

        max_per = self.s.max_alerts_per_tier
        for tier in sorted(by_tier, reverse=True):
            if self._stop:
                break
            webhook = self.s.webhooks.get(tier)
            if not webhook:
                continue
            ranked = sorted(by_tier[tier], key=score, reverse=True)
            pool = ranked[:10]
            random.shuffle(pool)
            ranked = pool
            picked = 0
            for item in ranked:
                if self._stop:
                    break
                if picked >= max_per:
                    break
                if item.asin in sent_asins:
                    continue
                # Quality gate (no product needed for the extra checks).
                ok, _ = passes_filters(item, self.s)
                if not ok:
                    continue

                ok, err = send_alert(
                    webhook,
                    self.s.pings.get(tier, ""),
                    item,
                    tier,
                )
                if ok:
                    if not force:
                        self.store.record_alert(item.asin, item.new_price, item.discount)
                    sent_asins.add(item.asin)
                    alerted += 1
                    picked += 1
                    logger.info(
                        "Alerted %s (%s%%, save $%.2f) -> %s-%s | seller=%s",
                        item.asin,
                        item.discount,
                        item.savings,
                        self.s.channel_prefix,
                        tier,
                        item.seller,
                    )
                else:
                    logger.warning("Failed %s-%s for %s: %s", self.s.channel_prefix, tier, item.asin, err)

        self.store.prune_history(45)
        self.store.prune_product_cache(self.s.keepa_cache_days * 2)
        return alerted

    def run_forever(self) -> None:
        self.reload_settings()
        logger.info(
            "Haul monitor started. tiers=%s interval=%ss min_discount=%s%% min_original=$%.2f seller=%s",
            self.s.tiers,
            self.s.poll_interval_sec,
            self.s.min_discount,
            self.s.min_original_price,
            self.s.seller_label,
        )
        first = True
        while not self._stop:
            try:
                self.reload_settings()
                if self._stop:
                    break
                n = self.run_once()
                if self._stop:
                    break
                wait = self.s.poll_interval_sec
                if first:
                    logger.info("First scan done (%d alert(s)). Next in %ss.", n, wait)
                    first = False
                else:
                    logger.info("Cycle done. %d alert(s). Next scan in %ss.", n, wait)
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in Haul monitor cycle")
                wait = self.s.poll_interval_sec
            if self._stop:
                break
            cycle_start = time.time()
            wait = self.s.poll_interval_sec
            end = cycle_start + wait
            while time.time() < end and not self._stop:
                self._sleep(min(0.5, end - time.time()))
                self.reload_settings()
                if self.s.poll_interval_sec != wait:
                    wait = self.s.poll_interval_sec
                    end = cycle_start + wait
        logger.info("Haul monitor stopped.")

    def close(self) -> None:
        self._stop = True
        try:
            self.keepa.close()
        except Exception:
            pass
        try:
            self.scraper.close()
        except Exception:
            pass
        try:
            self.store.close()
        except Exception:
            pass
