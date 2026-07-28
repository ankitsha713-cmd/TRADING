"""SEC EDGAR primary-source data: dilution, cash runway, insider clusters.

Uses only the verified-free official endpoints (data.sec.gov + sec.gov
archives): no API key, JSON/XML out. Two rules from SEC's access policy are
enforced here because violating them gets the IP blocked:

- every request carries a declared User-Agent (app name + contact),
- requests are throttled well under the 10 req/s cap.

Everything is best-effort and cached: a candidate without EDGAR data simply
scores without the dilution/insider factors.
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

import defusedxml.ElementTree as SafeET
import requests

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"

_MIN_REQUEST_INTERVAL = 0.15  # ~6 req/s, comfortably under SEC's 10 req/s cap
_last_request_at = 0.0
_throttle_lock = threading.Lock()

_TICKER_MAP: dict[str, str] = {}
_TICKER_MAP_FETCHED = 0.0
_TICKER_MAP_TTL = 24 * 3600

_FACTS_CACHE: dict[str, tuple[dict | None, float]] = {}
_FACTS_TTL = 12 * 3600


def _user_agent() -> str:
    settings = get_settings()
    contact = settings.email_from or settings.smtp_username or "unknown@example.com"
    return f"TradingAgentsAssistant/1.0 ({contact})"


def _get(url: str, timeout: float = 15) -> requests.Response | None:
    """Throttled, identified GET. Returns None on any failure."""
    global _last_request_at
    with _throttle_lock:
        wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
    try:
        response = requests.get(url, headers={"User-Agent": _user_agent()}, timeout=timeout)
        if response.status_code != 200:
            logger.debug("EDGAR %s -> HTTP %s", url, response.status_code)
            return None
        return response
    except requests.RequestException:
        logger.debug("EDGAR request failed: %s", url)
        return None


def cik_for(symbol: str) -> str | None:
    """10-digit zero-padded CIK for a US ticker (daily-cached official map)."""
    global _TICKER_MAP, _TICKER_MAP_FETCHED
    now = time.monotonic()
    if not _TICKER_MAP or now - _TICKER_MAP_FETCHED > _TICKER_MAP_TTL:
        response = _get(_TICKER_MAP_URL)
        if response is not None:
            try:
                raw = response.json()
                _TICKER_MAP = {
                    row["ticker"].upper(): str(row["cik_str"]).zfill(10)
                    for row in raw.values()
                }
                _TICKER_MAP_FETCHED = now
                logger.info("EDGAR ticker map loaded: %d entries", len(_TICKER_MAP))
            except Exception:
                logger.warning("EDGAR ticker map parse failed")
    return _TICKER_MAP.get(symbol.upper().replace("-", "."))


def _companyfacts(cik: str) -> dict | None:
    cached = _FACTS_CACHE.get(cik)
    if cached and time.monotonic() - cached[1] < _FACTS_TTL:
        return cached[0]
    response = _get(_COMPANYFACTS_URL.format(cik=cik))
    facts = None
    if response is not None:
        try:
            facts = response.json()
        except Exception:
            logger.debug("companyfacts parse failed for CIK %s", cik)
    _FACTS_CACHE[cik] = (facts, time.monotonic())
    return facts


# ---------------------------------------------------------------------------
# Dilution + cash runway (Iteration 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DilutionMetrics:
    shares_yoy_pct: float | None       # share count growth over ~1 year
    cash_usd: float | None
    quarterly_burn_usd: float | None   # positive number = burning cash
    runway_quarters: float | None      # None when not burning or unknown


def _units(facts: dict, taxonomy: str, tag: str, unit: str) -> list[dict]:
    try:
        return facts["facts"][taxonomy][tag]["units"][unit]
    except (KeyError, TypeError):
        return []


def _latest_and_year_ago(entries: list[dict]) -> tuple[float | None, float | None]:
    """(latest value, value ~1 year earlier) from XBRL fact entries."""
    dated = []
    for e in entries:
        try:
            dated.append((date.fromisoformat(e["end"]), float(e["val"])))
        except (KeyError, ValueError, TypeError):
            continue
    if not dated:
        return None, None
    dated.sort()
    latest_date, latest_val = dated[-1]
    target = latest_date - timedelta(days=365)
    year_ago = min(dated, key=lambda pair: abs((pair[0] - target).days))
    if abs((year_ago[0] - target).days) > 120:  # nothing close to a year back
        return latest_val, None
    return latest_val, year_ago[1]


def dilution_metrics_from_facts(facts: dict) -> DilutionMetrics:
    """Pure extraction — separated for unit testing with fixture JSON."""
    shares_now, shares_then = _latest_and_year_ago(
        _units(facts, "dei", "EntityCommonStockSharesOutstanding", "shares")
        or _units(facts, "us-gaap", "CommonStockSharesOutstanding", "shares")
    )
    shares_yoy = None
    if shares_now and shares_then and shares_then > 0:
        shares_yoy = (shares_now - shares_then) / shares_then * 100

    cash, _ = _latest_and_year_ago(
        _units(facts, "us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD")
        or _units(
            facts, "us-gaap",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD",
        )
    )

    # Operating cash flow entries are duration facts; approximate trailing
    # year with the widest recent duration ending at the latest date.
    ocf_entries = _units(
        facts, "us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"
    )
    ttm_ocf = None
    best = None
    for e in ocf_entries:
        try:
            start, end = date.fromisoformat(e["start"]), date.fromisoformat(e["end"])
            days = (end - start).days
            if 300 <= days <= 400 and (best is None or end > best[0]):  # annual-ish
                best = (end, float(e["val"]))
        except (KeyError, ValueError, TypeError):
            continue
    if best is not None:
        ttm_ocf = best[1]

    burn = runway = None
    if ttm_ocf is not None and ttm_ocf < 0:
        burn = -ttm_ocf / 4
        if cash is not None and burn > 0:
            runway = cash / burn
    return DilutionMetrics(
        shares_yoy_pct=shares_yoy,
        cash_usd=cash,
        quarterly_burn_usd=burn,
        runway_quarters=runway,
    )


def fetch_dilution_sync(symbol: str) -> DilutionMetrics | None:
    cik = cik_for(symbol)
    if cik is None:
        return None
    facts = _companyfacts(cik)
    if facts is None:
        return None
    try:
        return dilution_metrics_from_facts(facts)
    except Exception:
        logger.warning("Dilution extraction failed for %s", symbol)
        return None


# ---------------------------------------------------------------------------
# Form 4 insider transactions + cluster detection (Iteration 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsiderActivity:
    net_shares: float          # open-market buys minus sells across parsed filings
    buyers: int                # distinct owners with open-market purchases
    sellers: int
    filings_parsed: int

    @property
    def cluster_buy(self) -> bool:
        return self.buyers >= 2 and self.net_shares > 0


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_form4_xml(xml_text: str) -> tuple[str, float]:
    """(owner name, net open-market shares) from one Form 4 document.

    Only transaction code P (open-market purchase) and S (open-market sale)
    count — awards, grants, and option exercises are compensation noise, not
    conviction. Pure function for fixture-based tests. Parsed with defusedxml:
    the documents come from an external server, so XXE/entity-expansion
    attacks are treated as real.
    """
    root = SafeET.fromstring(xml_text)
    owner = ""
    net = 0.0
    for element in root.iter():
        tag = _strip_ns(element.tag)
        if tag == "rptOwnerName" and not owner:
            owner = (element.text or "").strip()
        elif tag == "nonDerivativeTransaction":
            code = shares = acquired = None
            for sub in element.iter():
                sub_tag = _strip_ns(sub.tag)
                if sub_tag == "transactionCode":
                    code = (sub.text or "").strip()
                elif sub_tag == "transactionShares":
                    value = sub.find("./{*}value")
                    if value is not None and value.text:
                        try:
                            shares = float(value.text)
                        except ValueError:
                            shares = None
                elif sub_tag == "transactionAcquiredDisposedCode":
                    value = sub.find("./{*}value")
                    if value is not None and value.text:
                        acquired = value.text.strip()
            if shares is None or code not in ("P", "S"):
                continue
            if code == "P" and acquired != "D":
                net += shares
            elif code == "S":
                net -= shares
    return owner, net


def fetch_insider_activity_sync(
    symbol: str, days: int = 90, max_filings: int = 8
) -> InsiderActivity | None:
    """Parse recent Form 4 filings for a ticker straight from EDGAR."""
    cik = cik_for(symbol)
    if cik is None:
        return None
    response = _get(_SUBMISSIONS_URL.format(cik=cik))
    if response is None:
        return None
    try:
        recent = response.json()["filings"]["recent"]
        forms = recent["form"]
        accessions = recent["accessionNumber"]
        docs = recent["primaryDocument"]
        filed = recent["filingDate"]
    except (KeyError, ValueError, TypeError):
        return None

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    picks = [
        (accessions[i], docs[i])
        for i in range(len(forms))
        if forms[i] == "4" and filed[i] >= cutoff
    ][:max_filings]
    if not picks:
        return InsiderActivity(net_shares=0.0, buyers=0, sellers=0, filings_parsed=0)

    cik_int = str(int(cik))
    net_total = 0.0
    buyers: set[str] = set()
    sellers: set[str] = set()
    parsed = 0
    for accession, doc in picks:
        url = _ARCHIVES_URL.format(
            cik_int=cik_int, accession=accession.replace("-", ""), doc=doc
        )
        doc_response = _get(url)
        if doc_response is None:
            continue
        try:
            owner, net = parse_form4_xml(doc_response.text)
        except (ET.ParseError, ValueError):  # defusedxml raises ValueError subclasses
            continue
        parsed += 1
        net_total += net
        if net > 0:
            buyers.add(owner or f"owner{parsed}")
        elif net < 0:
            sellers.add(owner or f"owner{parsed}")
    return InsiderActivity(
        net_shares=net_total, buyers=len(buyers), sellers=len(sellers),
        filings_parsed=parsed,
    )
