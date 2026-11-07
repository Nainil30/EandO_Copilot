# Business Glossary – E&O Copilot

Business definitions used by the copilot. Each `##` section becomes one retrievable chunk,
so keep every section self-contained.

## Excess and Obsolete (E&O) inventory
E&O inventory is stock that is not expected to be consumed by remaining demand (excess) or
that can no longer be used because the part or platform reached end of life (obsolete).
E&O drives inventory write-offs, so supply-chain teams track it weekly.

## Excess quantity
Excess quantity is on-hand inventory minus remaining forecast demand over the horizon.
Formula: excess_qty = on_hand - total_forecast_remaining.
In the warehouse it is precomputed as fact_excess_calculation.calculated_excess.
If excess_qty <= 0 the part has no excess.

## Excess value
Excess value (excess $, excess dollars, E&O exposure) is the dollar value of excess units.
Formula: excess_value = fact_excess_calculation.calculated_excess * dim_part.unit_cost.
Join fact_excess_calculation to dim_part on part_id. Aggregate with SUM for rollups by
supplier, commodity, country or lifecycle_state.

## Forecast horizon
The default demand horizon is the next 12 weeks (fact_forecast.forecast_week between
CURRENT_DATE and CURRENT_DATE + INTERVAL '12 weeks') unless the user specifies another window.

## Scrap and hold recommendation
The excess engine splits excess into scrap and hold:
scrap_recommended = floor(calculated_excess * 0.70) and
hold_recommended = calculated_excess - scrap_recommended.
Scrap value = scrap_recommended * unit_cost. Hold stock is kept as a buffer for service demand.

## Scrap approval workflow
Scrap write-offs need approval (fact_scrap_approval). status is 'Approved', 'Pending' or
'Rejected'. approval_level 1-3 reflects the value tier (higher level = larger write-off,
more senior approver). Pending approvals with high scrap_value are the main risk backlog.

## Lifecycle states
dim_part.lifecycle_state values:
- Active: currently shipping and supported.
- EOL (End of Life): end-of-life announced; last-time buys may be placed.
- EOSS (End of Service Support): only service demand remains until eoss_date.
- Discontinued: no further demand expected; remaining stock is obsolete.
"At-risk" parts usually means lifecycle_state IN ('EOL', 'EOSS', 'Discontinued').

## Last Time Buy (LTB)
A Last Time Buy is the final purchase order placed with a supplier before a part is
discontinued (fact_ltb_orders). Over-sized LTBs are a leading cause of future excess.
order_reason is one of 'EOL Final Buy', 'Service Buffer', 'Safety Stock'.

## Consignment
Consigned inventory (fact_inventory.consigned_qty) is owned by the supplier until consumed,
so it does not count as company-owned excess. dim_supplier.consignment_partner marks suppliers
that support consignment; fact_excess_calculation.consignment_eligible marks excess that could be
moved back to supplier consignment to reduce exposure.

## Shared parts and BOM
A Bill of Materials (fact_bom) lists the parts used per platform and qty_per_unit.
is_shared = true means the part is used on multiple platforms, which lowers obsolescence risk
because demand from one platform can absorb excess from another.

## Supplier tiers and lead time
supplier_tier is 'Tier 1', 'Tier 2' or 'CM' (contract manufacturer). Long lead_time_days
increases the safety stock needed and therefore excess risk when demand drops.

## Inventory positions
fact_inventory has three quantities per part, supplier and location:
on_hand_qty (owned, physically present), consigned_qty (supplier-owned) and in_transit_qty
(shipped, not received). Total pipeline = on_hand_qty + in_transit_qty.
