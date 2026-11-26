"""Amazon Haul scraper.

Haul prices are not in Keepa, so we scan Amazon Haul search/category pages and
read the current Haul price from the result tiles. Keepa is used later only for
the regular Amazon avg90 / graph.
"""
from __future__ import annotations

import logging
import random
import random
import re
import time
from urllib.parse import quote_plus, urlparse, urlencode, parse_qsl, urlunparse

from bs4 import BeautifulSoup

from config import Settings
from models import HaulListing

logger = logging.getLogger(__name__)

HAUL_SEARCH = "https://www.amazon.com/s?i=bazaar&k={kw}"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
_DEFAULT_UA = _USER_AGENTS[0]

_BLOCK_MARKERS = (
    "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "to discuss automated access to amazon data",
    "api-services-support@amazon.com",
    "type the characters you see in this image",
    "sorry, something went wrong",
)

_PRICE_RE = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")
_RATING_RE = re.compile(r"([0-9]+(?:\.[0-9])?)\s+out of\s+5", re.I)
_REVIEWS_RE = re.compile(r"\(([0-9][0-9,]*)\)")
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class HaulBlocked(RuntimeError):
    pass


class HaulScraper:
    def __init__(self, settings: Settings):
        self.s = settings
        self._session = None
        self._use_curl = False
        self._init_session()

    def _init_session(self) -> None:
        try:
            from curl_cffi import requests as curl_requests  # type: ignore

            self._session = curl_requests.Session(impersonate="chrome")
            self._use_curl = True
        except Exception:
            import httpx

            self._session = httpx.Client(
                timeout=self.s.haul_timeout_sec,
                follow_redirects=True,
                headers=self._headers(),
            )
            self._use_curl = False

    def _headers(self) -> dict:
        ua = (self.s.scraper_user_agent or "").strip()
        if not ua:
            ua = random.choice(_USER_AGENTS)
        return {**_HEADERS, "User-Agent": ua}

    def _proxies(self):
        proxy = (self.s.proxy_url or "").strip()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def build_urls(self) -> list[str]:
        urls: list[str] = []
        for kw in self.s.haul_keywords:
            kw = (kw or "").strip()
            if not kw:
                continue
            for page in range(1, self.s.haul_pages + 1):
                u = HAUL_SEARCH.format(kw=quote_plus(kw))
                if page > 1:
                    u += f"&page={page}"
                urls.append(u)
        for node in getattr(self.s, "haul_category_nodes", []) or []:
            try:
                node = int(node)
            except (TypeError, ValueError):
                continue
            for page in range(1, self.s.haul_pages + 1):
                u = f"https://www.amazon.com/s?i=bazaar&rh=n%3A{node}"
                if page > 1:
                    u += f"&page={page}"
                urls.append(u)
        for extra in self.s.haul_extra_urls:
            extra = (extra or "").strip()
            if not extra:
                continue
            for page in range(1, self.s.haul_pages + 1):
                urls.append(_with_page(extra, page))
        # de-dupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def fetch_all(self) -> tuple[list[HaulListing], dict]:
        urls = self.build_urls()
        by_asin: dict[str, HaulListing] = {}
        stats = {
            "pages_requested": len(urls),
            "pages_ok": 0,
            "pages_blocked": 0,
            "pages_error": 0,
            "pages_missing_price": 0,
            "listings": 0,
            "priced": 0,
        }
        for i, url in enumerate(urls):
            if len(by_asin) >= self.s.haul_max_items:
                break
            try:
                html = self.fetch_html(url)
            except HaulBlocked as exc:
                stats["pages_blocked"] += 1
                logger.warning("Haul blocked on page %s: %s", url, exc)
                # Back off once, then continue with the next keyword.
                time.sleep(min(30.0, 3.0 * (stats["pages_blocked"] + 1)))
                continue
            except Exception as exc:  # noqa: BLE001
                stats["pages_error"] += 1
                logger.warning("Haul page failed %s: %s", url, exc)
                continue

            stats["pages_ok"] += 1
            listings = parse_search_html(html, page_url=url)
            if listings and not any(x.price for x in listings):
                stats["pages_missing_price"] += 1
                if stats["pages_missing_price"] == 1:
                    logger.warning(
                        "Haul page returned items but no USD prices. "
                        "Amazon usually hides prices outside the US — Render Oregon "
                        "(US IP) or a US proxy is required."
                    )
            for item in listings:
                item.source = url
                stats["listings"] += 1
                if item.price is not None:
                    stats["priced"] += 1
                prev = by_asin.get(item.asin)
                if prev is None:
                    by_asin[item.asin] = item
                elif item.price is not None and (
                    prev.price is None or item.price < prev.price
                ):
                    by_asin[item.asin] = item

            if i < len(urls) - 1:
                time.sleep(self.s.haul_request_delay + random.uniform(0.0, 0.4))

        # Watchlist: check specific product pages directly (finds items Haul search
        # does not index). These are added even if they were not in search results.
        for asin in getattr(self.s, "watchlist_asins", []) or []:
            url = f"https://www.amazon.com/dp/{asin}?s=bazaar&psc=1"
            try:
                html = self.fetch_html(url)
            except Exception as exc:  # noqa: BLE001
                logger.info("Watchlist page failed %s: %s", asin, str(exc)[:80])
                continue
            item = parse_product_page(html, asin, page_url=url)
            if item is None:
                logger.info("Watchlist %s: could not parse Haul product page", asin)
                continue
            stats["listings"] += 1
            if item.price is not None:
                stats["priced"] += 1
            prev = by_asin.get(item.asin)
            if prev is None or (item.price is not None and (prev.price is None or item.price < prev.price)):
                by_asin[item.asin] = item
            time.sleep(self.s.haul_request_delay + random.uniform(0.0, 0.4))

        out = list(by_asin.values())
        stats["unique"] = len(out)
        if not out and stats["pages_blocked"]:
            raise HaulBlocked("all Haul pages were blocked")
        return out, stats

    # ------------------------------------------------------------------
    def fetch_html(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(self.s.haul_retries + 2):
            try:
                # Refresh session on retry to clear cookies/state
                if attempt > 0:
                    self._init_session()
                    time.sleep(1.0 + attempt)
                if self._use_curl:
                    resp = self._session.get(
                        url,
                        headers=self._headers(),
                        cookies={"lc-main": "en_US", "i18n-prefs": "USD"},
                        proxies=self._proxies(),
                        timeout=self.s.haul_timeout_sec,
                        allow_redirects=True,
                    )
                    status = int(resp.status_code)
                    text = resp.text or ""
                else:
                    resp = self._session.get(
                        url,
                        headers=self._headers(),
                        cookies={"lc-main": "en_US", "i18n-prefs": "USD"},
                        proxies=self._proxies(),
                    )
                    status = int(resp.status_code)
                    text = resp.text or ""
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.s.haul_retries + 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise

            if status in (429, 503):
                last = HaulBlocked(f"HTTP {status}")
                if attempt < self.s.haul_retries + 1:
                    time.sleep(5.0 * (attempt + 1))
                    continue
                raise last

            if status >= 400:
                last = HaulBlocked(f"HTTP {status}")
                if attempt < self.s.haul_retries + 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise last

            low = text.lower()
            if any(m in low for m in _BLOCK_MARKERS):
                # Don't raise immediately - check if page still has listings
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(text, "html.parser")
                if len(soup.select("div[data-asin]")) > 0:
                    # Page has listings despite block marker - probably false positive
                    pass
                else:
                    raise HaulBlocked("captcha / automated-access page")

            if len(text) < 5000:
                last = HaulBlocked(f"short response ({len(text)} bytes)")
                if attempt < self.s.haul_retries + 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise last

            return text

        raise last or HaulBlocked("unknown fetch failure")


def _with_page(url: str, page: int) -> str:
    """Append / replace Amazon page param on a full URL."""
    if page <= 1:
        return url
    try:
        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        q["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(q)))
    except Exception:
        return url + ("&" if "?" in url else "?") + f"page={page}"


def _money(text: str) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return round(v, 2) if 0 < v < 10000 else None


def _tile_price(el) -> float | None:
    # Prefer Amazon's current-price element; skip struck-through list prices.
    for p in el.select("span.a-price"):
        classes = " ".join(p.get("class") or [])
        if "a-text-price" in classes:
            continue
        off = p.select_one("span.a-offscreen")
        if off:
            v = _money(off.get_text(" ", strip=True))
            if v is not None:
                return v
        whole = p.select_one("span.a-price-whole")
        if whole:
            frac = p.select_one("span.a-price-fraction")
            raw = whole.get_text(" ", strip=True).rstrip(".")
            if frac:
                raw += "." + frac.get_text(" ", strip=True)
            v = _money(raw)
            if v is not None:
                return v
    # Fallback: first $ amount in the tile text (Haul shows current price first).
    return _money(el.get_text(" ", strip=True))


_OOS_MARKERS = (
    "currently unavailable",
    "out of stock",
    "temporarily out of stock",
    "unavailable",
    "see all buying options",
)


def _detect_availability(tile_text_l: str) -> bool | None:
    """Best-effort stock check from the search tile text."""
    if not tile_text_l:
        return None
    if any(m in tile_text_l for m in _OOS_MARKERS):
        return False
    m = re.search(r"only\s+(\d+)\s+left", tile_text_l)
    if m:
        try:
            return int(m.group(1)) > 0
        except ValueError:
            return None
    return True


def parse_search_html(html: str, page_url: str = "") -> list[HaulListing]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[HaulListing] = []
    for el in soup.select("div[data-asin]"):
        asin = (el.get("data-asin") or "").strip().upper()
        if not _ASIN_RE.match(asin):
            continue
        comp = (el.get("data-component-type") or "").strip()
        if comp and comp != "s-search-result":
            continue
        img = el.select_one("img.s-image")
        link = el.select_one('a.a-link-normal[href*="/dp/"]') or el.select_one('a[href*="/dp/"]')
        if not img and not link:
            continue

        title = ""
        h2 = el.select_one("h2")
        if h2:
            title = h2.get_text(" ", strip=True)
        if not title and link:
            title = link.get_text(" ", strip=True)
        if not title and img:
            title = (img.get("alt") or "").strip()
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue

        price = _tile_price(el)
        image_url = ""
        if img:
            image_url = (img.get("src") or img.get("data-src") or "").strip()

        rating = None
        rating_el = el.select_one("span.a-icon-alt")
        if rating_el:
            m = _RATING_RE.search(rating_el.get_text(" ", strip=True))
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    rating = None

        review_count = None
        rev_el = el.select_one('a[href*="#customerReviews"] span') or el.select_one("span.a-size-base")
        if rev_el:
            m = _REVIEWS_RE.search(rev_el.get_text(" ", strip=True))
            if m:
                try:
                    review_count = int(m.group(1).replace(",", ""))
                except ValueError:
                    review_count = None

        href = (link.get("href") or "").strip() if link else ""
        if href.startswith("/"):
            href = "https://www.amazon.com" + href
        if not href:
            href = f"https://www.amazon.com/dp/{asin}?s=bazaar&psc=1"

        tile_text_l = el.get_text(" ", strip=True).lower()
        available = _detect_availability(tile_text_l)

        out.append(
            HaulListing(
                asin=asin,
                title=title,
                price=price,
                image_url=image_url,
                rating=rating,
                review_count=review_count,
                url=href,
                source=page_url,
                available=available,
            )
        )
    return out


_OOS_PAGE_MARKERS = (
    "currently unavailable",
    "temporarily out of stock",
)


def check_haul_stock(scraper, asin, url):
    """Fetch the Haul product page and check for an Add-to-Cart button.

    Returns True if in stock, False if unavailable, None on fetch failure.
    """
    try:
        html = scraper.fetch_html(url)
    except Exception:  # noqa: BLE001
        return None
    low = (html or "").lower()
    if any(m in low for m in _OOS_PAGE_MARKERS):
        return False
    if "add to cart" in low or "add-to-cart" in low:
        return True
    return None


def parse_product_page(html: str, asin: str, page_url: str = ""):
    """Parse a Haul product page into a HaulListing (for the watchlist)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    el = soup.select_one("#productTitle") or soup.select_one("#title") or soup.select_one("h1")
    if el:
        title = el.get_text(" ", strip=True)
    if not title:
        og = soup.select_one('meta[property="og:title"]')
        if og:
            title = (og.get("content") or "").strip()

    price = None
    for p in soup.select("#corePrice_feature_div span.a-price, #corePriceDisplay_desktop_feature_div span.a-price, span.a-price"):
        classes = " ".join(p.get("class") or [])
        if "a-text-price" in classes:
            continue
        off = p.select_one("span.a-offscreen")
        if off:
            price = _money(off.get_text(" ", strip=True))
            if price is not None:
                break
    if price is None:
        price = _money(soup.get_text(" ", strip=True))

    image_url = ""
    img = soup.select_one("#landingImage") or soup.select_one("img#main-image") or soup.select_one("img.a-dynamic-image")
    if img:
        image_url = (img.get("src") or img.get("data-old-hires") or "").strip()

    rating = None
    r = soup.select_one("#acrPopover span.a-icon-alt") or soup.select_one("span.a-icon-alt")
    if r:
        m = _RATING_RE.search(r.get_text(" ", strip=True))
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass

    reviews = None
    rv = soup.select_one("#acrCustomerReviewText")
    if rv:
        m = _REVIEWS_RE.search(rv.get_text(" ", strip=True))
        if m:
            try:
                reviews = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

    low = html.lower()
    available = None
    if "currently unavailable" in low or "temporarily out of stock" in low:
        available = False
    elif "add to cart" in low or "add-to-cart" in low:
        available = True

    if not title and price is None:
        return None

    return HaulListing(
        asin=asin,
        title=title or asin,
        price=price,
        image_url=image_url,
        rating=rating,
        review_count=reviews,
        url=page_url or f"https://www.amazon.com/dp/{asin}?s=bazaar&psc=1",
        source=page_url,
        available=available,
    )
