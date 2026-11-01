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
from datetime import date
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/gabrielolvr11/emporio-agent"
OUT = ROOT / "dist" / "Gabriel Oliveira Souza - AI Engineer.pdf"
DOCUMENTS = [
    "README.md",
    "docs/design/requirements-and-plan.md",
    "docs/data-quality-report.md",
    "data/processed/policies/README.md",
    *sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("examples/conversations/*.md")),
]
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
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt;
  break-inside: auto; }
th, td { border: 1px solid #d9e1ea; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef2f6; }
tr { break-inside: avoid; }
blockquote { margin: 6px 0; padding: 2px 10px; border-left: 3px solid #f39c12; color: #45576a; }
img { max-width: 100%; border: 1px solid #d9e1ea; border-radius: 6px; }
section.doc { break-before: page; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; }
.cover h1 { font-size: 26pt; border: none; margin-bottom: 4px; }
.cover .band { height: 6px; width: 120px; background: #f39c12; margin: 10px 0 18px; }
.cover p { font-size: 12pt; margin: 4px 0; }
.toc li { margin: 3px 0; }
.source { color: #7f8c8d; font-size: 8.5pt; margin-top: -6px; }
"""


def slug(text: str) -> str:
    """GitHub-style heading anchor."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def renderer() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")

    def heading_open(self, tokens, idx, options, env):
        inline = tokens[idx + 1]
        tokens[idx].attrSet("id", slug(inline.content))
        return self.renderToken(tokens, idx, options, env)

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        if token.info.strip() == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        return f"<pre><code>{html.escape(token.content)}</code></pre>\n"

    md.add_render_rule("heading_open", heading_open)
    md.add_render_rule("fence", fence)
    return md


def fix_links(body: str, doc: str) -> str:
    """Relative links go to GitHub; relative images are loaded from disk."""
    base = posixpath.dirname(doc)

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


def build_html() -> str:
    md = renderer()
    toc = "".join(f"<li><code>{html.escape(doc)}</code></li>" for doc in DOCUMENTS)
    sections = []
    for doc in DOCUMENTS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        body = fix_links(md.render(text), doc)
        source = f'<p class="source">Source: <a href="{REPO_URL}/blob/main/{doc}">{doc}</a></p>'
        sections.append(f'<section class="doc">{source}{body}</section>')
    cover = (
        '<div class="cover">'
        "<h1>Empório da Música — Customer Service Agent</h1><div class='band'></div>"
        "<p><strong>Gabriel Oliveira Souza</strong> · AI Engineer</p>"
        "<p>Artefact technical challenge · Written case submission</p>"
        f'<p>Repository: <a href="{REPO_URL}">{REPO_URL}</a></p>'
        f"<p>Generated on {date.today():%Y-%m-%d} from the repository's README and "
        "documentation:</p>"
        f'<ol class="toc">{toc}</ol>'
        "</div>"
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Gabriel Oliveira Souza - AI Engineer</title>"
        f"<style>{CSS}</style>"
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>'
        "<script>mermaid.initialize({startOnLoad: true, theme: 'neutral'});</script>"
        f"</head><body>{cover}{''.join(sections)}</body></html>"
    )


def find_browser(given: str | None) -> str:
    for candidate in [given] if given else BROWSERS:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    sys.exit("No Edge or Chrome found: pass --browser PATH")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="path to Edge, Chrome or Chromium")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    browser = find_browser(args.browser)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "submission.html"
        page.write_text(build_html(), encoding="utf-8")
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--virtual-time-budget=20000",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={args.out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
