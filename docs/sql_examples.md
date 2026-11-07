# Approved SQL Examples (PostgreSQL)

Curated question -> SQL pairs. Each `##` section becomes one chunk and is retrieved as a
**dynamic few-shot example**: the generator sees the 1-3 examples most similar to the user's
question. Add a new example whenever you fix a recurring failure (from evals or thumbs-down feedback).

## Top excess parts by value
Question: Which parts have the highest excess value?
```sql
SELECT p.part_number, p.commodity, p.lifecycle_state, e.calculated_excess, p.unit_cost,
       e.calculated_excess * p.unit_cost AS excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
ORDER BY excess_value DESC
LIMIT 10
```

## Excess value for EOL parts
Question: Top 10 EOL parts by excess value
```sql
SELECT p.part_number, p.commodity, SUM(e.calculated_excess) AS excess_qty,
       SUM(e.calculated_excess * p.unit_cost) AS excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
WHERE p.lifecycle_state = 'EOL'
GROUP BY p.part_number, p.commodity
ORDER BY excess_value DESC
LIMIT 10
```

## Supplier rollup of excess value
Question: Total excess value by supplier
```sql
SELECT s.supplier_name, s.country, SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
JOIN dim_supplier s ON s.supplier_id = e.supplier_id
GROUP BY s.supplier_name, s.country
ORDER BY total_excess_value DESC
LIMIT 10
```

## Excess value by commodity
Question: Which commodities contribute most to excess value?
```sql
SELECT p.commodity, SUM(e.calculated_excess * p.unit_cost) AS excess_value, COUNT(DISTINCT p.part_id) AS parts
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
GROUP BY p.commodity
ORDER BY excess_value DESC
LIMIT 5
```

## Excess by supplier country
Question: Total excess quantity and value by supplier country
```sql
SELECT s.country, SUM(e.calculated_excess) AS total_excess_qty,
       SUM(e.calculated_excess * p.unit_cost) AS total_excess_value
FROM fact_excess_calculation e
JOIN dim_supplier s ON s.supplier_id = e.supplier_id
JOIN dim_part p ON p.part_id = e.part_id
GROUP BY s.country
ORDER BY total_excess_value DESC
LIMIT 100
```

## Excess by lifecycle state
Question: How is excess value split across lifecycle states?
```sql
SELECT p.lifecycle_state, SUM(e.calculated_excess * p.unit_cost) AS excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
GROUP BY p.lifecycle_state
ORDER BY excess_value DESC
LIMIT 100
```

## Lifecycle distribution of parts
Question: Count parts by lifecycle state
```sql
SELECT lifecycle_state, COUNT(*) AS parts
FROM dim_part
GROUP BY lifecycle_state
ORDER BY parts DESC
LIMIT 100
```

## Consignment eligible excess
Question: Which excess positions are eligible to move to supplier consignment?
```sql
SELECT p.part_number, s.supplier_name, e.calculated_excess, e.scrap_recommended,
       e.calculated_excess * p.unit_cost AS excess_value
FROM fact_excess_calculation e
JOIN dim_part p ON p.part_id = e.part_id
JOIN dim_supplier s ON s.supplier_id = e.supplier_id
WHERE e.consignment_eligible = TRUE
ORDER BY excess_value DESC
LIMIT 20
```

## Inventory by location
Question: Total on hand, consigned and in-transit quantity per location
```sql
SELECT i.location, SUM(i.on_hand_qty) AS on_hand_qty, SUM(i.consigned_qty) AS consigned_qty,
       SUM(i.in_transit_qty) AS in_transit_qty
FROM fact_inventory i
GROUP BY i.location
ORDER BY on_hand_qty DESC
LIMIT 100
```

## Inventory value by commodity
Question: What is the on-hand inventory value by commodity?
```sql
SELECT p.commodity, SUM(i.on_hand_qty * p.unit_cost) AS on_hand_value
FROM fact_inventory i
JOIN dim_part p ON p.part_id = i.part_id
GROUP BY p.commodity
ORDER BY on_hand_value DESC
LIMIT 100
```

## Top parts by on hand with supplier and location
Question: List top parts by on-hand quantity with supplier and location
```sql
SELECT p.part_number, s.supplier_name, i.location, i.on_hand_qty
FROM fact_inventory i
JOIN dim_part p ON p.part_id = i.part_id
JOIN dim_supplier s ON s.supplier_id = i.supplier_id
ORDER BY i.on_hand_qty DESC
LIMIT 10
```

## Forecast demand next 12 weeks
Question: Top parts by forecasted units over the next 12 weeks
```sql
SELECT p.part_number, SUM(f.forecasted_units) AS forecast_units_12w
FROM fact_forecast f
JOIN dim_part p ON p.part_id = f.part_id
WHERE f.forecast_week >= CURRENT_DATE - INTERVAL '7 days'
  AND f.forecast_week < CURRENT_DATE + INTERVAL '12 weeks'
GROUP BY p.part_number
ORDER BY forecast_units_12w DESC
LIMIT 10
```

## Weekly forecast trend
Question: Weekly forecast trend across all parts
```sql
SELECT f.forecast_week, SUM(f.forecasted_units) AS forecasted_units
FROM fact_forecast f
GROUP BY f.forecast_week
ORDER BY f.forecast_week
LIMIT 100
```

## Platforms by BOM size
Question: Which platforms have the most parts in their bill of materials?
```sql
SELECT pl.platform_name, pl.business_unit, COUNT(DISTINCT b.part_id) AS bom_parts
FROM fact_bom b
JOIN dim_platform pl ON pl.platform_id = b.platform_id
GROUP BY pl.platform_name, pl.business_unit
ORDER BY bom_parts DESC
LIMIT 5
```

## Parts used on a specific platform
Question: Top parts by quantity per unit on the ServerY R750 platform
```sql
SELECT p.part_number, p.commodity, b.qty_per_unit
FROM fact_bom b
JOIN dim_platform pl ON pl.platform_id = b.platform_id
JOIN dim_part p ON p.part_id = b.part_id
WHERE pl.platform_name ILIKE '%R750%'
ORDER BY b.qty_per_unit DESC
LIMIT 10
```

## Recent last time buy orders
Question: Show the latest LTB orders with part and supplier
```sql
SELECT l.order_date, p.part_number, s.supplier_name, l.qty_ordered, l.order_reason
FROM fact_ltb_orders l
JOIN dim_part p ON p.part_id = l.part_id
JOIN dim_supplier s ON s.supplier_id = l.supplier_id
ORDER BY l.order_date DESC
LIMIT 20
```

## LTB orders arriving soon
Question: Which LTB orders are expected to be delivered in the next 90 days?
```sql
SELECT l.ltb_id, p.part_number, s.supplier_name, l.qty_ordered, l.expected_delivery_date
FROM fact_ltb_orders l
JOIN dim_part p ON p.part_id = l.part_id
JOIN dim_supplier s ON s.supplier_id = l.supplier_id
WHERE l.expected_delivery_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '90 days'
ORDER BY l.expected_delivery_date
LIMIT 100
```

## Scrap approval status mix
Question: Count and value of scrap approvals by status
```sql
SELECT status, COUNT(*) AS approvals, SUM(scrap_value) AS total_scrap_value
FROM fact_scrap_approval
GROUP BY status
ORDER BY total_scrap_value DESC
LIMIT 100
```

## Pending scrap backlog
Question: Largest pending scrap approvals
```sql
SELECT a.regrello_workflow_id, p.part_number, a.scrap_qty, a.scrap_value, a.approval_level, a.gsm_approver
FROM fact_scrap_approval a
JOIN dim_part p ON p.part_id = a.part_id
WHERE a.status = 'Pending'
ORDER BY a.scrap_value DESC
LIMIT 10
```

## Excess exposure versus LTB quantity
Question: Which EOL parts have both LTB orders and excess (over-buy risk)?
```sql
WITH ltb AS (
  SELECT part_id, SUM(qty_ordered) AS ltb_qty FROM fact_ltb_orders GROUP BY part_id
), exc AS (
  SELECT part_id, SUM(calculated_excess) AS excess_qty FROM fact_excess_calculation GROUP BY part_id
)
SELECT p.part_number, p.lifecycle_state, ltb.ltb_qty, exc.excess_qty,
       exc.excess_qty * p.unit_cost AS excess_value
FROM ltb
JOIN exc ON exc.part_id = ltb.part_id
JOIN dim_part p ON p.part_id = ltb.part_id
ORDER BY excess_value DESC
LIMIT 20
```

## Supplier lead time ranking
Question: Which suppliers have the longest lead times?
```sql
SELECT supplier_name, supplier_tier, country, lead_time_days
FROM dim_supplier
ORDER BY lead_time_days DESC
LIMIT 10
```
