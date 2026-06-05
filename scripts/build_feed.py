#!/usr/bin/env python3
"""
A MyArxiv-compatible feed builder.

It keeps the original MyArxiv page features (CSS/JS layout, title/author/conference
highlighting, ☆/★ and ♻ markers) while avoiding export.arxiv.org/api/query.
Data is fetched from official arXiv RSS category feeds.
"""

from __future__ import annotations

import calendar
import email.utils
import html
import json
import os
import re
import shutil
import time
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import feedparser


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.toml"
RHAI_CONFIG_PATH = ROOT / "scripts" / "config.rhai"
STATICS_DIR = ROOT / "statics"
OUT_DIR = ROOT / "target"

USER_AGENT = "KpKqwq-MyArxiv/2.0 (+https://github.com/KpKqwq/MyArxiv)"


@dataclass(frozen=True)
class HighlightConfig:
    title_terms: list[str]
    author_terms: list[str]
    conference_terms: list[str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").split())


def parse_struct_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    except Exception:
        return None


def parse_email_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_iso(dt: datetime | None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).isoformat()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def first_regex_group(pattern: str, text: str, flags: int = re.IGNORECASE | re.DOTALL) -> str:
    match = re.search(pattern, text, flags)
    return normalize_text(match.group(1)) if match else ""


def clean_rss_title(title: str) -> str:
    # arXiv RSS titles are commonly like: "arXiv:2501.01234v1 [cs.CL] Paper title".
    title = normalize_text(title)
    title = re.sub(r"^arXiv:\S+\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    return title or "Untitled"


def extract_authors(entry: Any, description_text: str) -> list[str]:
    authors: list[str] = []

    for author in entry.get("authors", []) or []:
        name = normalize_text(author.get("name") if isinstance(author, dict) else str(author))
        if name:
            authors.append(name)

    if not authors and entry.get("author"):
        # Some RSS readers expose dc:creator as author.
        raw = normalize_text(entry.get("author"))
        if raw:
            authors = [part.strip() for part in re.split(r",\s*|;\s*", raw) if part.strip()]

    if not authors:
        raw = first_regex_group(r"(?:^|\n)Authors?:\s*(.+?)(?:\n[A-Z][A-Za-z ]{1,24}:|$)", description_text)
        if raw:
            authors = [part.strip() for part in re.split(r",\s*|;\s*", raw) if part.strip()]

    return authors


def extract_comment(description_text: str) -> str | None:
    comment = first_regex_group(r"(?:^|\n)Comments?:\s*(.+?)(?:\n[A-Z][A-Za-z ]{1,24}:|$)", description_text)
    return comment or None


def extract_summary(entry: Any, description_text: str) -> str:
    # Prefer an explicit Abstract field if present; otherwise remove metadata-like lines.
    abstract = first_regex_group(r"(?:^|\n)Abstract:\s*(.+?)(?:\n[A-Z][A-Za-z ]{1,24}:|$)", description_text)
    if abstract:
        return abstract

    lines = []
    for line in description_text.splitlines():
        if re.match(r"^(Title|Authors?|Comments?|Subjects?|Journal-ref|Report-no|ACM-class|MSC-class):\s*", line, re.I):
            continue
        lines.append(line)

    summary = normalize_text(" ".join(lines))
    if not summary:
        summary = normalize_text(html_to_text(entry.get("summary") or entry.get("description") or ""))
    return summary


def strip_rhai_line_comments(text: str) -> str:
    # Good enough for this config file: strings do not contain // URLs.
    return re.sub(r"//.*?(?=\n|$)", "", text)


def extract_rhai_array(text: str, name: str) -> list[str]:
    text = strip_rhai_line_comments(text)
    match = re.search(rf"let\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*;", text, re.S)
    if not match:
        return []
    body = match.group(1)
    return re.findall(r'"((?:\\.|[^"\\])*)"', body)


def load_highlight_config(path: Path = RHAI_CONFIG_PATH) -> HighlightConfig:
    if not path.exists():
        return HighlightConfig([], [], [])

    text = path.read_text(encoding="utf-8")

    titles_type = extract_rhai_array(text, "titles_type")
    titles_model = extract_rhai_array(text, "titles_model")
    titles_method = extract_rhai_array(text, "titles_method")
    authors = extract_rhai_array(text, "authors_array")
    conferences = extract_rhai_array(text, "conferences")

    title_terms = titles_model + titles_method + titles_type
    return HighlightConfig(
        title_terms=[x for x in title_terms if x],
        author_terms=[x for x in authors if x],
        conference_terms=[x for x in conferences if x],
    )


def compile_terms(terms: Iterable[str], *, suffix: str = "") -> re.Pattern[str] | None:
    clean = sorted({t for t in terms if t}, key=len, reverse=True)
    if not clean:
        return None
    pattern = "|".join(re.escape(t) for t in clean)
    return re.compile(rf"({pattern}){suffix}", re.IGNORECASE)


def highlight_plain_text(text: str, regex: re.Pattern[str] | None, class_name: str) -> str:
    if not regex or not text:
        return html.escape(text or "")

    out: list[str] = []
    pos = 0
    for match in regex.finditer(text):
        start, end = match.span()
        if start < pos:
            continue
        out.append(html.escape(text[pos:start]))
        out.append(f'<span class="{class_name}">{html.escape(text[start:end])}</span>')
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)


def make_highlighters(config: HighlightConfig):
    title_rg = compile_terms(config.title_terms)
    author_rg = compile_terms(config.author_terms)
    conf_rg = compile_terms(config.conference_terms, suffix=r"[\s'\-]*\d*")

    def author_is_highlighted(authors: list[str]) -> bool:
        return bool(author_rg and any(author_rg.search(a or "") for a in authors))

    def highlight_title(title: str, authors: list[str]) -> str:
        prefix = "★" if author_is_highlighted(authors) else "☆"
        return prefix + " " + highlight_plain_text(title or "", title_rg, "highlight-title")

    def highlight_author(authors: list[str]) -> str:
        return ", ".join(highlight_plain_text(a or "", author_rg, "highlight-author") for a in authors)

    def highlight_conference(comment: str | None) -> str:
        if not comment or not conf_rg:
            return ""
        match = conf_rg.search(comment)
        if not match:
            return ""
        return f'<span class="chip">{html.escape(match.group(0).strip())}</span>'

    return highlight_title, highlight_author, highlight_conference


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def fetch_rss_source(category: str, source_title: str, limit: int) -> list[dict[str, Any]]:
    url = f"https://rss.arxiv.org/rss/{category}"
    print(f"Fetching {url}")

    feed = feedparser.parse(
        url,
        request_headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )

    status = getattr(feed, "status", None)
    if status and int(status) >= 400:
        raise RuntimeError(f"HTTP {status} from {url}")

    papers: list[dict[str, Any]] = []
    entries = list(feed.entries or [])[: max(0, limit)]

    for entry in entries:
        description_text = html_to_text(entry.get("summary") or entry.get("description") or "")
        link = entry.get("link") or entry.get("id") or ""
        arxiv_id = entry.get("id") or link
        title = clean_rss_title(entry.get("title", ""))
        authors = extract_authors(entry, description_text)
        comment = extract_comment(description_text)
        summary = extract_summary(entry, description_text)

        published = (
            parse_struct_time(entry.get("published_parsed"))
            or parse_email_date(entry.get("published"))
            or parse_struct_time(entry.get("updated_parsed"))
            or parse_email_date(entry.get("updated"))
            or utc_now()
        )
        updated = (
            parse_struct_time(entry.get("updated_parsed"))
            or parse_email_date(entry.get("updated"))
            or published
        )

        pdf_url = link.replace("/abs/", "/pdf/") if link else ""

        papers.append(
            {
                "id": arxiv_id,
                "title": title,
                "authors": authors,
                "summary": summary,
                "published": format_iso(published),
                "updated": format_iso(updated),
                "pdf_url": pdf_url,
                "comment": comment,
                "subject": source_title,
                "category": category,
            }
        )

    print(f"Fetched {len(papers)} entries for {category}")
    return papers


def load_remote_cache(cache_url: str | None) -> list[dict[str, Any]]:
    if not cache_url:
        return []

    print(f"Loading existing cache: {cache_url}")
    try:
        req = urllib.request.Request(cache_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"Existing cache unavailable: {exc}")
        return []

    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]

    # A defensive fallback for other possible cache shapes.
    if isinstance(data, dict):
        if isinstance(data.get("papers"), list):
            return [p for p in data["papers"] if isinstance(p, dict)]
        if isinstance(data.get("items"), list):
            return [p for p in data["items"] if isinstance(p, dict)]

    return []


def paper_date(paper: dict[str, Any]) -> datetime:
    return (
        parse_iso_date(str(paper.get("updated") or ""))
        or parse_iso_date(str(paper.get("published") or ""))
        or utc_now()
    )


def normalize_cached_paper(paper: dict[str, Any], default_subject: str = "") -> dict[str, Any]:
    authors = paper.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r",\s*|;\s*", authors) if a.strip()]

    return {
        "id": str(paper.get("id") or paper.get("entry_url") or paper.get("link") or paper.get("title") or ""),
        "title": normalize_text(str(paper.get("title") or "Untitled")),
        "authors": [str(a) for a in authors],
        "summary": normalize_text(str(paper.get("summary") or paper.get("abstract") or "")),
        "published": str(paper.get("published") or paper.get("updated") or format_iso(utc_now())),
        "updated": str(paper.get("updated") or paper.get("published") or format_iso(utc_now())),
        "pdf_url": str(paper.get("pdf_url") or paper.get("pdf") or ""),
        "comment": paper.get("comment"),
        "subject": str(paper.get("subject") or paper.get("source_title") or default_subject or paper.get("category") or "arXiv"),
        "category": str(paper.get("category") or paper.get("primary_category") or ""),
    }


def build_papers(config: dict[str, Any]) -> list[dict[str, Any]]:
    limit_days = int(config.get("limit_days", 7))
    cutoff = utc_now() - timedelta(days=limit_days)

    cache = [normalize_cached_paper(p) for p in load_remote_cache(config.get("cache_url"))]
    fetched: list[dict[str, Any]] = []

    for source in config.get("sources", []):
        category = source["category"]
        source_title = source.get("title") or category
        limit = max(1, int(source.get("limit", 150)))

        try:
            fetched.extend(fetch_rss_source(category, source_title, limit))
        except Exception as exc:
            print(f"Failed to fetch {category}: {exc}")

        time.sleep(3.2)

    merged: dict[str, dict[str, Any]] = {}
    for paper in cache + fetched:
        paper = normalize_cached_paper(paper)
        if paper_date(paper) < cutoff:
            continue
        # Keep per-subject rows so the same arXiv item can appear under different configured subjects.
        key = f"{paper.get('id')}|{paper.get('subject')}"
        merged[key] = paper

    papers = sorted(merged.values(), key=paper_date, reverse=True)
    if not papers:
        raise RuntimeError("No papers fetched and no usable cache remained after filtering.")

    return papers


def group_by_day_and_subject(papers: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for paper in papers:
        day = paper_date(paper).strftime("%Y-%m-%d")
        subject = str(paper.get("subject") or paper.get("category") or "arXiv")
        grouped[day][subject].append(paper)
    return grouped


def render_index(papers: list[dict[str, Any]], config: dict[str, Any], hcfg: HighlightConfig) -> str:
    highlight_title, highlight_author, highlight_conference = make_highlighters(hcfg)
    site_title = html.escape(str(config.get("site_title") or "MyArxiv"))
    build_time = utc_now()
    grouped = group_by_day_and_subject(papers)

    sections: list[str] = []
    for day in sorted(grouped.keys(), reverse=True):
        subject_html: list[str] = []
        for subject in sorted(grouped[day].keys()):
            subject_papers = grouped[day][subject]
            items: list[str] = []
            for paper in subject_papers:
                title = str(paper.get("title") or "Untitled")
                authors = paper.get("authors") or []
                if not isinstance(authors, list):
                    authors = [str(authors)]
                comment = paper.get("comment")
                updated = str(paper.get("updated") or "")
                published = str(paper.get("published") or "")
                recycle = "♻" if updated[:10] != published[:10] else ""
                link = html.escape(str(paper.get("id") or ""), quote=True)
                pdf_url = html.escape(str(paper.get("pdf_url") or ""), quote=True)
                summary = html.escape(str(paper.get("summary") or ""))

                comment_block = ""
                if comment:
                    comment_block = f'''
        <div class="article-summary-box-inner">
          <span class="chip">comment</span>: <span>{html.escape(str(comment))}</span>
        </div>'''

                items.append(f'''
      <article>
        <details class="article-expander">
          <summary class="article-expander-title">
            {recycle} {highlight_title(title, authors)} {highlight_conference(str(comment) if comment else None)}
          </summary>
          <div class="article-authors">
            <a href="{link}"><i class="ri-links-line"></i></a>
            <a href="{pdf_url}"><i class="ri-file-paper-2-line"></i></a>
            {highlight_author(authors)}
          </div>
          <div class="article-summary-box-inner">
            <span>{summary}</span>
          </div>{comment_block}
        </details>
      </article>''')

            subject_html.append(f'''
    <article>
      <details>
        <summary>{html.escape(subject)} <span class="chip" style="font-size: 60%">{len(subject_papers)}</span></summary>
        <div class="details-content">
          {''.join(items)}
        </div>
      </details>
    </article>''')

        sections.append(f'''
  <section class="day-container">
    <div class="date"><time datetime="{day}">{day}</time></div>
    {''.join(subject_html)}
  </section>''')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <title>{site_title}</title>
  <meta charset="utf-8"/>
  <meta http-equiv="X-UA-Compatible" content="IE=edge"/>
  <meta name="robots" content="noindex, nofollow"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="shortcut icon" type="image/x-icon" href="favicon.ico"/>
  <link href="index.css" rel="stylesheet"/>
  <link href="https://cdn.jsdelivr.net/npm/remixicon@2.5.0/fonts/remixicon.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.15.1/dist/katex.min.css"
        integrity="sha384-R4558gYOUz8mP9YWpZJjofhk+zx0AS11p36HnD2ZKj/6JR5z27gSSULCNHIRReVs" crossorigin="anonymous">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.15.1/dist/katex.min.js"
          integrity="sha384-z1fJDqw8ZApjGO3/unPWUPsIymfsJmyrDVWC8Tv/a1HeOtGmkwNd/7xUS0Xcnvsx" crossorigin="anonymous"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.15.1/dist/contrib/auto-render.min.js"
          integrity="sha384-+XBljXPPiv+OzfbB3cVmLHf4hdUFHlWNZN5spNQ7rmHTXpd7WvJum6fIACpNNfIR" crossorigin="anonymous"></script>
  <script>
    document.addEventListener("DOMContentLoaded", function () {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: '$$', right: '$$', display: true}},
          {{left: '$', right: '$', display: false}},
          {{left: '\\(', right: '\\)', display: false}},
          {{left: '\\[', right: '\\]', display: true}},
          {{left: "\\begin{{equation}}", right: "\\end{{equation}}", display: true}},
          {{left: "\\begin{{align}}", right: "\\end{{align}}", display: true}},
          {{left: "\\begin{{alignat}}", right: "\\end{{alignat}}", display: true}},
          {{left: "\\begin{{gather}}", right: "\\end{{gather}}", display: true}},
          {{left: "\\begin{{CD}}", right: "\\end{{CD}}", display: true}},
        ],
        throwOnError: false
      }});
    }});
  </script>
</head>
<body>
<section class="header-container">
  <div style="display:flex; justify-content:space-between; align-items:flex-end;">
    <div><div class="header-title">{site_title}</div></div>
    <div class="icons">
      <label class="theme-switch" for="checkbox">
        <input type="checkbox" id="checkbox"/>
        <i id="theme-icon" class="ri-moon-line" style="font-size: 32px" rel="noopener noreferrer"></i>
      </label>
    </div>
  </div>
</section>
{''.join(sections)}
</body>
<footer>
  <div>
    <time id="build-timestamp" datetime="{build_time.isoformat()}">{build_time.strftime('%Y-%m-%d %H:%M:%S UTC')}</time>
  </div>
</footer>
<script src="index.js"></script>
</html>
'''


def write_rss(papers: list[dict[str, Any]], config: dict[str, Any]) -> None:
    site_title = str(config.get("site_title") or "MyArxiv")
    base_url = str(config.get("cache_url") or "").replace("/cache.json", "/") or "https://arxiv.org/"

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = site_title
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = "Personal arXiv paper feed"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(utc_now())

    for paper in papers:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = str(paper.get("title") or "Untitled")
        ET.SubElement(item, "link").text = str(paper.get("id") or "")
        ET.SubElement(item, "guid").text = str(paper.get("id") or paper.get("title") or "")
        ET.SubElement(item, "pubDate").text = email.utils.format_datetime(paper_date(paper))

        authors = ", ".join(paper.get("authors") or [])
        desc = (
            f"<p><b>Authors:</b> {html.escape(authors)}</p>"
            f"<p><b>Subject:</b> {html.escape(str(paper.get('subject') or ''))}</p>"
            f"<p>{html.escape(str(paper.get('summary') or ''))}</p>"
            f"<p><a href=\"{html.escape(str(paper.get('pdf_url') or ''))}\">PDF</a></p>"
        )
        if paper.get("comment"):
            desc += f"<p><b>Comment:</b> {html.escape(str(paper.get('comment')))}</p>"
        ET.SubElement(item, "description").text = desc

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(OUT_DIR / "rss.xml", encoding="utf-8", xml_declaration=True)


def copy_static_assets() -> None:
    if STATICS_DIR.exists():
        for path in STATICS_DIR.iterdir():
            if path.is_file():
                shutil.copy2(path, OUT_DIR / path.name)

    for name in ["favicon.ico"]:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, OUT_DIR / name)


def main() -> None:
    config = load_config()
    hcfg = load_highlight_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    papers = build_papers(config)
    print(f"Total papers after merge/filter: {len(papers)}")
    print(f"Highlight terms: titles={len(hcfg.title_terms)}, authors={len(hcfg.author_terms)}, conferences={len(hcfg.conference_terms)}")

    (OUT_DIR / "cache.json").write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "index.html").write_text(render_index(papers, config, hcfg), encoding="utf-8")
    write_rss(papers, config)
    copy_static_assets()

    print("Generated target/index.html, target/cache.json, target/rss.xml")


if __name__ == "__main__":
    main()
