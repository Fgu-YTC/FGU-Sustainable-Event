"""
永續活動爬蟲：
1. 從「最新消息」與「永續活動」標籤頁收集候選網址
2. 合併上次 events.json／種子網址（避免列表暫未顯示的文章漏抓）
3. 內頁需有「永續活動」標籤才收錄（不抓研討會分類）
4. 從內文解析活動日、時間（含多場次）、地點 → 寫入 events.json

本機：python scraper.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sdgs.fgu.edu.tw"
NEWS_URL = f"{BASE_URL}/zh_tw/announcement/News"
TAG_URL = (
    f"{BASE_URL}/zh_tw/announcement/News"
    f"?tags%5B%5D=6aa249da434ade0ee2b15128"
)
REQUIRED_TAG = "永續活動"
REQUIRED_TAG_ID = "6aa249da434ade0ee2b15128"
OUTPUT_PATH = Path(__file__).resolve().parent / "events.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = 30
DELAY_SECONDS = 0.4
MAX_PAGES = 20

SEED_URLS = [
    "https://sdgs.fgu.edu.tw/zh_tw/announcement/News/"
    "%F0%9F%8C%B1-ESG%E5%9F%B9%E5%8A%9B%E8%AA%B2%E7%A8%8B-"
    "%E6%B0%B8%E7%BA%8C%E5%A0%B1%E5%91%8A%E6%9B%B8%E5%AF%A6%E6%88%B0%E8%A7%A3%E6%9E%90-"
    "%E5%BE%9E%E5%90%88%E8%A6%8F%E5%88%B0%E7%89%B9%E8%89%B2%E5%B1%95%E7%8F%BE-72919609",
]

MONTH_LABELS = {
    1: "一月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "十一月",
    12: "十二月",
}

TIME_RANGE_RE = re.compile(
    r"(\d{1,2}:\d{2})\s*[-–—~〜～至到]\s*(?:上午|下午|中午)?\s*(\d{1,2}:\d{2})"
)
CLOCK_RE = re.compile(r"\d{1,2}:\d{2}")
SESSION_RE = re.compile(r"【第\s*(\d+)\s*場】\s*([^\n]+)")
ADMISSION_NOTE_RE = re.compile(
    r"\s*[（(][^）)]*開放進場[^）)]*[）)]"
)


def clean_time_text(value: str) -> str:
    """只保留幾點到幾點，去掉開放進場等括號說明。"""
    text = ADMISSION_NOTE_RE.sub("", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_soup(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    # 官網為 UTF-8；勿用 apparent_encoding（Actions 上易誤判成亂碼）
    response.encoding = "utf-8"
    return BeautifulSoup(response.content, "html.parser", from_encoding="utf-8")


def normalize_link(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    full = urljoin(BASE_URL, url)
    parts = urlsplit(full)
    path = parts.path
    if path.startswith("/announcement/"):
        path = "/zh_tw" + path
    elif path.startswith("/zh_cn/announcement/"):
        path = path.replace("/zh_cn/", "/zh_tw/", 1)
    elif path.startswith("/en/announcement/"):
        path = path.replace("/en/", "/zh_tw/", 1)
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def article_key(url: str) -> str:
    norm = normalize_link(url)
    m = re.search(r"-(\d+)$", norm)
    return m.group(1) if m else norm


def parse_date_parts(date_str: str) -> tuple[str, str, str]:
    date_str = (date_str or "").strip()
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
    if not match:
        return date_str, date_str or "—", ""
    year, month, day = match.groups()
    month_i = int(month)
    day_s = f"{int(day):02d}"
    return (
        f"{int(year):04d}-{month_i:02d}-{day_s}",
        MONTH_LABELS.get(month_i, f"{month_i}月"),
        day_s,
    )


def _valid_iso(y: int, mo: int, d: int) -> str | None:
    if not (2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _iso_from_roc(y: int, mo: int, d: int) -> str | None:
    if y < 1911:
        y += 1911
    return _valid_iso(y, mo, d)


def extract_event_date(content_text: str, post_date: str) -> tuple[str, str]:
    """
    回傳 (活動日, 來源)。優先內文活動時間，找不到再用發佈日。
    注意：不可把「2026年5月13日」誤拆成民國 26 年（→1937）。
    """
    text = content_text or ""
    post = (post_date or "").strip()

    # 1) 西元完整：2026年5月13日 / 2026-05-13（可選前面有時間／日期標籤）
    for pat, src in (
        (
            r"(?:時間|日期|活動時間|活動日期)[：:\s]*[^\n]{0,40}?"
            r"(?<!\d)(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})",
            "content_ymd_labeled",
        ),
        (
            r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
            "content_ymd",
        ),
    ):
        m = re.search(pat, text)
        if m:
            iso = _valid_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if iso:
                return iso, src

    # 2) 民國年：115 年 9 月 23 日（(?<!\d) 避免吃到 2026 的尾數）
    m = re.search(
        r"(?<!\d)(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        text,
    )
    if m:
        iso = _iso_from_roc(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso, "content_roc"

    # 3) 多場次取第一場：【第 1 場】9/8
    m = re.search(r"【第\s*1\s*場】\s*(\d{1,2})\s*/\s*(\d{1,2})", text)
    if m:
        year_m = re.match(r"^(\d{4})", post)
        year = int(year_m.group(1)) if year_m else 2026
        iso = _valid_iso(year, int(m.group(1)), int(m.group(2)))
        if iso:
            return iso, "content_session1"

    if re.match(r"^\d{4}-\d{2}-\d{2}", post):
        return post[:10], "post_date"
    return post, "post_date"


def parse_sessions(content_text: str, fallback_year: str) -> list[dict]:
    """多場次工作坊：拆成第1場、第2場…各自日期與時間。"""
    text = content_text or ""
    raw_sessions = SESSION_RE.findall(text)
    if len(raw_sessions) < 2:
        return []

    year_m = re.match(r"^(\d{4})", (fallback_year or "").strip())
    year = int(year_m.group(1)) if year_m else 2026
    parsed: list[dict] = []

    for num, rest in raw_sessions:
        rest = re.split(r"[👉📌]", rest, maxsplit=1)[0].strip()
        rest = clean_time_text(rest)
        dm = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", rest)
        tm = TIME_RANGE_RE.search(rest)
        if not dm or not tm:
            continue
        iso = _valid_iso(year, int(dm.group(1)), int(dm.group(2)))
        if not iso:
            continue
        date_display, month_label, day = parse_date_parts(iso)
        parsed.append(
            {
                "session": int(num),
                "date": date_display,
                "month_label": month_label,
                "day": day,
                "time": f"{tm.group(1)} - {tm.group(2)}",
            }
        )
    return parsed


def expand_multi_session(event: dict, content_text: str) -> list[dict]:
    sessions = parse_sessions(
        content_text,
        event.get("date") or event.get("post_date") or "",
    )
    if not sessions:
        return [event]

    base_title = re.sub(r"（第\d+場）$", "", event.get("title") or "")
    expanded: list[dict] = []
    for s in sessions:
        item = dict(event)
        item.update(
            {
                "title": f"{base_title}（第{s['session']}場）",
                "date": s["date"],
                "month_label": s["month_label"],
                "day": s["day"],
                "time": s["time"],
                "session": s["session"],
                "date_source": "content_session",
            }
        )
        expanded.append(item)
    return expanded


def extract_time(content_text: str) -> str:
    """解析活動時間；單場次活動用。多場次改由 expand_multi_session 拆項。"""
    text = content_text or ""

    sessions = SESSION_RE.findall(text)
    if len(sessions) >= 2:
        lines: list[str] = []
        for num, rest in sessions:
            rest = re.split(r"[👉📌]", rest, maxsplit=1)[0].strip()
            rest = clean_time_text(rest)
            if CLOCK_RE.search(rest):
                lines.append(f"第{num}場 {rest}")
        if lines:
            return "\n".join(lines)

    labeled = re.search(
        r"(?:⏰\s*)?(?:時間|活動時間)\s*[：:]\s*([^\n]{0,160})",
        text,
    )
    chunk = labeled.group(1) if labeled else ""

    for source in (chunk, text):
        if not source:
            continue
        m = TIME_RANGE_RE.search(source)
        if m:
            return f"{m.group(1)} - {m.group(2)}"

    if chunk:
        m = CLOCK_RE.search(clean_time_text(chunk))
        if m:
            return m.group(0)

    return ""


def extract_location(content_text: str) -> str:
    text = content_text or ""
    # 必須有冒號，避免「時程與地點」誤判
    m = re.search(r"(?:📍\s*)?地點\s*[：:]\s*\n?\s*([^\n]+)", text)
    if not m:
        return ""
    loc = m.group(1).strip()
    loc = re.sub(r"^[：:\s•📍]+", "", loc)
    if CLOCK_RE.match(loc) or loc.startswith("（") or loc.startswith("("):
        return ""
    loc = re.split(r"[（(]\s*\d{1,2}:\d{2}", loc, maxsplit=1)[0].strip()
    loc = re.sub(r"\s+", " ", loc)
    if len(loc) > 60:
        loc = loc[:60].rstrip()
    return loc


def discover_max_page(soup: BeautifulSoup) -> int:
    pages = {1}
    for a in soup.select("a[href*='page_no']"):
        m = re.search(r"page_no=(\d+)", a.get("href", ""))
        if m:
            pages.add(int(m.group(1)))
    return min(max(pages), MAX_PAGES)


def parse_list_page(soup: BeautifulSoup) -> list[dict]:
    rows: list[dict] = []
    for item in soup.select("li.i-annc__item"):
        link_el = item.select_one("a[href]")
        if not link_el:
            continue
        href = link_el.get("href", "")
        if "/announcement/" not in href:
            continue
        path = urlsplit(urljoin(BASE_URL, href)).path.rstrip("/")
        if path.endswith("/News") or path.endswith("/Seminar"):
            continue

        title_el = item.select_one(".i-annc__title")
        date_el = item.select_one(".i-annc__postdate")
        title = (
            title_el.get_text(strip=True)
            if title_el
            else link_el.get_text(strip=True)
        )
        link = normalize_link(href)
        rows.append(
            {
                "title": title,
                "date": date_el.get_text(strip=True) if date_el else "",
                "link": link,
                "key": article_key(link),
            }
        )
    return rows


def collect_from_list(list_url: str, label: str) -> dict[str, dict]:
    print(f"正在抓取{label}：{list_url}")
    first = get_soup(list_url)
    max_page = discover_max_page(first)
    print(f"  分頁：1～{max_page}")
    by_key: dict[str, dict] = {}
    for page in range(1, max_page + 1):
        soup = first if page == 1 else get_soup(f"{list_url}?page_no={page}")
        rows = parse_list_page(soup)
        print(f"  第 {page} 頁：{len(rows)} 筆")
        for row in rows:
            by_key[row["key"]] = row
        if page < max_page:
            time.sleep(DELAY_SECONDS)
    return by_key


def collect_from_tag_page() -> dict[str, dict]:
    print(f"正在抓取標籤頁：{TAG_URL}")
    soup = get_soup(TAG_URL)
    rows = parse_list_page(soup)
    print(f"  標籤頁：{len(rows)} 筆")
    return {row["key"]: row for row in rows}


def collect_from_previous() -> dict[str, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    by_key: dict[str, dict] = {}
    for item in data if isinstance(data, list) else []:
        link = normalize_link(item.get("link", ""))
        if not link:
            continue
        key = article_key(link)
        by_key[key] = {
            "title": item.get("title") or "",
            "date": item.get("date") or item.get("date_display") or "",
            "link": link,
            "key": key,
        }
    print(f"合併上次 events.json：{len(by_key)} 筆")
    return by_key


def collect_from_seeds() -> dict[str, dict]:
    by_key: dict[str, dict] = {}
    for url in SEED_URLS:
        link = normalize_link(url)
        key = article_key(link)
        by_key[key] = {"title": "", "date": "", "link": link, "key": key}
    if by_key:
        print(f"合併種子網址：{len(by_key)} 筆")
    return by_key


def collect_candidates() -> list[dict]:
    by_key: dict[str, dict] = {}
    for chunk in (
        collect_from_list(NEWS_URL, "最新消息"),
        collect_from_tag_page(),
        collect_from_previous(),
        collect_from_seeds(),
    ):
        by_key.update(chunk)
    items = list(by_key.values())
    print(f"候選去重後共 {len(items)} 筆，開始檢查「{REQUIRED_TAG}」標籤…")
    return items


def page_has_sustainability_tag(soup: BeautifulSoup) -> bool:
    for el in soup.select(".s-annc__tag"):
        if el.get_text(strip=True) == REQUIRED_TAG:
            return True
        parent = el.find_parent("a")
        if parent and REQUIRED_TAG_ID in (parent.get("href") or ""):
            return True
    return False


def extract_detail_meta(soup: BeautifulSoup) -> tuple[str, str, str]:
    """title, post_date, content_text"""
    title_el = soup.select_one("h3")
    title = title_el.get_text(strip=True) if title_el else ""

    date_el = soup.select_one(".s-annc__date")
    date = date_el.get_text(strip=True) if date_el else ""

    body = soup.select_one("div.s-annc__post-body")
    content_text = body.get_text("\n", strip=True) if body else ""
    return title, date, content_text


def fetch_sustainability_events() -> list[dict]:
    candidates = collect_candidates()
    events: list[dict] = []

    for index, row in enumerate(candidates, start=1):
        link = row["link"]
        hint_title = row.get("title") or link
        print(f"  [{index}/{len(candidates)}] 檢查：{hint_title[:36]}…")
        time.sleep(DELAY_SECONDS)
        try:
            soup = get_soup(link)
        except requests.RequestException as exc:
            print(f"      略過（讀取失敗）：{exc}")
            continue

        if not page_has_sustainability_tag(soup):
            print("      → 無「永續活動」標籤")
            continue

        title, post_date, content_text = extract_detail_meta(soup)
        post_date = post_date or row.get("date") or ""
        event_date, date_source = extract_event_date(content_text, post_date)
        date_display, month_label, day = parse_date_parts(event_date)
        event_time = extract_time(content_text)
        location = extract_location(content_text)
        final_title = title or row.get("title") or "（無標題）"
        print(
            f"      → 符合，日={date_display}（{date_source}），"
            f"時={event_time[:40] or '—'}，地={location or '—'}"
        )

        base_event = {
            "title": final_title,
            "date": date_display,
            "month_label": month_label,
            "day": day,
            "time": event_time,
            "location": location,
            "link": link,
            "post_date": post_date,
            "date_source": date_source,
        }
        expanded = expand_multi_session(base_event, content_text)
        if len(expanded) > 1:
            print(f"      → 拆成 {len(expanded)} 場次")
        events.extend(expanded)

    events.sort(key=lambda e: e.get("date") or "", reverse=True)
    return events


def main() -> None:
    events = fetch_sustainability_events()
    OUTPUT_PATH.write_text(
        json.dumps(events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"成功擷取 {len(events)} 筆「{REQUIRED_TAG}」，已存入 {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
