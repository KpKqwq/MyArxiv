import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    # Chunk generation is pure, so its regression tests do not need fetch deps.
    sys.modules.setdefault("feedparser", types.SimpleNamespace())
    bs4 = types.ModuleType("bs4")
    bs4.BeautifulSoup = object
    sys.modules.setdefault("bs4", bs4)

    spec = importlib.util.spec_from_file_location(
        "build_feed", ROOT / "scripts" / "build_feed.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PaperChunkTests(unittest.TestCase):
    def test_catalog_keeps_papers_out_of_index_and_splits_large_groups(self):
        builder = load_builder()
        papers = [
            {
                "id": f"https://arxiv.org/abs/2601.{index:05d}",
                "title": f"Paper {index}",
                "authors": ["Ada Lovelace"],
                "summary": "A summary",
                "published": "2026-09-20T00:00:00+00:00",
                "updated": "2026-09-20T00:00:00+00:00",
                "pdf_url": f"https://arxiv.org/pdf/2601.{index:05d}",
                "comment": None,
                "subject": "Computation and Language",
                "category": "cs.CL",
            }
            for index in range(5)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            catalog = builder.write_paper_chunks(papers, output_dir, page_size=2)
            index_html = builder.render_index(catalog, {"site_title": "MyArxiv"})

            self.assertNotIn("Paper 0", index_html)
            self.assertNotIn("article-expander", index_html)
            group = catalog["days"][0]["subjects"][0]
            self.assertEqual(group["paper_count"], 5)
            self.assertEqual(len(group["pages"]), 3)

            for page in group["pages"]:
                chunk = output_dir.joinpath(page["url"]).read_text(encoding="utf-8")
                self.assertLessEqual(len(__import__("json").loads(chunk)), 2)


if __name__ == "__main__":
    unittest.main()
