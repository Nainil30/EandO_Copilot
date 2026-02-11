# Approved SQL Examples (Postgres)

## Top excess parts by value
SELECT
  p.part_number,
  p.commodity,
  p.unit_cost,
  e.calculated_excess,
  (e.calculated_excess * p.unit_cost) AS excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
ORDER BY excess_value DESC
LIMIT 10

## Supplier rollup: total excess value
SELECT
  s.supplier_name,
  SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
JOIN dim_supplier s ON s.supplier_id = e.supplier_id
GROUP BY s.supplier_name
ORDER BY total_excess_value DESC
LIMIT 20

## Lifecycle distribution
SELECT lifecycle_state, COUNT(*) AS parts
FROM dim_part
GROUP BY lifecycle_state
ORDER BY parts DESC
LIMIT 100
