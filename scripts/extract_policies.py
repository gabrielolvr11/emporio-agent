"""Extract the store manual into one Markdown file per topic, served by get_policy(topic).

pypdf's layout mode keeps each table row on one line, with columns separated by runs of
spaces. This script splits the text into the manual's numbered sections, turns table rows into
Markdown tables, joins wrapped lines, and applies CORRECTIONS where the manual contradicts
itself. It fails if the manual's structure or a corrected passage is not found.

Usage: uv run python scripts/extract_policies.py
"""

import re
from pathlib import Path

from pypdf import PdfReader

from emporio_agent.policies import POLICY_DIR, TOPICS

PDF = Path(__file__).resolve().parents[1] / "data" / "raw" / "políticas_da_loja.pdf"

# topic -> (title, manual sections). Sections 7.1-7.3 (service guidelines) and 10 (final
# provisions) are instructions for the attendant: they are written into the system prompt
# instead. Section 7's introduction gives the WhatsApp number, so it belongs to store_info.
TOPIC_SECTIONS: dict[str, tuple[str, list[str]]] = {
    "store_info": ("Sobre a loja e contatos", ["1", "1.1", "1.2", "7"]),
    "hours": ("Horário de funcionamento", ["2"]),
    "payment": ("Formas de pagamento", ["3", "3.1"]),
    "returns": ("Trocas e devoluções", ["4", "4.1", "4.2", "4.3", "4.4"]),
    "shipping": ("Frete e entregas", ["5", "5.1", "5.2", "5.3"]),
    "promotions": ("Promoções e descontos", ["6", "6.1", "6.2"]),
    "warranty": ("Garantia", ["8", "8.1", "8.2", "8.3"]),
    "privacy": ("Privacidade e proteção de dados", ["9"]),
}
EXPECTED_SECTIONS = [
    "1", "1.1", "1.2", "2", "3", "3.1", "4", "4.1", "4.2", "4.3", "4.4", "5", "5.1", "5.2",
    "5.3", "6", "6.1", "6.2", "7", "7.1", "7.2", "7.3", "8", "8.1", "8.2", "8.3", "9", "10",
]  # fmt: skip

# Where the manual contradicts itself: (section, extracted text, corrected text, reason).
CORRECTIONS = [
    (
        "1.2",
        "| Telefone / WhatsApp | (67) 3341-4444 |",
        "| Telefone | (67) 3341-4444 |\n| WhatsApp | (67) 3321-4500 |",
        "Section 7 names (67) 3321-4500 as the WhatsApp channel; (67) 3341-4444 stays as the "
        "store phone.",
    ),
    (
        "1.2",
        "contato@empóriodamusica.com.br",
        "contato@emporiodamusica.com.br",
        "The cover page spells the e-mail domain without the accent.",
    ),
    (
        "3",
        "Parcela mínima de R$ 100,00",
        "Parcela mínima conforme o número de parcelas (ver 3.1)",
        "Section 3.1 sets the minimum installment by number of installments (R$ 50, R$ 80, "
        "R$ 100); the table gives only the 7x-12x minimum.",
    ),
    (
        "3.1",
        "sem valor mínimo de parcela (exceto abaixo de R$ 50,00)",
        "parcela mínima de R$ 50,00",
        'The original ("no minimum, except below R$ 50,00") contradicts itself; read as a '
        "R$ 50,00 minimum.",
    ),
]

PAGE_HEADER = re.compile(r"^Empório da Música\s+Manual de Políticas e Procedimentos$")
PAGE_FOOTER = re.compile(r"^\s*Página \d+$")
HEADING = re.compile(r"^(\d{1,2}\.\d|\d{1,2}\.)\s+([A-ZÀ-Ú].*)$")  # "3. Formas...", "3.1 Regras..."
LIST_ITEM = re.compile(r"^\s+(•|\d+\.)\s{2,}(.*)$")
CELL_GAP = re.compile(r"\s{3,}")
WRAPPED = 85  # a body line this long, or without final punctuation, continues on the next


def read_body_lines(reader: PdfReader) -> list[str]:
    """Text lines of pages 2 onwards (page 1 is the cover), without page headers and footers."""
    lines = []
    for page in reader.pages[1:]:
        for line in page.extract_text(extraction_mode="layout").splitlines():
            line = line.rstrip()
            if not PAGE_HEADER.match(line) and not PAGE_FOOTER.match(line):
                lines.append(line)
    return lines


def split_sections(lines: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Section number -> (heading line, body lines)."""
    sections: dict[str, tuple[str, list[str]]] = {}
    current: list[str] | None = None
    for line in lines:
        if match := HEADING.match(line):
            number = match.group(1).rstrip(".")
            current = []
            sections[number] = (line.strip(), current)
        elif current is not None:
            current.append(line)
    if list(sections) != EXPECTED_SECTIONS:
        raise ValueError(f"unexpected manual structure: {list(sections)}")
    return sections


def to_markdown(lines: list[str]) -> str:
    out: list[str] = []
    kind = None  # "para", "list" or "table": the block the last line belongs to
    previous = ""
    for line in lines:
        if not line.strip():
            if kind == "para":
                kind = None  # a blank line ends a paragraph; tables and lists span blank lines
            continue
        text = line.strip()
        if item := LIST_ITEM.match(line):
            marker = "-" if item.group(1) == "•" else item.group(1)
            out += ([""] if kind != "list" else []) + [f"{marker} {item.group(2).strip()}"]
            kind = "list"
        elif line.startswith(" ") and len(cells := CELL_GAP.split(text)) > 1:
            row = "| " + " | ".join(cells) + " |"
            if kind != "table":  # the first row is the header
                out += ["", row, "|" + "---|" * len(cells)]
            else:
                out.append(row)
            kind = "table"
        elif line.startswith(" ") and kind == "list":
            out[-1] += " " + text  # wrapped list item
        elif line.startswith(" ") and kind == "table":
            out[-1] = out[-1].removesuffix(" |") + " " + text + " |"  # wrapped last cell
        elif kind == "para" and (
            len(previous) >= WRAPPED or not previous.endswith((".", ":", "!", "?"))
        ):
            out[-1] += " " + text  # wrapped paragraph line
        else:
            out += ["", text]
            kind = "para"
        previous = line
    return "\n".join(out).strip()


def apply_corrections(markdown: dict[str, str]) -> None:
    for section, old, new, _ in CORRECTIONS:
        if old not in markdown[section]:
            raise ValueError(f"correction not applicable, section {section}: {old!r}")
        markdown[section] = markdown[section].replace(old, new)


def extract() -> dict[str, str]:
    """File name -> content, for every topic plus the README."""
    reader = PdfReader(PDF)
    cover = reader.pages[0].extract_text(extraction_mode="layout")
    version = re.search(r"Versão ([\d.]+) — (\w+ de \d{4})", cover)
    if not version:
        raise ValueError("manual version not found on the cover")
    source = f"Manual de Políticas e Procedimentos, versão {version[1]}, {version[2].lower()}"

    sections = split_sections(read_body_lines(reader))
    markdown = {number: to_markdown(body) for number, (_, body) in sections.items()}
    apply_corrections(markdown)

    assert list(TOPIC_SECTIONS) == list(TOPICS), "topics differ from emporio_agent.policies"
    files = {}
    for topic, (title, numbers) in TOPIC_SECTIONS.items():
        label = "seção" if len(numbers) == 1 else "seções"
        parts = [f"# {title}", f"Fonte: {source}, {label} {', '.join(numbers)}."]
        for number in numbers:
            heading, _ = sections[number]
            parts += [f"## {heading}", markdown[number]]
        files[f"{topic}.md"] = "\n\n".join(part for part in parts if part) + "\n"
    files["README.md"] = readme(source)
    return files


def readme(source: str) -> str:
    lines = [
        "# Store policies by topic",
        "",
        f"Generated by `scripts/extract_policies.py` from `data/raw/políticas_da_loja.pdf` "
        f"({source}). Do not edit by hand. The agent's `get_policy(topic)` tool serves one file "
        "per topic.",
        "",
        "| Topic | Manual sections |",
        "|---|---|",
        *(
            f"| `{topic}` | {', '.join(numbers)} |"
            for topic, (_, numbers) in TOPIC_SECTIONS.items()
        ),
        "",
        "Sections 7.1–7.3 (service guidelines) and 10 (final provisions) are not served: they "
        "are instructions for the attendant, written into the system prompt.",
        "",
        "## Corrections",
        "",
        "Where the manual contradicts itself, the extraction replaces the text:",
        "",
        *(
            f"- Section {section}: `{old}` → `{new.replace(chr(10), ' ')}`. {reason}"
            for section, old, new, reason in CORRECTIONS
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in extract().items():
        (POLICY_DIR / name).write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {len(TOPICS)} topics and README.md to {POLICY_DIR}")


if __name__ == "__main__":
    main()
