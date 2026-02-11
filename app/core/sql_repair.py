from __future__ import annotations

import re
from typing import Optional

from app.llm.gemini_client import generate_text


REPAIR_TEMPLATE = """
You are fixing a PostgreSQL SELECT query that failed to execute.

Rules:
- Return ONLY corrected SQL (no markdown, no commentary).
- Keep it as a single SELECT statement (no semicolons).
- Do NOT invent new tables/columns. Use only what exists in the schema.
- Fix only what is necessary to address the error.

Schema hints:
- dim_part columns include: part_id, part_number, description, commodity, unit_cost, lifecycle_state, eol_date, eoss_date, platform_primary, created_at
- fact_excess_calculation columns include: part_id, supplier_id, calculated_excess, scrap_recommended, hold_recommended, total_forecast_remaining, on_hand, calc_date, consignment_eligible

Execution error:
{error}

Bad SQL:
{sql}

Corrected SQL:
""".strip()


def can_repair(error_message: str) -> bool:
    """Only retry for a narrow set of deterministic errors."""
    patterns = [
        r"UndefinedColumn",
        r"column .* does not exist",
        r"UndefinedTable",
        r"relation .* does not exist",
    ]
    return any(re.search(p, error_message, flags=re.IGNORECASE) for p in patterns)


def repair_sql(sql: str, error_message: str, model: str = "gemini-1.5-flash") -> Optional[str]:
    prompt = REPAIR_TEMPLATE.format(error=error_message, sql=sql)
    fixed = generate_text(prompt=prompt, model=model).strip()
    if not fixed:
        return None
    return fixed
