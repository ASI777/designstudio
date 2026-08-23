"""Public-web datasheet discovery fallback; results remain untrusted until PDF evidence validates."""
from __future__ import annotations

import html
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .base import normalize_mpn


_DISTRIBUTOR_HOSTS = (
    "digikey.", "mouser.", "arrow.", "farnell.", "element14.",
    "newark.", "rs-online.", "tme.", "futureelectronics.",
)
_MANUFACTURER_NOISE = {
    "inc", "ltd", "llc", "corp", "corporation", "company", "co",
    "semiconductor", "semiconductors", "electronics", "technology", "technologies",
}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self._href = None; self._text = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href"); self._text = []
    def handle_data(self, data):
        if self._href: self._text.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def _source_tier(hostname: str, title: str, manufacturer: str) -> str:
    host = hostname.casefold()
    if any(token in host for token in _DISTRIBUTOR_HOSTS):
        return "distributor-mirror"
    manufacturer_tokens = [token for token in ''.join(
        char if char.isalnum() else ' ' for char in manufacturer.casefold()).split()
        if len(token) >= 3 and token not in _MANUFACTURER_NOISE]
    searchable = normalize_mpn(host + " " + title)
    if manufacturer_tokens and any(normalize_mpn(token) in searchable
                                   for token in manufacturer_tokens):
        return "manufacturer-likely"
    return "public-web"


def parse_search_html(body: str, mpn: str, limit: int = 5,
                      manufacturer: str = "") -> list[dict]:
    parser = _Links(); parser.feed(body)
    key = normalize_mpn(mpn); results = []; seen = set()
    for href, title in parser.links:
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com"):
            href = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            parsed = urllib.parse.urlparse(href)
        combined = normalize_mpn(html.unescape(title) + " " + href)
        low = href.lower()
        if parsed.scheme != "https" or key not in combined:
            continue
        if not (low.endswith(".pdf") or "datasheet" in low or "/document" in low):
            continue
        if href in seen: continue
        seen.add(href)
        decoded_title = html.unescape(title)
        results.append({"url": href, "title": decoded_title,
                        "source": "public-web-search",
                        "source_tier": _source_tier(parsed.hostname or "", decoded_title,
                                                    manufacturer),
                        "trusted": False})
    priority = {"manufacturer-likely": 0, "distributor-mirror": 1, "public-web": 2}
    return sorted(results, key=lambda item: (priority[item["source_tier"]], item["url"]))[:limit]


def search_datasheets(mpn: str, limit: int = 5, opener=urllib.request.urlopen,
                      manufacturer: str = "") -> list[dict]:
    terms = f'"{mpn}" datasheet filetype:pdf'
    if manufacturer.strip():
        terms = f'"{mpn}" "{manufacturer.strip()}" datasheet filetype:pdf'
    query = urllib.parse.urlencode({"q": terms})
    request = urllib.request.Request("https://html.duckduckgo.com/html/?" + query,
                                     headers={"User-Agent": "DesignStudio/1.0"})
    with opener(request, timeout=15) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        return []
    return parse_search_html(body.decode("utf-8", "replace"), mpn, limit,
                             manufacturer=manufacturer)
