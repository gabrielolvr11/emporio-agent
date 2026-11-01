"""Build the submission PDF from the README and the project documentation.

Renders the Markdown documents to one HTML file (Mermaid diagrams included) and prints it to PDF
with a headless Chromium browser (Edge or Chrome). Relative links point to the GitHub repository.

Usage: uv run python scripts/build_pdf.py [--browser PATH] [--out PATH]
"""

import argparse
import html
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/gabrielolvr11/emporio-agent"
AUTHOR = "Gabriel Oliveira Souza"
ROLE = "Engenheiro(a) de IA – Full-Stack para Aplicações com IA Generativa"
OUT = ROOT / "dist" / f"{AUTHOR} - {ROLE}.pdf"
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "msedge",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
CSS = """
@page { size: A4; margin: 16mm 15mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.5;
  color: #1f2d3a; }
h1, h2, h3, h4 { color: #2c3e50; line-height: 1.25; break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 3px solid #f39c12; padding-bottom: 4px; }
h2 { font-size: 14.5pt; margin-top: 22px; border-bottom: 1px solid #d9e1ea; padding-bottom: 2px; }
h3 { font-size: 12pt; }
a { color: #1a5276; text-decoration: none; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: #f1f4f7;
  padding: 0 3px; border-radius: 3px; }
pre { background: #f1f4f7; padding: 8px 10px; border-radius: 6px; white-space: pre-wrap;
  word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; }
pre.mermaid { background: #fff; text-align: center; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; }
th, td { border: 1px solid #d9e1ea; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef2f6; }
tr, img, blockquote { break-inside: avoid; }
blockquote { margin: 6px 0; padding: 2px 10px; border-left: 3px solid #f39c12; color: #45576a; }
img { max-width: 100%; border: 1px solid #d9e1ea; border-radius: 6px; }
section.doc { break-before: page; }
.cover { padding-top: 70mm; }
.cover h1 { font-size: 26pt; border: none; margin-bottom: 4px; }
.cover .band { height: 6px; width: 120px; background: #f39c12; margin: 10px 0 18px; }
.cover p { font-size: 12pt; margin: 4px 0; }
.cover .role { font-size: 13pt; color: #1a5276; }
.toc li { margin: 3px 0; }
.source { color: #7f8c8d; font-size: 8.5pt; margin: 0 0 4px; }
"""


@dataclass(frozen=True)
class Doc:
    path: Path
    """The Markdown file to render."""
    repo_path: str
    """Where the document lives in the repository: its relative links and images resolve
    from there, and the source line points to it."""


def repository_docs() -> list[Doc]:
    names = [
        "README.md",
        "docs/design/requirements-and-plan.md",
        "docs/data-quality-report.md",
        "data/processed/policies/README.md",
        *sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("examples/conversations/*.md")),
    ]
    return [Doc(ROOT / name, name) for name in names]


def slug(text: str) -> str:
    """GitHub-style heading anchor."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def renderer() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")

    def heading_open(self, tokens, idx, options, env):
        tokens[idx].attrSet("id", slug(tokens[idx + 1].content))
        return self.renderToken(tokens, idx, options, env)

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        if token.info.strip() == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        return f"<pre><code>{html.escape(token.content)}</code></pre>\n"

    md.add_render_rule("heading_open", heading_open)
    md.add_render_rule("fence", fence)
    return md


def fix_links(body: str, repo_path: str) -> str:
    """Relative links go to GitHub; relative images are loaded from disk."""
    base = posixpath.dirname(repo_path)

    def target(path: str) -> str:
        return posixpath.normpath(posixpath.join(base, path))

    def link(match: re.Match) -> str:
        href = html.unescape(match.group(1))
        if href.startswith(("http", "#", "mailto:")):
            return match.group(0)
        path, _, anchor = href.partition("#")
        url = f"{REPO_URL}/blob/main/{target(path)}" + (f"#{anchor}" if anchor else "")
        return f'href="{html.escape(url)}"'

    def image(match: re.Match) -> str:
        src = html.unescape(match.group(1))
        if src.startswith("http"):
            return match.group(0)
        return f'src="{(ROOT / target(src)).as_uri()}"'

    body = re.sub(r'href="([^"]+)"', link, body)
    return re.sub(r'src="([^"]+)"', image, body)


def cover(title: str, lines: list[str], docs: list[Doc]) -> str:
    toc = "".join(f"<li><code>{html.escape(doc.repo_path)}</code></li>" for doc in docs)
    paragraphs = "".join(f"<p>{line}</p>" for line in lines)
    return (
        f"<div class='cover'><h1>{html.escape(title)}</h1><div class='band'></div>"
        f"<p><strong>{html.escape(AUTHOR)}</strong></p>"
        f"<p class='role'>{html.escape(ROLE)}</p>"
        f"{paragraphs}<ol class='toc'>{toc}</ol></div>"
    )


def build_html(docs: list[Doc], cover_html: str, source_label: str, lang: str) -> str:
    md = renderer()
    sections = []
    for doc in docs:
        body = fix_links(md.render(doc.path.read_text(encoding="utf-8")), doc.repo_path)
        url = f"{REPO_URL}/blob/main/{doc.repo_path}"
        source = f'<p class="source">{source_label}: <a href="{url}">{doc.repo_path}</a></p>'
        sections.append(f'<section class="doc">{source}{body}</section>')
    return (
        f"<!DOCTYPE html><html lang='{lang}'><head><meta charset='utf-8'>"
        f"<title>{html.escape(AUTHOR)} - {html.escape(ROLE)}</title>"
        f"<style>{CSS}</style>"
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>'
        "<script>mermaid.initialize({startOnLoad: true, theme: 'neutral'});</script>"
        f"</head><body>{cover_html}{''.join(sections)}</body></html>"
    )


def find_browser(given: str | None) -> str:
    for candidate in [given] if given else BROWSERS:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    sys.exit("No Edge or Chrome found: pass --browser PATH")


def print_pdf(page_html: str, out: Path, browser: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "submission.html"
        page.write_text(page_html, encoding="utf-8")
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--virtual-time-budget=20000",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="path to Edge, Chrome or Chromium")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    docs = repository_docs()
    cover_html = cover(
        "Empório da Música — Customer Service Agent",
        [
            "Artefact technical challenge · Written case submission",
            f'Repository: <a href="{REPO_URL}">{REPO_URL}</a>',
            f"Generated on {date.today():%Y-%m-%d} from the repository's README and documentation:",
        ],
        docs,
    )
    print_pdf(build_html(docs, cover_html, "Source", "en"), args.out, find_browser(args.browser))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
