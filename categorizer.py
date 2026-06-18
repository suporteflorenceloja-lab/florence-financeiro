"""Categorize transactions using rules first, then Claude AI as fallback."""
from __future__ import annotations
import json
import os

from config import CATEGORIES


def categorize(rows: list[dict], rules: list[dict]) -> list[dict]:
    """Apply rules to all rows. Unknowns go to Claude if API key is set."""
    rules_sorted = sorted(rules, key=lambda r: -r.get("priority", 0))

    uncategorized_indices = []
    for i, row in enumerate(rows):
        cat = _apply_rules(row["description"], rules_sorted)
        if cat:
            rows[i]["category"] = cat
        elif row["amount"] > 0:
            rows[i]["category"] = "RECEITA BRUTA"
        else:
            uncategorized_indices.append(i)

    if uncategorized_indices and os.getenv("ANTHROPIC_API_KEY"):
        unknowns = [rows[i] for i in uncategorized_indices]
        ai_cats = _ask_claude_batch(unknowns)
        for idx, cat in zip(uncategorized_indices, ai_cats):
            rows[idx]["category"] = cat

    return rows


def _apply_rules(description: str, rules: list[dict]) -> str | None:
    desc_upper = description.upper()
    for rule in rules:
        if rule["keyword"].upper() in desc_upper:
            return rule["category"]
    return None


def _ask_claude_batch(rows: list[dict]) -> list[str]:
    """Send unknown transactions to Claude and get categories back."""
    try:
        import anthropic
    except ImportError:
        return ["OUTROS"] * len(rows)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    lines = "\n".join(
        f"{i+1}. {r['description']} | R$ {r['amount']:.2f}"
        for i, r in enumerate(rows)
    )
    categories_str = ", ".join(CATEGORIES)

    prompt = f"""Você é uma assistente contábil para uma micro-empresa de lingerie artesanal (Florence Intimates).
Categorize cada lançamento bancário abaixo usando SOMENTE uma das categorias da lista.

CATEGORIAS DISPONÍVEIS:
{categories_str}

LANÇAMENTOS (formato: número. descrição | valor):
{lines}

Responda SOMENTE com um JSON array com a categoria de cada lançamento na mesma ordem.
Exemplo: ["FRETE PEDIDO", "MATERIAL", "OUTROS"]
Valores positivos são geralmente RECEITA BRUTA. Débitos negativos são custos/despesas."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Extract JSON array from response
        match = __import__("re").search(r"\[.*\]", text, __import__("re").DOTALL)
        if match:
            cats = json.loads(match.group())
            # Validate each category
            return [
                c if c in CATEGORIES else "OUTROS"
                for c in cats[:len(rows)]
            ] + ["OUTROS"] * max(0, len(rows) - len(cats))
    except Exception:
        pass

    return ["OUTROS"] * len(rows)
