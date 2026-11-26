"""Scraper for Georgian State Procurement portal (Tenders & Market Researches)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
from typing import Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config
from document_parser import (
    FALLBACK_DELIVERY_TEXT,
    extract_delivery_from_attachments,
    extract_delivery_from_text,
    is_delivery_missing_or_redirect,
    normalize_whitespace,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://tenders.procurement.gov.ge/public/"
TBILISI_TZ = ZoneInfo("Asia/Tbilisi")

ACTIVE_TENDER_STATUSES = {
    "გამოცხადებულია",
    "წინადადებების მიღება დაწყებულია",
}

ACTIVE_QEP_STATUSES = {
    "გამოცხადებულია",
    "წინადადების მიღება დაწყებულია",
    "წინადადებების მიღება დაწყებულია",
}

SHOW_APP_RE = re.compile(r"ShowApp\((\d+),'([^']*)',\d+,'([^']*)'\)")
SHOW_QEP_RE = re.compile(r"ShowQep\((\d+)\)")
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
PLASTPAGE_RE = re.compile(r"plastpage\s*=\s*(\d+)")
CPV_RE = re.compile(r"\b\d{8}\b")


@dataclass
class Announcement:
    procurement_type: str
    announcement_number: str
    buyer: str
    published_date: str
    start_date: str
    end_date: str
    estimated_value: str
    delivery_terms: str
    direct_url: str


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_ddmmyyyy(text: str) -> datetime | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").replace(tzinfo=TBILISI_TZ)
    except ValueError:
        return None


def _clean_amount(raw: str) -> str:
    cleaned = normalize_whitespace(raw).replace("`", ",")
    return cleaned if cleaned else "არ არის მითითებული"


class ProcurementScraper:
    """Client for tenders.procurement.gov.ge public XHR endpoints."""

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.target_cpvs = set(config.cpv_codes)
        self.session = self._build_session()
        self.cutoff_date = (datetime.now(TBILISI_TZ) - timedelta(days=config.lookback_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.cfg.max_retries,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ka-GE,ka;q=0.9,en;q=0.8",
            "Referer": BASE_URL + "?lang=ge",
        })
        session.hooks["response"].append(
            lambda r, *args, **kwargs: setattr(r, "encoding", "utf-8")
            if r.encoding and r.encoding.lower() == "iso-8859-1"
            else None
        )
        return session

    def init_session(self) -> None:
        resp = self.session.get(BASE_URL + "?lang=ge", timeout=self.cfg.request_timeout)
        resp.raise_for_status()

    def _fetch_bytes(self, relative_or_abs_url: str) -> bytes:
        full_url = urljoin(BASE_URL, relative_or_abs_url)
        resp = self.session.get(full_url, timeout=self.cfg.request_timeout, stream=True)
        resp.raise_for_status()
        content = resp.content
        if len(content) > self.cfg.max_doc_bytes:
            logger.warning("Skipping oversized attachment (%d bytes): %s", len(content), full_url)
            return b""
        return content

    def fetch_new_announcements(self, is_seen_fn) -> list[Announcement]:
        """Collect all unseen active tenders and market researches matching CPV codes."""
        self.init_session()
        results: list[Announcement] = []
        seen_in_run: set[str] = set()

        tender_candidates = self._search_tender_candidates(is_seen_fn)
        logger.info("Found %d unseen active tender candidate(s)", len(tender_candidates))
        for app_id, spa_no, key, reg_no in tender_candidates:
            if reg_no in seen_in_run:
                continue
            item = self._fetch_tender_detail(app_id, key)
            if item and item.announcement_number not in seen_in_run and not is_seen_fn(item.announcement_number):
                seen_in_run.add(item.announcement_number)
                results.append(item)

        qep_candidates = self._search_qep_candidates(is_seen_fn)
        logger.info("Found %d unseen active market research candidate(s)", len(qep_candidates))
        for qep_id, reg_no in qep_candidates:
            if reg_no in seen_in_run:
                continue
            item = self._fetch_qep_detail(qep_id)
            if item and item.announcement_number not in seen_in_run and not is_seen_fn(item.announcement_number):
                seen_in_run.add(item.announcement_number)
                results.append(item)

        return results

    def _search_tender_candidates(self, is_seen_fn) -> list[tuple[str, str, str, str]]:
        candidates: list[tuple[str, str, str, str]] = []
        seen_app_ids: set[str] = set()

        for batch in _chunks(self.cfg.cpv_codes, self.cfg.batch_size):
            codes_str = ",".join(batch)
            for page_num in range(1, self.cfg.max_pages_per_batch + 1):
                if page_num == 1:
                    resp = self.session.post(
                        BASE_URL + "library/controller.php",
                        data={
                            "action": "search_app",
                            "app_t": "0",
                            "search": "",
                            "app_reg_id": "",
                            "app_shems_id": "0",
                            "org_a": "",
                            "app_monac_id": "0",
                            "org_b": "",
                            "app_particip_status_id": "0",
                            "app_donor_id": "0",
                            "app_status": "0",
                            "app_agr_status": "0",
                            "app_type": "0",
                            "app_basecode": "0",
                            "app_codes": codes_str,
                            "app_date_type": "1",
                            "app_date_from": "",
                            "app_date_tlll": "",
                            "app_amount_from": "",
                            "app_amount_to": "",
                            "app_currency": "2",
                            "app_pricelist": "0",
                        },
                        timeout=self.cfg.request_timeout,
                    )
                else:
                    resp = self.session.get(
                        BASE_URL + "library/controller.php",
                        params={"action": "search_app", "page": page_num},
                        timeout=self.cfg.request_timeout,
                    )
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table#list_apps_by_subject tbody tr[id^='A']")
                if not rows:
                    break

                oldest_on_page: datetime | None = None
                for tr in rows:
                    onclick = tr.get("onclick", "")
                    m = SHOW_APP_RE.search(onclick)
                    if not m:
                        continue
                    app_id, spa_no, key = m.groups()
                    reg_el = tr.select_one("strong")
                    reg_no = normalize_whitespace(reg_el.get_text()) if reg_el else ""
                    status_el = tr.select_one("p.status")
                    status = normalize_whitespace(status_el.get_text(" ", strip=True)) if status_el else ""
                    row_text = tr.get_text(" ", strip=True)
                    m_pub = re.search(r"გამოცხადების თარიღი:\s*(\d{2}\.\d{2}\.\d{4})", row_text)
                    pub_dt = _parse_ddmmyyyy(m_pub.group(1)) if m_pub else None
                    if pub_dt and (oldest_on_page is None or pub_dt < oldest_on_page):
                        oldest_on_page = pub_dt

                    if not reg_no or is_seen_fn(reg_no) or app_id in seen_app_ids:
                        continue
                    if status not in ACTIVE_TENDER_STATUSES:
                        continue
                    if pub_dt and pub_dt < self.cutoff_date:
                        continue

                    seen_app_ids.add(app_id)
                    candidates.append((app_id, spa_no, key, reg_no))

                m_last = PLASTPAGE_RE.search(resp.text)
                last_page = int(m_last.group(1)) if m_last else 1
                if page_num >= last_page:
                    break
                if oldest_on_page and oldest_on_page < self.cutoff_date:
                    break

        return candidates

    def _search_qep_candidates(self, is_seen_fn) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        seen_qep_ids: set[str] = set()

        for batch in _chunks(self.cfg.cpv_codes, self.cfg.batch_size):
            codes_str = ",".join(batch)
            for page_num in range(1, self.cfg.max_pages_per_batch + 1):
                if page_num == 1:
                    resp = self.session.post(
                        BASE_URL + "library/qep/qep_controller.php",
                        data={
                            "action": "search_qep",
                            "search": "yez",
                            "qep_amount_from": "",
                            "qep_amount_to": "",
                            "qep_reg_no": "",
                            "qep_app_type": "",
                            "qep_shems_id": "0",
                            "org_f": "",
                            "qep_status": "0",
                            "qep_basecode": "0",
                            "qep_codes": codes_str,
                            "qep_date_type": "1",
                            "qep_date_from": "",
                            "qep_date_till": "",
                        },
                        timeout=self.cfg.request_timeout,
                    )
                else:
                    resp = self.session.get(
                        BASE_URL + "library/qep/qep_controller.php",
                        params={"action": "search_qep", "page": page_num},
                        timeout=self.cfg.request_timeout,
                    )
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table#list_qep_apps_by_subject tbody tr[id^='A']")
                if not rows:
                    break

                oldest_on_page: datetime | None = None
                for tr in rows:
                    onclick = tr.get("onclick", "")
                    m = SHOW_QEP_RE.search(onclick)
                    if not m:
                        continue
                    qep_id = m.group(1)
                    reg_el = tr.select_one("strong")
                    reg_no = normalize_whitespace(reg_el.get_text()) if reg_el else ""
                    status_el = tr.select_one("p.status")
                    status = normalize_whitespace(status_el.get_text(" ", strip=True)) if status_el else ""
                    row_text = tr.get_text(" ", strip=True)
                    m_pub = re.search(r"გამოცხადების თარიღი:\s*(\d{2}\.\d{2}\.\d{4})", row_text)
                    pub_dt = _parse_ddmmyyyy(m_pub.group(1)) if m_pub else None
                    if pub_dt and (oldest_on_page is None or pub_dt < oldest_on_page):
                        oldest_on_page = pub_dt

                    if not reg_no or is_seen_fn(reg_no) or qep_id in seen_qep_ids:
                        continue
                    if status not in ACTIVE_QEP_STATUSES:
                        continue
                    if pub_dt and pub_dt < self.cutoff_date:
                        continue

                    seen_qep_ids.add(qep_id)
                    candidates.append((qep_id, reg_no))

                m_last = PLASTPAGE_RE.search(resp.text)
                last_page = int(m_last.group(1)) if m_last else 1
                if page_num >= last_page:
                    break
                if oldest_on_page and oldest_on_page < self.cutoff_date:
                    break

        return candidates

    def _extract_table_kv(self, soup: BeautifulSoup) -> dict[str, str]:
        kv: dict[str, str] = {}
        for tr in soup.select("table.ktable tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) != 2:
                continue
            k = normalize_whitespace(tds[0].get_text(" ", strip=True))
            v = normalize_whitespace(tds[1].get_text(" ", strip=True))
            if k:
                kv[k] = v
        return kv

    def _fetch_tender_detail(self, app_id: str, key: str) -> Announcement | None:
        resp = self.session.get(
            BASE_URL + "library/controller.php",
            params={"action": "app_main", "app_id": app_id, "key": key},
            timeout=self.cfg.request_timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        kv = self._extract_table_kv(soup)

        reg_no = kv.get("განცხადების ნომერი", "")
        if not reg_no:
            return None

        # Strict CPV verification against detailed CPV list
        detail_cpvs = set(CPV_RE.findall(resp.text))
        if not (detail_cpvs & self.target_cpvs):
            logger.debug("Skipping %s — CPVs %s not in target list", reg_no, detail_cpvs)
            return None

        delivery_raw = kv.get("მოწოდების ვადა", "")
        if is_delivery_missing_or_redirect(delivery_raw):
            delivery_terms = self._deep_extract_tender_delivery(app_id, key)
        else:
            delivery_terms = normalize_whitespace(delivery_raw)

        return Announcement(
            procurement_type=kv.get("შესყიდვის ტიპი", "ტენდერი"),
            announcement_number=reg_no,
            buyer=kv.get("შემსყიდველი", "არ არის მითითებული"),
            published_date=kv.get("შესყიდვის გამოცხადების თარიღი", "არ არის მითითებული"),
            start_date=kv.get("წინადადებების მიღება იწყება", "არ არის მითითებული"),
            end_date=kv.get("წინადადებების მიღება მთავრდება", "არ არის მითითებული"),
            estimated_value=_clean_amount(kv.get("შესყიდვის სავარაუდო ღირებულება", "")),
            delivery_terms=delivery_terms or FALLBACK_DELIVERY_TEXT,
            direct_url=f"{BASE_URL}?go={app_id}&lang=ge",
        )

    def _deep_extract_tender_delivery(self, app_id: str, key: str) -> str:
        """Inspect 'დოკუმენტაცია' tab text sections and PDF/DOCX attachments."""
        try:
            resp = self.session.get(
                BASE_URL + "library/controller.php",
                params={"action": "app_docs", "app_id": app_id, "key": key},
                timeout=self.cfg.request_timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to fetch app_docs for app_id=%s: %s", app_id, exc)
            return FALLBACK_DELIVERY_TEXT

        soup = BeautifulSoup(resp.text, "lxml")

        # 1. Structured question 1.4 (#que200) dedicated to delivery/execution term
        q200 = soup.select_one("#que200 .a")
        if q200:
            q200_text = normalize_whitespace(q200.get_text(" ", strip=True))
            if q200_text and not is_delivery_missing_or_redirect(q200_text):
                return q200_text[:280] + ("..." if len(q200_text) > 280 else "")

        # 2. Other documentation sections (1.2 tech spec, 1.5 delivery place, 8.x, 10.1)
        sections_text = "\n".join(
            normalize_whitespace(el.get_text(" ", strip=True))
            for el in soup.select("#app_docs section.question .a")
            if el.get_text(strip=True)
        )
        found_in_html = extract_delivery_from_text(sections_text)
        if found_in_html:
            return found_in_html

        # 3. Parse attached PDF / DOCX files
        attachments = [
            (normalize_whitespace(a.get_text(" ", strip=True)), a.get("href", ""))
            for a in soup.select("a[href*='files.php']")
            if a.get("href")
        ]
        found_in_docs = extract_delivery_from_attachments(self._fetch_bytes, attachments)
        if found_in_docs:
            return found_in_docs

        return FALLBACK_DELIVERY_TEXT

    def _fetch_qep_detail(self, qep_id: str) -> Announcement | None:
        resp = self.session.get(
            BASE_URL + "library/qep/qep_controller.php",
            params={"action": "qep_main", "qep_id": qep_id},
            timeout=self.cfg.request_timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        kv = self._extract_table_kv(soup)

        reg_no = kv.get("რეგისტრაციის ნომერი", "")
        if not reg_no:
            return None

        # Strict CPV verification against detailed CPV list
        detail_cpvs = set(CPV_RE.findall(resp.text))
        if not (detail_cpvs & self.target_cpvs):
            logger.debug("Skipping %s — CPVs %s not in target list", reg_no, detail_cpvs)
            return None

        proc_type = kv.get("შესყიდვის პროცედურა", "").strip()
        full_type = f"ბაზრის კვლევა ({proc_type})" if proc_type else "ბაზრის კვლევა"

        delivery_raw = kv.get("მოწოდების ვადა", "")
        if is_delivery_missing_or_redirect(delivery_raw):
            attachments = [
                (normalize_whitespace(a.get_text(" ", strip=True)), a.get("href", ""))
                for a in soup.select("a[href*='files.php']")
                if a.get("href")
            ]
            delivery_terms = (
                extract_delivery_from_attachments(self._fetch_bytes, attachments)
                or FALLBACK_DELIVERY_TEXT
            )
        else:
            delivery_terms = normalize_whitespace(delivery_raw)

        est_val = kv.get("შესყიდვის ღირებულება", "") or kv.get("შესყიდვის სავარაუდო ღირებულება", "")

        return Announcement(
            procurement_type=full_type,
            announcement_number=reg_no,
            buyer=kv.get("შემსყიდველი", "არ არის მითითებული"),
            published_date=kv.get("რეგისტრაციის თარიღი", "არ არის მითითებული"),
            start_date=kv.get("წინადადებების მიღება იწყება", "არ არის მითითებული"),
            end_date=kv.get("წინადადებების მიღება მთავრდება", "არ არის მითითებული"),
            estimated_value=_clean_amount(est_val),
            delivery_terms=delivery_terms or FALLBACK_DELIVERY_TEXT,
            direct_url=f"{BASE_URL}?qep={qep_id}&lang=ge",
        )
