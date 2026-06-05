import html
import json
import os
import time
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

import arxiv


def load_config(path="config.toml"):
    with open(path, "rb") as f:
        return tomllib.load(f)


def normalize_text(s):
    return " ".join((s or "").split())


def paper_to_dict(paper, source_title):
    authors = [str(a) for a in paper.authors]
    published = paper.published.astimezone(timezone.utc)
    updated = paper.updated.astimezone(timezone.utc)

    return {
        "id": paper.entry_id,
        "title": normalize_text(paper.title),
        "authors": authors,
        "summary": normalize_text(paper.summary),
        "published": published.isoformat(),
        "updated": updated.isoformat(),
        "pdf_url": paper.pdf_url,
        "entry_url": paper.entry_id,
        "primary_category": getattr(paper, "primary_category", ""),
        "categories": list(getattr(paper, "categories", []) or []),
        "comment": getattr(paper, "comment", None),
        "source_title": source_title,
    }


def fetch_papers(config):
    limit_days = int(config.get("limit_days", 7))
    cutoff = datetime.now(timezone.utc) - timedelta(days=limit_days)

    client = arxiv.Client(
        page_size=100,
        delay_seconds=3.2,
        num_retries=5,
    )

    all_papers = {}
    sources = config.get("sources", [])

    for source in sources:
        category = source["category"]
        source_title = source.get("title", category)

        # 避免一次拉 4000/1000 导致大量分页请求。
        # 对个人日报/周报来说，300 通常已经足够；需要更多可以改成 500。
        max_results = min(int(source.get("limit", 100)), 300)

        print(f"Fetching {category}, max_results={max_results}")

        search = arxiv.Search(
            query=f"cat:{category}",
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        for paper in client.results(search):
            updated = paper.updated.astimezone(timezone.utc)
            published = paper.published.astimezone(timezone.utc)

            # arXiv 有些文章是 replace/update；这里 published/updated 二者命中任一即可保留。
            if published < cutoff and updated < cutoff:
                continue

            item = paper_to_dict(paper, source_title)

            # 多个 category 命中同一篇论文时去重。
            old = all_papers.get(item["id"])
            if old:
                if source_title not in old["source_title"]:
                    old["source_title"] += f", {source_title}"
            else:
                all_papers[item["id"]] = item

        # 多 source 之间再稍微让一下，避免撞 API。
        time.sleep(3.2)

    papers = sorted(
        all_papers.values(),
        key=lambda x: x["updated"],
        reverse=True,
    )
    return papers


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
        ET.SubElement(item, "title").text = p["title"]
        ET.SubElement(item, "link").text = p["entry_url"]
        ET.SubElement(item, "guid").text = p["id"]
        ET.SubElement(item, "pubDate").text = format_datetime(
            datetime.fromisoformat(p["updated"])
        )

        authors = ", ".join(p["authors"])
        desc = f"""
        <p><b>Authors:</b> {html.escape(authors)}</p>
        <p><b>Category:</b> {html.escape(p.get("primary_category", ""))}</p>
        <p><b>Source:</b> {html.escape(p.get("source_title", ""))}</p>
        <p>{html.escape(p["summary"])}</p>
        <p><a href="{html.escape(p["pdf_url"])}">PDF</a></p>
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
        authors = html.escape(", ".join(p["authors"]))
        title = html.escape(p["title"])
        summary = html.escape(p["summary"])
        entry_url = html.escape(p["entry_url"])
        pdf_url = html.escape(p["pdf_url"])
        source_title = html.escape(p.get("source_title", ""))
        updated = html.escape(p["updated"][:10])

        rows.append(f"""
        <article>
          <h2><a href="{entry_url}">{title}</a></h2>
          <div class="meta">{updated} · {source_title}</div>
          <div class="authors">{authors}</div>
          <p>{summary}</p>
          <p><a href="{pdf_url}">PDF</a></p>
        </article>
        """)

    body = "\n".join(rows)

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
  {body}
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
    print(f"Fetched {len(papers)} papers")

    write_cache(papers, out_dir)
    write_rss(papers, config, out_dir)
    write_index(papers, config, out_dir)


if __name__ == "__main__":
    main()
