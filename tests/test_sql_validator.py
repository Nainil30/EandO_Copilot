import pytest

from app.sql.validator import validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT part_number FROM dim_part LIMIT 5",
        "select p.part_number, sum(e.calculated_excess * p.unit_cost) v from fact_excess_calculation e "
        "join dim_part p on p.part_id = e.part_id group by 1 order by 2 desc limit 10",
        "WITH x AS (SELECT part_id FROM dim_part) SELECT * FROM x LIMIT 3",
        "SELECT lifecycle_state FROM dim_part UNION SELECT status FROM fact_scrap_approval",
        "SELECT part_number FROM dim_part LIMIT 5;",  # single trailing semicolon tolerated
    ],
)
def test_allows_read_only_queries(sql):
    r = validate_sql(sql)
    assert r.ok, r.errors


@pytest.mark.parametrize(
    "sql,needle",
    [
        ("DELETE FROM dim_part", "SELECT"),
        ("DROP TABLE dim_part", "SELECT"),
        ("UPDATE dim_part SET unit_cost = 0", "SELECT"),
        ("SELECT 1; DROP TABLE dim_part", "one statement"),
        ("SELECT * INTO backup FROM dim_part", "INTO"),
        ("SELECT pg_sleep(10)", "pg_sleep"),
        ("SELECT * FROM pg_catalog.pg_authid", "System catalog"),
        ("SELECT * FROM information_schema.tables", "System catalog"),
        ("SELECT * FROM users", "Unknown"),
        ("WITH d AS (DELETE FROM dim_part RETURNING *) SELECT * FROM d", "Forbidden"),
        ("SELECT current_setting('is_superuser')", "current_setting"),
        ("", "Empty"),
        ("SELEC part FROM", ""),
    ],
)
def test_blocks_unsafe_queries(sql, needle):
    r = validate_sql(sql)
    assert not r.ok
    assert needle.lower() in " ".join(r.errors).lower()


def test_injects_default_limit():
    r = validate_sql("SELECT part_number FROM dim_part", default_limit=100)
    assert r.ok and "LIMIT 100" in r.sql
    assert any("added LIMIT" in w for w in r.warnings)


def test_clamps_excessive_limit():
    r = validate_sql("SELECT part_number FROM dim_part LIMIT 999999", max_rows=1000)
    assert r.ok and "LIMIT 1000" in r.sql


def test_cte_names_are_not_treated_as_unknown_tables():
    r = validate_sql("WITH ltb AS (SELECT part_id FROM fact_ltb_orders) SELECT * FROM ltb LIMIT 1")
    assert r.ok and r.tables == ["fact_ltb_orders"]


def test_strips_markdown_fences():
    r = validate_sql("```sql\nSELECT 1 FROM dim_part LIMIT 1\n```")
    assert r.ok
