# SQL Interview Q&A: E&O warehouse

Twelve questions an interviewer could ask about this project's warehouse, from basic joins to a multi-table
risk query. Every answer is PostgreSQL and uses only columns in [`db/schema.sql`](../db/schema.sql), so you
can run it yourself:

```bash
docker compose exec postgres psql -U copilot_user -d eando_copilot
```

**Schema at a glance**

| Table | Grain (one row per...) | Key columns |
|---|---|---|
| `dim_part` | part | `part_id`, `part_number`, `commodity`, `unit_cost`, `lifecycle_state` (`Active`/`EOL`/`EOSS`/`Discontinued`), `eol_date` |
| `dim_supplier` | supplier | `supplier_id`, `supplier_name`, `country`, `lead_time_days` |
| `dim_platform` | product platform | `platform_id`, `platform_name`, `business_unit` |
| `fact_inventory` | part × supplier × location | `on_hand_qty`, `consigned_qty`, `in_transit_qty`, `last_updated` |
| `fact_forecast` | part × platform × week | `forecast_week`, `forecasted_units` |
| `fact_excess_calculation` | part × supplier × calc run | `on_hand`, `total_forecast_remaining`, `calculated_excess` |
| `fact_bom` | platform × part | `qty_per_unit`, `is_shared` |
| `fact_ltb_orders` | last-time-buy order | `qty_ordered`, `order_date` |
| `fact_scrap_approval` | scrap request | `scrap_qty`, `scrap_value`, `status` (`Approved`/`Pending`/`Rejected`) |

The habit that prevents most wrong answers: **before writing a join, say the grain of each table out loud.**

---

## 1. Basic JOIN: top suppliers by excess value

**Q:** "Give me the top 10 suppliers by total excess value in dollars."

```sql
SELECT
    s.supplier_name,
    SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
FROM fact_excess_calculation e
JOIN dim_supplier s ON s.supplier_id = e.supplier_id
JOIN dim_part     p ON p.part_id     = e.part_id
GROUP BY s.supplier_id, s.supplier_name
ORDER BY total_excess_value DESC
LIMIT 10;
```

Excess is stored in **units**, so value needs `unit_cost` from `dim_part`; that's why it's a three-table join.
Group by `supplier_id` as well as the name, because two suppliers can share a name. If the table held several
calculation runs (`calc_date`), you'd filter to the latest run first, otherwise every run is summed and the
value is multiplied.

---

## 2. LEFT JOIN anti-join: EOL parts with no remaining forecast

**Q:** "Which EOL parts have no remaining demand forecast?"

```sql
SELECT p.part_number, p.description, p.eol_date
FROM dim_part p
LEFT JOIN fact_forecast f
       ON f.part_id = p.part_id
      AND f.forecast_week >= CURRENT_DATE
      AND f.forecasted_units > 0
WHERE p.lifecycle_state = 'EOL'
  AND f.forecast_id IS NULL
ORDER BY p.eol_date;
```

The LEFT JOIN keeps every EOL part, and `f.forecast_id IS NULL` keeps only those with no matching row. The
trap: the forecast filters (`forecast_week`, `forecasted_units`) **must be in the `ON` clause**. Put them in
`WHERE` and the NULL rows get filtered out, which silently turns it back into an inner join. `NOT EXISTS` is
an equally good answer and often reads more clearly.

---

## 3. GROUP BY + HAVING: commodities with large on-hand inventory

**Q:** "Which commodities have more than 5,000 units on hand in total?"

```sql
SELECT
    p.commodity,
    SUM(i.on_hand_qty) AS total_on_hand
FROM fact_inventory i
JOIN dim_part p ON p.part_id = i.part_id
GROUP BY p.commodity
HAVING SUM(i.on_hand_qty) > 5000
ORDER BY total_on_hand DESC;
```

`WHERE` filters rows **before** grouping; `HAVING` filters groups **after** aggregation. That's why the
condition on `SUM(...)` must be in `HAVING`. In PostgreSQL you can't reference the alias `total_on_hand` in
`HAVING`, so repeat the expression (you can use the alias in `ORDER BY`).

---

## 4. Window function RANK(): rank parts by excess within commodity

**Q:** "Within each commodity, rank parts by their excess units."

```sql
SELECT
    p.commodity,
    p.part_number,
    SUM(e.calculated_excess) AS total_excess,
    RANK() OVER (
        PARTITION BY p.commodity
        ORDER BY SUM(e.calculated_excess) DESC
    ) AS excess_rank
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
GROUP BY p.commodity, p.part_id, p.part_number
ORDER BY p.commodity, excess_rank;
```

A part can have rows for several suppliers, so aggregate to one row per part first; the window function runs
**after** `GROUP BY`, which is why it can rank on `SUM(...)`. `RANK()` gives ties the same number and skips
the next one (1, 1, 3); `DENSE_RANK()` doesn't skip (1, 1, 2); `ROW_NUMBER()` never ties. To keep only the top
3 per commodity, wrap it in a CTE and filter `excess_rank <= 3`: you can't use a window function in `WHERE`.

---

## 5. Window function LAG(): week-over-week forecast change

**Q:** "For each part, show the forecast per week and the change from the previous week."

```sql
WITH weekly AS (
    SELECT part_id, forecast_week, SUM(forecasted_units) AS units
    FROM fact_forecast
    GROUP BY part_id, forecast_week
)
SELECT
    p.part_number,
    w.forecast_week,
    w.units,
    LAG(w.units) OVER wk           AS prev_week_units,
    w.units - LAG(w.units) OVER wk AS wow_change
FROM weekly w
JOIN dim_part p ON p.part_id = w.part_id
WINDOW wk AS (PARTITION BY w.part_id ORDER BY w.forecast_week)
ORDER BY p.part_number, w.forecast_week;
```

The CTE first rolls forecasts up to one row per part per week, since a part can be forecast on several
platforms. `LAG()` returns the **previous row** in the partition, not the previous calendar week, so a
missing week makes the comparison span two weeks; check for gaps or join to a calendar table. The first week
has no previous row, so `prev_week_units` is NULL. The named `WINDOW` clause avoids repeating the definition.

---

## 6. Window function ROW_NUMBER(): latest inventory record per part per supplier

**Q:** "Inventory gets re-loaded. Give me only the latest record for each part and supplier."

```sql
WITH ranked AS (
    SELECT
        i.*,
        ROW_NUMBER() OVER (
            PARTITION BY i.part_id, i.supplier_id
            ORDER BY i.last_updated DESC, i.inventory_id DESC
        ) AS rn
    FROM fact_inventory i
)
SELECT part_id, supplier_id, location, on_hand_qty, consigned_qty, in_transit_qty, last_updated
FROM ranked
WHERE rn = 1;
```

This is the standard **deduplication** pattern in data pipelines: partition by the business key, order by
recency, keep row 1. Add a tie-breaker (`inventory_id DESC`) so the result is deterministic when two rows share
a timestamp. PostgreSQL also has `SELECT DISTINCT ON (part_id, supplier_id) ... ORDER BY part_id, supplier_id,
last_updated DESC`, which is shorter but not portable to other databases.

---

## 7. CTE: excess ratio and a high-excess flag

**Q:** "Calculate each part's excess ratio, excess divided by on-hand, and flag anything above 0.5."

```sql
WITH latest_run AS (
    SELECT part_id,
           SUM(calculated_excess) AS excess_units,
           SUM(on_hand)           AS on_hand_units
    FROM fact_excess_calculation
    WHERE calc_date = (SELECT MAX(calc_date) FROM fact_excess_calculation)
    GROUP BY part_id
),
ratios AS (
    SELECT part_id, excess_units, on_hand_units,
           excess_units::numeric / NULLIF(on_hand_units, 0) AS excess_ratio
    FROM latest_run
)
SELECT
    p.part_number,
    p.commodity,
    r.on_hand_units,
    r.excess_units,
    ROUND(r.excess_ratio, 2) AS excess_ratio,
    r.excess_ratio > 0.5     AS high_excess_flag
FROM ratios r
JOIN dim_part p ON p.part_id = r.part_id
ORDER BY r.excess_ratio DESC NULLS LAST;
```

Each CTE is one named step (filter to the latest run → compute the ratio → join and flag), which is easier to
read and debug than one nested query. Two classic bugs are avoided here: **integer division** (`7 / 10 = 0` in
Postgres when both are integers, hence `::numeric`) and **division by zero** (`NULLIF(x, 0)` turns 0 into NULL,
so the ratio is NULL instead of an error).

---

## 8. CTE vs subquery: the same question both ways

**Q:** "Which suppliers have a total excess value above the average supplier? Write it with a subquery, then
with a CTE. When would you pick each?"

**Subquery version**

```sql
SELECT supplier_name, total_excess_value
FROM (
    SELECT s.supplier_name, SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
    FROM fact_excess_calculation e
    JOIN dim_supplier s ON s.supplier_id = e.supplier_id
    JOIN dim_part     p ON p.part_id     = e.part_id
    GROUP BY s.supplier_id, s.supplier_name
) AS supplier_totals
WHERE total_excess_value > (
    SELECT AVG(total_excess_value)
    FROM (
        SELECT SUM(e2.calculated_excess * p2.unit_cost) AS total_excess_value
        FROM fact_excess_calculation e2
        JOIN dim_part p2 ON p2.part_id = e2.part_id
        GROUP BY e2.supplier_id
    ) AS t
)
ORDER BY total_excess_value DESC;
```

**CTE version**

```sql
WITH supplier_totals AS (
    SELECT s.supplier_name, SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
    FROM fact_excess_calculation e
    JOIN dim_supplier s ON s.supplier_id = e.supplier_id
    JOIN dim_part     p ON p.part_id     = e.part_id
    GROUP BY s.supplier_id, s.supplier_name
)
SELECT supplier_name, total_excess_value
FROM supplier_totals
WHERE total_excess_value > (SELECT AVG(total_excess_value) FROM supplier_totals)
ORDER BY total_excess_value DESC;
```

The CTE wins here because the per-supplier total is needed **twice**: the subquery version has to repeat the
aggregation, and the two copies can drift apart when someone edits one. Use a subquery for a small, one-off
filter (`WHERE x IN (SELECT ...)`). Since PostgreSQL 12, a CTE referenced once is inlined like a subquery, so
there's usually no performance difference; one referenced twice is computed once (`MATERIALIZED`), and you can
override that with `AS [NOT] MATERIALIZED`.

---

## 9. Index design: why `idx_forecast_part_week` is composite

**Q:** "`db/schema.sql` has `CREATE INDEX idx_forecast_part_week ON fact_forecast(part_id, forecast_week)`.
Why one composite index instead of two single-column indexes? How would you prove it?"

The query it serves, the most common forecast access pattern ("demand for this part from now on"):

```sql
EXPLAIN ANALYZE
SELECT forecast_week, SUM(forecasted_units)
FROM fact_forecast
WHERE part_id = 42
  AND forecast_week >= CURRENT_DATE
GROUP BY forecast_week;
```

With the composite index the plan is a single index scan. The rows for `part_id = 42` are stored next to each
other in the index, already sorted by week, so Postgres jumps to the first matching week and reads forward:

```
GroupAggregate
  -> Index Scan using idx_forecast_part_week on fact_forecast
       Index Cond: ((part_id = 42) AND (forecast_week >= CURRENT_DATE))
```

To compare with two separate indexes, test it inside a transaction (DDL is transactional in Postgres, so
`ROLLBACK` undoes everything):

```sql
BEGIN;
DROP INDEX idx_forecast_part_week;
CREATE INDEX tmp_forecast_part ON fact_forecast(part_id);
CREATE INDEX tmp_forecast_week ON fact_forecast(forecast_week);
SET LOCAL enable_seqscan = off;   -- the demo table is small; force index use to see the plan shape
EXPLAIN ANALYZE
SELECT forecast_week, SUM(forecasted_units)
FROM fact_forecast
WHERE part_id = 42 AND forecast_week >= CURRENT_DATE
GROUP BY forecast_week;
ROLLBACK;
```

Typical plan shape (exact choice and costs depend on the data):

```
HashAggregate
  -> Bitmap Heap Scan on fact_forecast
       Recheck Cond: ((part_id = 42) AND (forecast_week >= CURRENT_DATE))
       -> BitmapAnd
            -> Bitmap Index Scan on tmp_forecast_part
            -> Bitmap Index Scan on tmp_forecast_week
```

With two indexes, Postgres either uses only one and filters the rest row by row, or builds a bitmap from
each and ANDs them, which is more work, especially because `forecast_week >= today` matches most of the
table. Column order matters: **equality column first, range column second**. The index also serves queries on
`part_id` alone (leftmost prefix) but not queries on `forecast_week` alone. On ~6,000 rows the planner may
just pick a sequential scan, which is correct: indexes pay off at scale, which is why the demo forces it.

---

## 10. NULL handling: EOL parts with no EOL date

**Q:** "Write a data quality check: find parts marked EOL that have no EOL date."

```sql
SELECT part_id, part_number, lifecycle_state, eol_date
FROM dim_part
WHERE lifecycle_state = 'EOL'
  AND eol_date IS NULL;
```

And as a monitoring query that summarizes every lifecycle state at once:

```sql
SELECT
    lifecycle_state,
    COUNT(*)                                  AS parts,
    COUNT(*) FILTER (WHERE eol_date IS NULL)  AS missing_eol_date
FROM dim_part
GROUP BY lifecycle_state
ORDER BY lifecycle_state;
```

`eol_date = NULL` is never true. Any comparison with NULL returns NULL (unknown), so you must use `IS NULL`.
A DE would turn the second query into a scheduled check that alerts when `missing_eol_date > 0` for EOL parts,
because every downstream excess calculation trusts that date. `COUNT(column)` skips NULLs while `COUNT(*)`
doesn't, so `COUNT(*) - COUNT(eol_date)` is another way to count the gaps.

---

## 11. Date arithmetic: parts going EOL in the next 90 days that still have stock

**Q:** "Which parts reach end-of-life in the next 90 days and still have inventory on hand?"

```sql
SELECT
    p.part_number,
    p.lifecycle_state,
    p.eol_date,
    p.eol_date - CURRENT_DATE AS days_to_eol,
    SUM(i.on_hand_qty)        AS total_on_hand
FROM dim_part p
JOIN fact_inventory i ON i.part_id = p.part_id
WHERE p.eol_date >= CURRENT_DATE
  AND p.eol_date <  CURRENT_DATE + 90
GROUP BY p.part_id, p.part_number, p.lifecycle_state, p.eol_date
HAVING SUM(i.on_hand_qty) > 0
ORDER BY p.eol_date;
```

In Postgres, `date + integer` gives a date and `date - date` gives an integer number of days, which is the
simplest arithmetic for `DATE` columns. `CURRENT_DATE + INTERVAL '90 days'` also works but returns a
`timestamp`. A half-open range (`>= start AND < end`) avoids off-by-one errors and, unlike wrapping the column
in a function, still lets Postgres use the `idx_part_eol` index.

---

## 12. Hard: parts at risk of becoming excess

**Q:** "Find parts at risk: on-hand inventory is greater than the total forecast for the next 12 weeks.
Exclude parts that already have an approved scrap. Show the units and dollars at risk."

```sql
WITH inv AS (                          -- one row per part
    SELECT part_id, SUM(on_hand_qty) AS on_hand
    FROM fact_inventory
    GROUP BY part_id
),
fcst AS (                              -- one row per part
    SELECT part_id, SUM(forecasted_units) AS forecast_12w
    FROM fact_forecast
    WHERE forecast_week >= CURRENT_DATE
      AND forecast_week <  CURRENT_DATE + 84      -- 12 weeks
    GROUP BY part_id
)
SELECT
    p.part_number,
    p.commodity,
    p.lifecycle_state,
    i.on_hand,
    COALESCE(f.forecast_12w, 0)                                AS forecast_12w,
    i.on_hand - COALESCE(f.forecast_12w, 0)                    AS units_at_risk,
    (i.on_hand - COALESCE(f.forecast_12w, 0)) * p.unit_cost    AS value_at_risk
FROM inv i
JOIN dim_part p   ON p.part_id = i.part_id
LEFT JOIN fcst f  ON f.part_id = i.part_id
WHERE i.on_hand > COALESCE(f.forecast_12w, 0)
  AND NOT EXISTS (
        SELECT 1
        FROM fact_scrap_approval sa
        WHERE sa.part_id = i.part_id
          AND sa.status = 'Approved'
  )
ORDER BY value_at_risk DESC NULLS LAST
LIMIT 25;
```

The key move is **aggregating each fact table to one row per part before joining**. Joining `fact_inventory`
(several rows per part) directly to `fact_forecast` (12+ rows per part) multiplies rows (fan-out), and both
sums come out inflated. The LEFT JOIN + `COALESCE(..., 0)` keeps parts with no forecast at all, which are the
riskiest. `NOT EXISTS` is safer than `NOT IN` because `NOT IN` returns no rows at all if the subquery contains
a NULL. On the synthetic data the forecast starts on the day `bootstrap` ran; if it is old, re-run
`python -m scripts.bootstrap --regenerate` so `CURRENT_DATE` falls inside the forecast horizon.
