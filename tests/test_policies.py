"""T-policies: every topic loads, key facts survive the extraction, files match the PDF."""

import importlib.util
from pathlib import Path

import pytest

from emporio_agent.policies import POLICY_DIR, TOPICS, load_policies

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def policies():
    return load_policies()


def test_every_topic_loads(policies):
    assert set(policies) == set(TOPICS)
    assert all(text.startswith("# ") for text in policies.values())


@pytest.mark.parametrize(
    ("topic", "facts"),
    [
        (
            "store_info",
            [
                "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333",
                "| Telefone | (67) 3341-4444 |",
                "| WhatsApp | (67) 3321-4500 |",
                "contato@emporiodamusica.com.br",
                "não comercializamos acessórios",
            ],
        ),
        (
            "hours",
            [
                "| Segunda a Sexta-feira | 09:00 às 18:00 |",
                "| Sábado | 09:00 às 13:00 |",
                "| Domingo e Feriados | Fechado |",
            ],
        ),
        (
            "payment",
            [
                "| PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela |",
                "até 3x: sem juros, parcela mínima de R$ 50,00",
                "4x a 6x: sem juros, parcela mínima de R$ 80,00",
                "7x a 12x: sem juros, parcela mínima de R$ 100,00",
                "acima de R$ 2.000,00",
            ],
        ),
        (
            "returns",
            [
                "até 7 (sete) dias corridos após o recebimento",
                "até 30 (trinta) dias corridos após a compra",
                "Trocas por preferência (cor, modelo, tamanho) são permitidas em até 7 dias",
            ],
        ),
        ("shipping", ["Frete grátis para pedidos acima de R$ 500,00", "| PAC (Correios) |"]),
        ("promotions", ["não são cumulativas", "O desconto de PIX (5%) não se aplica"]),
        ("warranty", ["garantia legal de 90 (noventa) dias"]),
        ("privacy", ["LGPD", "não são compartilhados com terceiros"]),
    ],
)
def test_key_facts(policies, topic, facts):
    for fact in facts:
        assert fact in policies[topic]


def test_contradictions_are_corrected(policies):
    text = "\n".join(policies.values())
    assert "empóriodamusica" not in text
    assert "Telefone / WhatsApp" not in text
    assert "sem valor mínimo de parcela" not in text


def test_attendant_guidelines_are_not_served(policies):
    text = "\n".join(policies.values())
    assert "Diretrizes de Atendimento" not in text  # 7.1, written into the system prompt
    assert "Disposições Finais" not in text  # 10


def test_missing_topic_fails(tmp_path):
    with pytest.raises(FileNotFoundError, match="extract_policies.py"):
        load_policies(tmp_path)


def test_files_match_a_fresh_extraction():
    spec = importlib.util.spec_from_file_location(
        "extract_policies", ROOT / "scripts" / "extract_policies.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    for name, content in script.extract().items():
        assert (POLICY_DIR / name).read_text(encoding="utf-8") == content, name
