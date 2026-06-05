import html
import json
import os
import time
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

import feedparser


USER_AGENT = "KpKqwq-MyArxiv/1.0 (GitHub Actions; https://github.com/KpKqwq/MyArxiv)"


def load_config(path="config.toml"):
    with open(path, "rb") as f:
        return tomllib.load(f)


def normalize_text(s):
    return " ".join((s or "").split())


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def get_authors(entry):
    authors = []

    for author in entry.get("authors", []) or []:
        name = author.get("name")
        if name:
            authors.append(name)

    if not authors and entry.get("author"):
        authors.append(entry.get("author"))

    return authors


def clean_summary(entry):
    summary = entry.get("summary") or entry.get("description") or ""
    return normalize_text(summary)


def fetch_category(category, source_title):
    url = f"https://rss.arxiv.org/rss/{category}"
    print(f"Fetching RSS: {url}")

    feed = feedparser.parse(
        url,
        request_headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )

    status = getattr(feed, "status", None)
    if status and status >= 400:
        raise RuntimeError(f"RSS request failed: HTTP {status} for {url}")

    papers = []

    for entry in feed.entries:
        link = entry.get("link", "")
        title = normalize_text(entry.get("title", "Untitled"))
        summary = clean_summary(entry)
        authors = get_authors(entry)

        published_dt = parse_date(entry.get("published") or entry.get("updated"))
        updated_dt = parse_date(entry.get("updated") or entry.get("published"))

        arxiv_id = entry.get("id") or link or title

        pdf_url = link
        if "/abs/" in pdf_url:
            pdf_url = pdf_url.replace("/abs/", "/pdf/")

        papers.append(
            {
                "id": arxiv_id,
                "title": title,
                "authors": authors,
                "summary": summary,
                "published": published_dt.isoformat(),
                "updated": updated_dt.isoformat(),
                "pdf_url": pdf_url,
                "entry_url": link,
                "primary_category": category,
                "categories": [category],
                "comment": None,
                "source_title": source_title,
            }
        )

    return papers


def load_remote_cache(config):
    cache_url = config.get("cache_url")
    if not cache_url:
        return []

    print(f"Trying fallback cache: {cache_url}")

    try:
        req = urllib.request.Request(
            cache_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if isinstance(data, list):
            return data

        print("Remote cache exists but is not a list; ignoring.")
        return []

    except Exception as e:
        print(f"Fallback cache unavailable: {e}")
        return []


def fetch_papers(config):
    all_papers = {}

    for source in config.get("sources", []):
        category = source["category"]
        source_title = source.get("title", category)

        try:
            papers = fetch_category(category, source_title)
        except Exception as e:
            print(f"Failed to fetch {category}: {e}")
            continue

        for p in papers:
            key = p["id"] or p["entry_url"] or p["title"]

            old = all_papers.get(key)
            if old:
                if source_title not in old["source_title"]:
                    old["source_title"] += f", {source_title}"
                old["categories"] = sorted(set(old.get("categories", []) + [category]))
            else:
                all_papers[key] = p

        time.sleep(3.2)

    papers = sorted(
        all_papers.values(),
        key=lambda x: x.get("updated", ""),
        reverse=True,
    )

    if papers:
        return papers

    cached = load_remote_cache(config)
    if cached:
        print(f"Using fallback cache with {len(cached)} papers")
        return cached

    raise RuntimeError("No papers fetched and no usable cache found.")


def write_cache(papers, out_dir):
    with open(os.path.join(out_dir, "cache.json"), "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)


def write_rss(papers, config, out_dir):
    site_title = config.get("site_title", "MyArxiv")
    base_url = config.get("cache_url", "").replace("/cache.json", "/")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = site_title
    ET.SubElement(channel, "link").text = base_url or "https://arxiv.org/"
    ET.SubElement(channel, "description").text = "Personal arXiv paper feed"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for p in papers:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = p.get("title", "Untitled")
        ET.SubElement(item, "link").text = p.get("entry_url") or p.get("id") or ""
        ET.SubElement(item, "guid").text = p.get("id") or p.get("entry_url") or p.get("title", "")

        try:
            pub_dt = datetime.fromisoformat(p.get("updated", ""))
        except Exception:
            pub_dt = datetime.now(timezone.utc)

        ET.SubElement(item, "pubDate").text = format_datetime(pub_dt)

        authors = ", ".join(p.get("authors", []) or [])
        desc = f"""
        <p><b>Authors:</b> {html.escape(authors)}</p>
        <p><b>Category:</b> {html.escape(p.get("primary_category", ""))}</p>
        <p><b>Source:</b> {html.escape(p.get("source_title", ""))}</p>
        <p>{html.escape(p.get("summary", ""))}</p>
        <p><a href="{html.escape(p.get("pdf_url", ""))}">PDF</a></p>
        """
        ET.SubElement(item, "description").text = desc

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(
        os.path.join(out_dir, "rss.xml"),
        encoding="utf-8",
        xml_declaration=True,
    )


def write_index(papers, config, out_dir):
    site_title = html.escape(config.get("site_title", "MyArxiv"))

    rows = []

    for p in papers:
        authors = html.escape(", ".join(p.get("authors", []) or []))
        title = html.escape(p.get("title", "Untitled"))
        summary = html.escape(p.get("summary", ""))
        entry_url = html.escape(p.get("entry_url") or p.get("id") or "")
        pdf_url = html.escape(p.get("pdf_url") or entry_url)
        source_title = html.escape(p.get("source_title", ""))
        updated = html.escape((p.get("updated") or "")[:10])

        rows.append(
            f"""
        <article>
          <h2><a href="{entry_url}">{title}</a></h2>
          <div class="meta">{updated} · {source_title}</div>
          <div class="authors">{authors}</div>
          <p>{summary}</p>
          <p><a href="{pdf_url}">PDF</a></p>
        </article>
        """
        )

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{site_title}</title>
  <style>
    body {{
      max-width: 960px;
      margin: 40px auto;
      padding: 0 20px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    h1 {{ margin-bottom: 0.2em; }}
    article {{
      border-top: 1px solid #ddd;
      padding: 18px 0;
    }}
    h2 {{ font-size: 1.15rem; }}
    .meta, .authors {{
      color: #666;
      font-size: 0.9rem;
    }}
    a {{ color: #0969da; }}
  </style>
</head>
<body>
  <h1>{site_title}</h1>
  <p><a href="rss.xml">RSS</a> · Generated at {html.escape(datetime.now(timezone.utc).isoformat())}</p>
  {"".join(rows)}
</body>
</html>
"""

    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_doc)


def main():
    config = load_config()
    out_dir = "target"
    os.makedirs(out_dir, exist_ok=True)

    papers = fetch_papers(config)
    print(f"Total papers: {len(papers)}")

    write_cache(papers, out_dir)
    write_rss(papers, config, out_dir)
    write_index(papers, config, out_dir)


if __name__ == "__main__":
    main()
