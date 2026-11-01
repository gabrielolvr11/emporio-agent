"""Store policies by topic, extracted from the store manual by scripts/extract_policies.py."""

from pathlib import Path
from typing import Literal, get_args

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "processed" / "policies"

Topic = Literal[
    "store_info", "hours", "payment", "returns", "shipping", "promotions", "warranty", "privacy"
]
TOPICS: tuple[Topic, ...] = get_args(Topic)


def load_policies(policy_dir: Path = POLICY_DIR) -> dict[Topic, str]:
    """Read the Markdown file of every topic. Fails if one is missing."""
    policies: dict[Topic, str] = {}
    for topic in TOPICS:
        path = policy_dir / f"{topic}.md"
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing: run scripts/extract_policies.py")
        policies[topic] = path.read_text(encoding="utf-8")
    return policies
