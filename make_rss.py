#!/usr/bin/env python3
"""Generate RSS 2.0 feeds from HTML pages using CSS selectors.

Usage:
    python3 make_rss.py --config feeds.yaml
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
import warnings
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from xml.sax.saxutils import escape

import yaml
from bs4 import BeautifulSoup, Tag

# 系统自带 Python 使用 LibreSSL 时 urllib3 v2 会打印无害警告，提前过滤。
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")
import requests


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
]

SOGOU_HOST = "https://weixin.sogou.com"
SOGOU_COOKIE = (
    "SNUID=78725B470A0EF2C3F97AA5EB0BBF95C1; "
    "ABTEST=0|1680917938|v1; "
    "SUID=8F7B1C682B83A20A000000006430C5B2; "
    "PHPSESSID=le2lak0vghad5c98ijd3t51ls4; "
    "IPLOC=USUS5"
)

ONE_SITE = "https://wufazhuce.com/"


def log(message: str) -> None:
    print(message, file=sys.stderr)


def resolve_link(node: Tag | None, page_url: str) -> str | None:
    if node is None:
        return None
    anchor = node if node.name == "a" else node.find("a")
    href = anchor.get("href") if anchor else node.get("href")
    if not href:
        return None
    return urljoin(page_url, href.strip())


def node_html(node: Tag | None) -> str | None:
    if node is None:
        return None
    return str(node.decode_contents()).strip() or None


def node_text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def parse_date(node: Tag | None, formats: list[str]) -> dt.datetime | None:
    if node is None:
        return None
    raw = node.get("datetime") or node.get("content") or node.get_text(" ", strip=True)
    if not raw:
        return None
    raw = raw.strip()
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def safe_guid(link: str | None, title: str, source_url: str) -> str:
    if link:
        return link
    digest = hashlib.sha1(f"{source_url}:{title}".encode("utf-8")).hexdigest()
    return f"tag:local,{digest}"


def finalize_items(items: list[dict], max_items: int) -> list[dict]:
    seen: set[str] = set()
    unique_items = []
    for item in items:
        if item["guid"] in seen:
            continue
        seen.add(item["guid"])
        unique_items.append(item)
    unique_items.sort(
        key=lambda item: item["pub_date"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )
    return unique_items[:max_items]


def parse_sogou_time(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    match = re.search(r"timeConvert\('(\d+)'\)", raw)
    if not match:
        return None
    try:
        return dt.datetime.fromtimestamp(int(match.group(1)), tz=dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def resolve_wechat_link(session: requests.Session, sogou_link: str, search_url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Referer": search_url, "Cookie": SOGOU_COOKIE}
    try:
        resp = session.get(sogou_link, headers=headers, allow_redirects=False, timeout=15)
        location = resp.headers.get("Location")
        if location and location.startswith(("http://mp.weixin.qq.com/", "https://mp.weixin.qq.com/")):
            return location
        if location:
            if not location.startswith("http"):
                location = urljoin(sogou_link, location)
            resp2 = session.get(
                location,
                headers={**headers, "Referer": sogou_link},
                allow_redirects=False,
                timeout=15,
            )
            loc2 = resp2.headers.get("Location")
            if loc2 and loc2.startswith(("http://mp.weixin.qq.com/", "https://mp.weixin.qq.com/")):
                return loc2
            return location
    except requests.RequestException:
        pass
    return sogou_link


def _one_vol(node: Tag | None) -> str | None:
    if node is None:
        return None
    return node_text(node.select_one(".one-titulo, span.text-muted, .titulo"))


def _parse_one_date(dom: str | None, may: str | None) -> dt.datetime | None:
    if not dom or not may:
        return None
    try:
        parsed = dt.datetime.strptime(f"{dom} {may}", "%d %b %Y")
    except ValueError:
        return None
    # ONE 的日期是北京时间，直接按 +08:00 解析。
    return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))


def parse_wufazhuce_one(source: dict, session: requests.Session) -> list[dict]:
    section = source.get("section", "article")
    resp = session.get(ONE_SITE, headers={"User-Agent": USER_AGENT}, timeout=source.get("timeout", 20))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    # 每日一图轮播里包含 VOL 编号和日期，文章/问题按 VOL 取对应日期。
    vol_dates: dict[str, dt.datetime] = {}
    for item in soup.select("#carousel-one .item"):
        vol = _one_vol(item)
        date = _parse_one_date(node_text(item.select_one(".dom")), node_text(item.select_one(".may")))
        if vol and date:
            vol_dates[vol] = date

    if section == "photo":
        nodes = soup.select("#carousel-one .item")
        title_sel = ".fp-one-cita a"
        link_sel = ".fp-one-cita a"
        content_sel = "img.fp-one-imagen"
    elif section == "question":
        nodes = soup.select(".fp-one-cuestion .corriente, .fp-one-cuestion .pasado li")
        title_sel = "a"
        link_sel = "a"
        content_sel = None
    else:
        nodes = soup.select(".fp-one-articulo .corriente, .fp-one-articulo .pasado li")
        title_sel = "a"
        link_sel = "a"
        content_sel = None

    items: list[dict] = []
    for node in nodes:
        title_node = node.select_one(title_sel)
        link_node = node.select_one(link_sel)
        title = node_text(title_node) or node_text(link_node)
        if not title:
            continue
        link = resolve_link(link_node or title_node, ONE_SITE)
        items.append(
            {
                "title": title,
                "link": link or ONE_SITE,
                "guid": safe_guid(link, title, ONE_SITE),
                "pub_date": vol_dates.get(_one_vol(node) or ""),
                "content": node_html(node.select_one(content_sel)) if content_sel else None,
            }
        )
    return finalize_items(items, int(source.get("max_items", 50)))


def parse_wechat_sogou(source: dict, session: requests.Session) -> list[dict]:
    wechat_id = source.get("wechat_id") or source.get("url")
    if not wechat_id:
        raise ValueError("wechat_sogou source requires wechat_id")
    search_url = f"{SOGOU_HOST}/weixin"
    params = {
        "ie": "utf8",
        "s_from": "input",
        "_sug_": "n",
        "_sug_type_": "1",
        "type": "2",
        "query": wechat_id,
        "page": "1",
    }
    log(f"fetching WeChat account {wechat_id} via Sogou")
    resp = session.get(
        search_url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Cookie": SOGOU_COOKIE},
        timeout=source.get("timeout", 20),
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    items: list[dict] = []
    for li in soup.select("ul.news-list > li"):
        anchor = li.select_one("h3 > a")
        if not anchor or not anchor.get("href"):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        sogou_link = urljoin(SOGOU_HOST, anchor["href"].strip())
        real_link = resolve_wechat_link(session, sogou_link, search_url)
        time_node = li.select_one("span.s2 script")
        pub_date = parse_sogou_time(time_node.get_text() if time_node else None)
        items.append(
            {
                "title": title,
                "link": real_link or sogou_link,
                "guid": real_link or sogou_link,
                "pub_date": pub_date,
                "content": node_html(li.select_one("p.txt-info")),
                "author": node_text(li.select_one("span.all-time-y2")),
            }
        )

    if not items:
        page_text = soup.get_text()
        if "验证码" in page_text or "antispider" in str(soup).lower():
            raise ValueError("Sogou requires captcha, try again later")
        raise ValueError(f"Sogou returned no items for {wechat_id}")
    return finalize_items(items, int(source.get("max_items", 20)))


def parse_source(source: dict, session: requests.Session) -> list[dict]:
    if source.get("type") == "wufazhuce_one":
        return parse_wufazhuce_one(source, session)
    if source.get("type") == "wechat_sogou":
        return parse_wechat_sogou(source, session)

    url = source["url"]
    if not source.get("title_selector"):
        raise ValueError(f"{url}: missing title_selector")
    headers = {"User-Agent": source.get("user_agent", USER_AGENT)}
    timeout = source.get("timeout", 20)
    log(f"fetching {source.get('title', url)}: {url}")
    resp = session.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    item_selector = source.get("item_selector")
    if not item_selector:
        raise ValueError(f"{url}: missing item_selector")
    nodes = soup.select(item_selector)

    date_formats = source.get("date_formats", DATE_FORMATS)
    items: list[dict] = []
    for node in nodes:
        title_node = node.select_one(source["title_selector"]) if source.get("title_selector") else None
        link_node = node.select_one(source["link_selector"]) if source.get("link_selector") else None
        date_node = node.select_one(source["date_selector"]) if source.get("date_selector") else None
        content_node = node.select_one(source["content_selector"]) if source.get("content_selector") else None

        title = node_text(title_node) or node_text(link_node)
        if not title:
            continue
        link = resolve_link(link_node or title_node, url)
        pub_date = parse_date(date_node, date_formats)
        content = node_html(content_node)

        item = {
            "title": title,
            "link": link or url,
            "guid": safe_guid(link, title, url),
            "pub_date": pub_date,
            "content": content,
        }
        items.append(item)

    return finalize_items(items, int(source.get("max_items", 50)))


def render_feed(source: dict, items: list[dict], output_path: Path) -> None:
    title = source["title"]
    link = source.get("link") or source.get("url") or ""
    description = source.get("description", "")
    language = source.get("language", "zh-CN")
    now = format_datetime(dt.datetime.now(dt.timezone.utc))
    feed_url = source.get("feed_url", str(output_path))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
        f"    <link>{escape(link)}</link>",
        f"    <description>{escape(description)}</description>",
        f"    <language>{escape(language)}</language>",
        f"    <lastBuildDate>{now}</lastBuildDate>",
        f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>',
    ]
    for item in items:
        lines.append("    <item>")
        lines.append(f"      <title>{escape(item['title'])}</title>")
        lines.append(f"      <link>{escape(item['link'])}</link>")
        lines.append(f"      <guid isPermaLink=\"true\">{escape(item['guid'])}</guid>")
        if item.get("author"):
            lines.append(f"      <author>{escape(item['author'])}</author>")
        if item["pub_date"]:
            pub_date = item["pub_date"]
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=dt.timezone.utc)
            lines.append(f"      <pubDate>{format_datetime(pub_date)}</pubDate>")
        if item["content"]:
            content = item["content"].replace("]]>", "]]]]><![CDATA[>")
            lines.append(f"      <description><![CDATA[{content}]]></description>")
        lines.append("    </item>")
    lines.extend(["  </channel>", "</rss>", ""])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"wrote {output_path} ({len(items)} items)")


def load_config(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("feeds") or []
    if not sources:
        raise ValueError(f"{path}: no feeds configured")
    return sources


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned or "feed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RSS feeds from HTML pages")
    parser.add_argument("--config", default="feeds.yaml", help="YAML config file (default: feeds.yaml)")
    parser.add_argument("--output-dir", default="out", help="output directory (default: out)")
    args = parser.parse_args()

    config_path = Path(args.config)
    output_dir = Path(args.output_dir)
    sources = load_config(config_path)

    session = requests.Session()
    for source in sources:
        try:
            items = parse_source(source, session)
            name = slugify(source.get("id") or source["title"])
            output_path = output_dir / f"{name}.xml"
            render_feed(source, items, output_path)
        except Exception as exc:
            log(f"ERROR {source.get('title', source.get('url'))}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
