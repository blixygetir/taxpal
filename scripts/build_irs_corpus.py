"""Pulls the IRS pubs we cite and chunks them into src/taxpal/rules/irs_corpus.jsonl.
Each chunk keeps its section heading and a link to that section on irs.gov.
(IRS pubs are public domain.)

    uv run python scripts/build_irs_corpus.py [--cache DIR]
"""

import argparse
import hashlib
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

DOCS = {
    "i1040sc": "instructions/i1040sc",   # Schedule C instructions
    "i1040sse": "instructions/i1040sse",  # Schedule SE instructions
    "i8995": "instructions/i8995",        # QBI (simplified)
    "p334": "publications/p334",          # Tax Guide for Small Business
    "p463": "publications/p463",          # Travel, Gift, and Car Expenses
    "p587": "publications/p587",          # Business Use of Your Home
    "p583": "publications/p583",          # Starting a Business and Keeping Records
    "p505": "publications/p505",          # Tax Withholding and Estimated Tax
    "p946": "publications/p946",          # How To Depreciate Property
    "p560": "publications/p560",          # Retirement Plans for Small Business
}
OUT = Path(__file__).resolve().parent.parent / "src" / "taxpal" / "rules" / "irs_corpus.jsonl"
MAX_WORDS = 220   # embedding model caps at 512 tokens
MIN_WORDS = 25
STOP_MARKERS = ("Page Last Reviewed or Updated",)
CALLOUTS = {"Note", "Caution", "Tip", "Reminder", "Example", "Examples"}  # these show up as headings but aren't sections
# index pages, help boilerplate, blank worksheets etc. - not useful for search
NOISE_SECTIONS = re.compile(r"\b(index|how to get tax help|taxpayer bill of rights|taxpayer advocate|comments and suggestions|"
                            r"ordering forms|getting answers|principal business or professional activity codes|"
                            r"what's new for 202\d and later|future developments)\b", re.I)
BLOCK_TAGS = {"p", "li", "td", "th", "tr", "div", "dd", "dt", "caption"}


class Sections(HTMLParser):
    """Pulls (headings, anchor, text) sections out of an irs.gov pub page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[dict] = []
        self.path: dict[int, str] = {}
        self.in_heading: int | None = None
        self.heading_text: list[str] = []
        self.heading_anchor: str | None = None
        self.current: dict | None = None
        self.skip = 0
        self.stopped = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "nav", "noscript"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5") and "title" in (a.get("class") or "").split():
            self.in_heading, self.heading_text, self.heading_anchor = int(tag[1]), [], a.get("id")
        elif tag == "a" and self.in_heading and a.get("name") and not self.heading_anchor:
            self.heading_anchor = a["name"]
        elif tag in BLOCK_TAGS and self.current is not None:
            self.current["text"].append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "noscript"):
            self.skip = max(self.skip - 1, 0)
        elif self.in_heading and tag == f"h{self.in_heading}":
            title = " ".join("".join(self.heading_text).split())
            level = self.in_heading
            self.in_heading = None
            if not title:
                return
            if title.rstrip(":.") in CALLOUTS:
                if self.current is not None:
                    self.current["text"].append(f"\n{title} ")
                return
            self.path = {k: v for k, v in self.path.items() if k < level}
            self.path[level] = title
            self.current = {"heading": [self.path[k] for k in sorted(self.path)], "anchor": self.heading_anchor, "text": []}
            self.sections.append(self.current)

    def handle_data(self, data):
        if self.skip or self.stopped:
            return
        if any(m in data for m in STOP_MARKERS):
            self.stopped = True
            return
        if self.in_heading:
            self.heading_text.append(data)
        elif self.current is not None:
            self.current["text"].append(data)


def fetch(slug: str, cache: Path) -> str:
    path = cache / f"{slug}.html"
    if not path.exists():
        req = urllib.request.Request(f"https://www.irs.gov/{DOCS[slug]}", headers={"User-Agent": "Mozilla/5.0 (TaxPal corpus builder)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8", errors="replace")


def split_words(text: str) -> list[str]:
    paras = [" ".join(p.split()) for p in text.split("\n")]
    chunks, cur = [], []
    for p in filter(None, paras):
        words = p.split()
        while len(words) > MAX_WORDS:  # very long paragraph
            if cur:
                chunks.append(" ".join(cur)); cur = []
            chunks.append(" ".join(words[:MAX_WORDS])); words = words[MAX_WORDS:]
        if len(cur) + len(words) > MAX_WORDS:
            chunks.append(" ".join(cur)); cur = []
        cur += words
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def is_noise(section: str, text: str) -> bool:
    if NOISE_SECTIONS.search(section):
        return True
    blanks = text.count("_") + text.count("□")
    digits = sum(ch.isdigit() for ch in text)
    return blanks > 40 or digits > 0.35 * len(text)  # worksheets / number tables


def build(cache: Path) -> list[dict]:
    out = []
    for slug in DOCS:
        html = fetch(slug, cache)
        title = re.search(r"<title>(.*?)\s*\|", html, re.S)
        doc_title = " ".join(title.group(1).split()) if title else slug
        revision = (re.search(r"\((\d{4})\)", doc_title) or re.search(r"(\d{4})", doc_title))
        parser = Sections()
        parser.feed(html)
        n = 0
        for sec in parser.sections:
            text = "".join(sec["text"])
            heading = [h for h in sec["heading"] if h != doc_title]
            section = " > ".join(heading[-2:]) or doc_title
            for part in split_words(text):
                if len(part.split()) < MIN_WORDS or is_noise(" ".join(heading), part):
                    continue
                n += 1
                out.append({
                    "id": f"{slug}-{n:04d}",
                    "doc": slug,
                    "doc_title": doc_title,
                    "revision": revision.group(1) if revision else None,
                    "section": section,
                    "url": f"https://www.irs.gov/{DOCS[slug]}" + (f"#{sec['anchor']}" if sec["anchor"] else ""),
                    "text": part,
                })
        print(f"{slug:9} {n:5} chunks  {doc_title}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path(".irs_cache"))
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    chunks = build(args.cache)
    body = "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n"
    OUT.write_text(body, encoding="utf-8")
    print(f"{len(chunks)} chunks, {len(body) / 1e6:.1f} MB, sha256 {hashlib.sha256(body.encode()).hexdigest()[:12]} -> {OUT}")


if __name__ == "__main__":
    main()
