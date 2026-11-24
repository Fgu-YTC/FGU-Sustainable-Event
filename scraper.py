"""
佛光大學永續發展辦公室 — 永續活動自動爬蟲（升級版）

工作流程：
1. 直接爬取官網「永續活動」標籤頁（支援 &page_no=N 自動翻頁，不重爬無關新聞稿）
2. 逐篇進入活動內頁，複驗「永續活動」標籤並精準擷取標題、發文日與結構化內文
3. 智慧解析活動日期（支援西元／民國／跨年推算，避開報名截止日）、時間（含上下午與單一時間）、
   地點（支援線上／括號說明）及多場次拆項（支援【第1場】、第1場次、第一場、場次一等）
4. 寫入 events.json 供前端顯示（Coming Soon / 歷年活動）

本機執行：python scraper.py
"""

from __future__ import annotations

import json
import re
import time
from datetime import date as dt_date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

BASE_URL = "https://sdgs.fgu.edu.tw"
NEWS_URL = f"{BASE_URL}/zh_tw/announcement/News"
REQUIRED_TAG = "永續活動"
REQUIRED_TAG_ID = "6aa249da434ade0ee2b15128"
TAG_URL = f"{NEWS_URL}?tags%5B%5D={REQUIRED_TAG_ID}"
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
DELAY_SECONDS = 0.35
MAX_PAGES = 30

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

ZH_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

BLOCK_TAGS = {
    "p", "div", "li", "tr", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "section", "article", "header", "footer", "table",
}

DEADLINE_LINE_RE = re.compile(r"(?:報名)?截止|截止(?:時間|日期)|報名期限|報名期間")
EVENT_DATE_LABEL_RE = re.compile(
    r"(?:活動日期時間|活動日期|活動時間|講座日期|講座時間|日期時間|時間|日期)\s*[：:]"
)
TIME_RANGE_RE = re.compile(
    r"(?:(上午|下午|中午|晚間|晚上)\s*)?(\d{1,2})\s*[:：]\s*(\d{2})"
    r"\s*[-–—~〜～至到]\s*"
    r"(?:(上午|下午|中午|晚間|晚上)\s*)?(\d{1,2})\s*[:：]\s*(\d{2})"
)
TIME_SINGLE_RE = re.compile(
    r"(?:(上午|下午|中午|晚間|晚上)\s*)?(\d{1,2})\s*[:：]\s*(\d{2})"
)
ADMISSION_NOTE_RE = re.compile(r"\s*[（(][^）)]*(?:開放進場|報到|入場)[^）)]*[）)]")

# 支援：【第 1 場】、【第一場】、第 1 場次：、第一場：、場次 1：、場次一：
SESSION_INLINE_RE = re.compile(
    r"【\s*(?:第\s*([0-9]+|[一二三四五六七八九十])\s*場次?|場次\s*([0-9]+|[一二三四五六七八九十]))\s*】\s*([^\n]+)"
)
SESSION_BLOCK_RE = re.compile(
    r"(?:📌\s*)?(?:第\s*([0-9]+|[一二三四五六七八九十])\s*場次?|場次\s*([0-9]+|[一二三四五六七八九十]))\s*[：:]"
    r"(?P<body>.*?)(?=(?:📌\s*)?(?:第\s*(?:[0-9]+|[一二三四五六七八九十])\s*場次?|場次\s*(?:[0-9]+|[一二三四五六七八九十]))\s*[：:]|\Z)",
    re.S,
)


def parse_session_num(token: str) -> int:
    token = (token or "").strip()
    if token.isdigit():
        return int(token)
    return ZH_NUM_MAP.get(token, 0)


def _to_24h(period: str | None, hour_s: str, min_s: str) -> str:
    h = int(hour_s)
    m = int(min_s)
    if period in ("下午", "晚間", "晚上") and 1 <= h <= 11:
        h += 12
    elif period == "上午" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def clean_time_text(value: str) -> str:
    """移除（12:40開放進場）等括號附註並壓縮空白。"""
    text = ADMISSION_NOTE_RE.sub("", value or "")
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def get_soup(url: str) -> BeautifulSoup:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.content, "html.parser", from_encoding="utf-8")


def with_query_param(url: str, key: str, value: str | int) -> str:
    """安全附加或替換網址查詢參數（避免重複出現兩個 '?'）。"""
    parts = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    params.append((key, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


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


def extract_clean_text(container: Tag | None) -> str:
    """
    保留段落換行，但不把 <strong>時間</strong>：... 這類行內元素切斷成兩行。
    """
    if not container:
        return ""
    parts: list[str] = []

    def walk(node: Tag | NavigableString) -> None:
        if isinstance(node, NavigableString):
            txt = str(node).replace("\xa0", " ")
            parts.append(txt)
            return
        if not isinstance(node, Tag):
            return
        name = (node.name or "").lower()
        if name in ("script", "style", "noscript"):
            return
        if name == "br":
            parts.append("\n")
            return
        is_block = name in BLOCK_TAGS
        if is_block:
            parts.append("\n")
        for child in node.children:
            walk(child)
        if is_block:
            parts.append("\n")

    walk(container)
    raw = "".join(parts)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    # 若出現「地點：」後換行接內容，或「時間」換行接「：...」，適度合併相鄰斷行
    merged: list[str] = []
    for ln in lines:
        if merged and re.match(r"^[：:]", ln):
            merged[-1] = merged[-1] + ln
        elif merged and re.match(r"^(?:[◆•📍⏰🗓]\s*)?(?:活動)?(?:時間|日期|日期時間|講座地點|活動地點|地點)\s*[：:]\s*$", merged[-1]):
            merged[-1] = merged[-1] + " " + ln
        else:
            merged.append(ln)
    return "\n".join(merged)


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
    try:
        parsed = dt_date(y, mo, d)
    except ValueError:
        return None
    if not (2000 <= parsed.year <= 2100):
        return None
    return parsed.isoformat()


def _iso_from_year(y: int, mo: int, d: int) -> str | None:
    if y < 1911:
        y += 1911
    return _valid_iso(y, mo, d)


def _infer_year_for_md(month: int, post_date: str) -> int:
    """
    無年份的月/日（例如 1/15）：以發文日年份為基準；
    若 10～12 月發文預告 1～3 月活動，自動進位為隔年（解決跨年誤判）。
    """
    m = re.match(r"^(\d{4})-(\d{1,2})", (post_date or "").strip())
    if m:
        post_year, post_month = int(m.group(1)), int(m.group(2))
        if post_month >= 10 and month <= 3:
            return post_year + 1
        return post_year
    return dt_date.today().year


def _extract_date_from_line(line: str, post_date: str) -> tuple[str | None, str]:
    """從單行文字擷取西元／民國／月日日期。"""
    # 1) 西元完整：2026年11月4日 / 2026/11/4 / 2026-11-04 / 2026.11.4
    m = re.search(r"(?<!\d)(\d{4})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?", line)
    if m:
        iso = _valid_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso, "content_ymd"

    # 2) 民國完整：115年12月2日 / 115/12/2 / 115.12.02
    m = re.search(r"(?<!\d)(1\d{2})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?", line)
    if m:
        iso = _iso_from_year(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso, "content_roc"

    # 3) 無年份月日：11月4日 / 12/2
    m = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日", line)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        iso = _valid_iso(_infer_year_for_md(mo, post_date), mo, d)
        if iso:
            return iso, "content_md"

    m = re.search(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\s*/|\d)", line)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        iso = _valid_iso(_infer_year_for_md(mo, post_date), mo, d)
        if iso:
            return iso, "content_md"

    return None, ""


def extract_event_date(content_text: str, post_date: str) -> tuple[str, str]:
    """
    優先順序：
    1. 明確標有「時間：／日期：／活動時間：」且非「報名截止」的行（西元與民國同等優先）
    2. 一般內文非截止行中的西元或民國完整日期
    3. 發文日
    """
    lines = [ln for ln in (content_text or "").splitlines() if ln.strip()]
    post = (post_date or "").strip()

    # 1) 具名活動日期／時間行（排除報名截止行）
    for ln in lines:
        if DEADLINE_LINE_RE.search(ln):
            continue
        if EVENT_DATE_LABEL_RE.search(ln):
            iso, src = _extract_date_from_line(ln, post)
            if iso:
                return iso, f"{src}_labeled"

    # 2) 非截止行中的完整西元／民國日期
    for ln in lines:
        if DEADLINE_LINE_RE.search(ln):
            continue
        m = re.search(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", ln)
        if m:
            iso = _valid_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if iso:
                return iso, "content_ymd"
        m = re.search(r"(?<!\d)(1\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", ln)
        if m:
            iso = _iso_from_year(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if iso:
                return iso, "content_roc"

    if re.match(r"^\d{4}-\d{2}-\d{2}", post):
        return post[:10], "post_date"
    return post, "post_date"


def _extract_time_from_chunk(chunk: str) -> str:
    cleaned = clean_time_text(chunk)
    m = TIME_RANGE_RE.search(cleaned)
    if m:
        p1, h1, m1, p2, h2, m2 = m.groups()
        t1 = _to_24h(p1, h1, m1)
        # 若前段有「下午」且後段沒寫上下午、數字小於 12（如 下午 1:10 - 3:00），沿用下午
        eff_p2 = p2 or (p1 if p1 in ("下午", "晚間", "晚上") and int(h2) < 12 else None)
        t2 = _to_24h(eff_p2, h2, m2)
        return f"{t1} - {t2}"
    m = TIME_SINGLE_RE.search(cleaned)
    if m:
        p1, h1, m1 = m.groups()
        return _to_24h(p1, h1, m1)
    return ""


def _session_from_chunk(num: int, chunk: str, post_date: str) -> dict | None:
    if num <= 0:
        return None
    chunk_lines = [ln for ln in chunk.splitlines() if ln.strip() and not DEADLINE_LINE_RE.search(ln)]
    if not chunk_lines:
        return None

    iso = None
    time_str = ""
    for ln in chunk_lines:
        if not iso:
            found_iso, _ = _extract_date_from_line(ln, post_date)
            if found_iso:
                iso = found_iso
        if not time_str:
            found_t = _extract_time_from_chunk(ln)
            if found_t:
                time_str = found_t

    if not iso:
        return None

    date_display, month_label, day = parse_date_parts(iso)
    location = extract_location("\n".join(chunk_lines))
    return {
        "session": num,
        "date": date_display,
        "month_label": month_label,
        "day": day,
        "time": time_str,
        "location": location,
    }


def parse_sessions(content_text: str, post_date: str) -> list[dict]:
    """多場次拆項：支援區塊格式（第 1 場次：...）與單行格式（【第 1 場】...）。"""
    text = content_text or ""

    blocks = SESSION_BLOCK_RE.findall(text)
    if len(blocks) >= 2:
        parsed: list[dict] = []
        for n1, n2, body in blocks:
            num = parse_session_num(n1 or n2)
            item = _session_from_chunk(num, body, post_date)
            if item:
                parsed.append(item)
        if len(parsed) >= 2:
            return parsed

    inlines = SESSION_INLINE_RE.findall(text)
    if len(inlines) >= 2:
        parsed = []
        for n1, n2, rest in inlines:
            num = parse_session_num(n1 or n2)
            rest = re.split(r"[👉📌🔗]", rest, maxsplit=1)[0].strip()
            item = _session_from_chunk(num, rest, post_date)
            if item:
                parsed.append(item)
        if len(parsed) >= 2:
            return parsed

    return []


def expand_multi_session(event: dict, content_text: str) -> list[dict]:
    sessions = parse_sessions(content_text, event.get("post_date") or event.get("date") or "")
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
        if s.get("location"):
            item["location"] = s["location"]
        expanded.append(item)
    return expanded


def extract_time(content_text: str) -> str:
    """擷取單場次活動時間（避開報名截止時間）。"""
    lines = [ln for ln in (content_text or "").splitlines() if ln.strip()]

    for ln in lines:
        if DEADLINE_LINE_RE.search(ln):
            continue
        if EVENT_DATE_LABEL_RE.search(ln):
            t = _extract_time_from_chunk(ln)
            if t:
                return t

    for ln in lines:
        if DEADLINE_LINE_RE.search(ln):
            continue
        m = TIME_RANGE_RE.search(clean_time_text(ln))
        if m:
            return _extract_time_from_chunk(ln)

    return ""


def extract_location(content_text: str) -> str:
    """擷取活動地點（支援括號線上地點，僅過濾純進場時間備註）。"""
    for ln in (content_text or "").splitlines():
        m = re.search(r"(?:講座地點|活動地點|上課地點|演講地點|會議地點|地點)\s*[：:]\s*(.+)", ln)
        if not m:
            continue
        loc = m.group(1).strip()
        loc = re.sub(r"^[：:\s•·📍◆]+", "", loc)
        # 切掉同行的其他欄位或符號
        loc = re.split(r"\s*(?:🔗|※|主辦單位|聯絡人|📌|【|👉)", loc, maxsplit=1)[0].strip()
        # 移除尾端進場時間括號，如「雲起樓102教室（12:40開放進場）」
        loc = ADMISSION_NOTE_RE.sub("", loc).strip()
        loc = re.split(r"[（(]\s*\d{1,2}:\d{2}", loc, maxsplit=1)[0].strip()
        loc = re.sub(r"\s+", " ", loc)
        if not loc or TIME_SINGLE_RE.match(loc):
            continue
        if len(loc) > 60:
            loc = loc[:60].rstrip()
        return loc
    return ""


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
        title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
        link = normalize_link(href)
        rows.append(
            {
                "title": title,
                "post_date": date_el.get_text(strip=True) if date_el else "",
                "link": link,
                "key": article_key(link),
            }
        )
    return rows


def collect_from_tag_pages() -> list[dict]:
    """
    直接翻完「永續活動」標籤頁的所有分頁（使用 &page_no=N），
    只抓有正確標籤的文章，速度快且若同事移除標籤會自動同步下架。
    """
    print(f"正在抓取「{REQUIRED_TAG}」標籤頁：{TAG_URL}")
    first = get_soup(TAG_URL)
    max_page = discover_max_page(first)
    print(f"  偵測分頁：1～{max_page}")

    by_key: dict[str, dict] = {}
    for page in range(1, max_page + 1):
        url = TAG_URL if page == 1 else with_query_param(TAG_URL, "page_no", page)
        soup = first if page == 1 else get_soup(url)
        rows = parse_list_page(soup)
        print(f"  第 {page} 頁：{len(rows)} 筆")
        for row in rows:
            by_key[row["key"]] = row
        if not rows:
            break
        if page < max_page:
            time.sleep(DELAY_SECONDS)

    return list(by_key.values())


def page_has_sustainability_tag(soup: BeautifulSoup) -> bool:
    for el in soup.select(".s-annc__tag"):
        if el.get_text(strip=True) == REQUIRED_TAG:
            return True
        parent = el.find_parent("a")
        if parent and REQUIRED_TAG_ID in (parent.get("href") or ""):
            return True
    return False


def extract_detail_meta(soup: BeautifulSoup) -> tuple[str, str, str]:
    """精準擷取文章標題、發文日期與不切斷行內標籤的純文字內文。"""
    title_el = (
        soup.select_one("h3.s-annc__show-title")
        or soup.select_one("h3.annc-title")
        or soup.select_one("#main-content h3")
    )
    title = title_el.get_text(strip=True) if title_el else ""

    date_el = soup.select_one(".s-annc__date")
    post_date = date_el.get_text(strip=True) if date_el else ""

    body = soup.select_one("div.s-annc__post-body")
    content_text = extract_clean_text(body)
    return title, post_date, content_text


def fetch_sustainability_events() -> list[dict]:
    candidates = collect_from_tag_pages()
    print(f"共 {len(candidates)} 篇「{REQUIRED_TAG}」文章，開始解析內文…")
    events: list[dict] = []

    for index, row in enumerate(candidates, start=1):
        link = row["link"]
        hint_title = row.get("title") or link
        print(f"  [{index}/{len(candidates)}] 解析：{hint_title[:40]}")
        time.sleep(DELAY_SECONDS)
        try:
            soup = get_soup(link)
        except requests.RequestException as exc:
            print(f"      略過（讀取失敗）：{exc}")
            continue

        if not page_has_sustainability_tag(soup):
            print("      → 內頁已無「永續活動」標籤，略過")
            continue

        title, post_date, content_text = extract_detail_meta(soup)
        post_date = post_date or row.get("post_date") or ""
        event_date, date_source = extract_event_date(content_text, post_date)
        date_display, month_label, day = parse_date_parts(event_date)
        event_time = extract_time(content_text)
        location = extract_location(content_text)
        final_title = title or row.get("title") or "（無標題）"

        print(
            f"      → 日={date_display}（{date_source}），"
            f"時={event_time or '—'}，地={location or '—'}"
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

    events.sort(
        key=lambda e: (e.get("date") or "", e.get("post_date") or "", e.get("session") or 0),
        reverse=True,
    )
    return events


def main() -> None:
    t0 = time.monotonic()
    events = fetch_sustainability_events()
    if not events and OUTPUT_PATH.exists():
        try:
            prev = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = []
        if prev:
            raise RuntimeError("本次抓取結果為 0 筆，為避免官網暫時異常清空資料，已中止寫入。")
    OUTPUT_PATH.write_text(
        json.dumps(events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    elapsed = time.monotonic() - t0
    print(f"完成！共擷取 {len(events)} 筆「{REQUIRED_TAG}」（耗時 {elapsed:.1f}s），已存入 {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
