"""Semantic layer / data dictionary.

Raw `information_schema` gives the LLM names and types only. Real text-to-SQL
accuracy comes from *meaning*: what a column represents, its units, valid
values and how tables relate. This catalog is the single source of truth for:

- the SQL validator's table allow-list
- schema chunks in the RAG knowledge base (one chunk per table)
- join-edge chunks (foreign keys)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    values: tuple[str, ...] = ()  # known categorical values (value linking)


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    grain: str
    columns: tuple[Column, ...]
    synonyms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JoinEdge:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


C = Column

TABLES: tuple[Table, ...] = (
    Table(
        "dim_part",
        "Master data for every component/part number in the supply chain.",
        "one row per part",
        (
            C("part_id", "integer", "Surrogate primary key."),
            C("part_number", "varchar", "Human-readable part number, e.g. 0X2796. Unique."),
            C("description", "text", "Free-text part description (commodity + vendor)."),
            C(
                "commodity",
                "varchar",
                "Component category.",
                (
                    "DRAM",
                    "SSD",
                    "HDD",
                    "Controller",
                    "Power Supply",
                    "Fan",
                    "Enclosure",
                    "NIC",
                    "CPU",
                    "RAID",
                    "Cable",
                    "Heatsink",
                ),
            ),
            C("unit_cost", "numeric(10,2)", "Standard cost per unit in USD. Multiply quantities by this to get $ value."),
            C("lifecycle_state", "varchar", "Product lifecycle stage of the part.", ("Active", "EOL", "EOSS", "Discontinued")),
            C("eol_date", "date", "End-of-life date (null for Active parts)."),
            C("eoss_date", "date", "End-of-service-support date."),
            C(
                "platform_primary",
                "varchar",
                "Name of the main platform using this part (denormalised text, joins to dim_platform.platform_name).",
            ),
            C("created_at", "timestamp", "Row creation timestamp."),
        ),
        ("component", "sku", "item", "material"),
    ),
    Table(
        "dim_supplier",
        "Suppliers, contract manufacturers and consignment partners.",
        "one row per supplier",
        (
            C("supplier_id", "integer", "Surrogate primary key."),
            C("supplier_name", "varchar", "Supplier legal name."),
            C("country", "varchar", "Supplier country.", ("China", "Taiwan", "Malaysia", "USA", "Mexico", "Vietnam")),
            C("consignment_partner", "boolean", "True if the supplier holds consigned (supplier-owned) inventory."),
            C("lead_time_days", "integer", "Quoted lead time in days."),
            C("supplier_tier", "varchar", "Supplier tier.", ("Tier 1", "Tier 2", "CM")),
        ),
        ("vendor", "cm", "contract manufacturer"),
    ),
    Table(
        "dim_platform",
        "End products / hardware platforms (servers, storage arrays) that consume parts.",
        "one row per platform",
        (
            C("platform_id", "integer", "Surrogate primary key."),
            C("platform_name", "varchar", "Platform/product name, e.g. 'ServerY R750', 'StorageX 3000'."),
            C("business_unit", "varchar", "Owning business unit.", ("Storage", "Servers")),
            C("launch_date", "date", "Platform launch date."),
            C("eol_date", "date", "Platform end-of-life date (null if still active)."),
        ),
        ("product", "program", "system"),
    ),
    Table(
        "fact_bom",
        "Bill of materials: which parts are used in which platform and how many per unit.",
        "one row per platform x part",
        (
            C("bom_id", "integer", "Primary key."),
            C("platform_id", "integer", "FK to dim_platform."),
            C("part_id", "integer", "FK to dim_part."),
            C("qty_per_unit", "integer", "Quantity of the part per finished platform unit."),
            C("is_shared", "boolean", "True if the part is shared with other platforms (lower obsolescence risk)."),
            C("effective_start", "date", "BOM line effective start."),
            C("effective_end", "date", "BOM line effective end."),
        ),
        ("bill of materials", "where used"),
    ),
    Table(
        "fact_inventory",
        "Current inventory snapshot by part, supplier and location.",
        "one row per part x supplier x location",
        (
            C("inventory_id", "integer", "Primary key."),
            C("part_id", "integer", "FK to dim_part."),
            C("supplier_id", "integer", "FK to dim_supplier."),
            C("location", "varchar", "Stocking location.", ("Penang Hub", "Shanghai CM", "Austin DC", "Cork IE", "GDL MX")),
            C("on_hand_qty", "integer", "Units physically on hand (owned)."),
            C("consigned_qty", "integer", "Units held on consignment (supplier-owned until consumed)."),
            C("in_transit_qty", "integer", "Units in transit."),
            C("last_updated", "timestamp", "Snapshot timestamp."),
        ),
        ("stock", "on hand", "warehouse"),
    ),
    Table(
        "fact_forecast",
        "Weekly demand forecast per part and platform (12-week horizon).",
        "one row per part x platform x week",
        (
            C("forecast_id", "integer", "Primary key."),
            C("platform_id", "integer", "FK to dim_platform."),
            C("part_id", "integer", "FK to dim_part."),
            C("forecast_week", "date", "Week start date of the forecast bucket."),
            C("forecasted_units", "integer", "Forecast demand in units for that week."),
            C("forecast_type", "varchar", "Forecast category.", ("Sustaining", "Pre-EOL", "Final-Buy")),
        ),
        ("demand", "plan"),
    ),
    Table(
        "fact_ltb_orders",
        "Last-Time-Buy (LTB) purchase orders placed when a part goes end-of-life.",
        "one row per LTB order",
        (
            C("ltb_id", "integer", "Primary key."),
            C("platform_id", "integer", "FK to dim_platform."),
            C("part_id", "integer", "FK to dim_part."),
            C("supplier_id", "integer", "FK to dim_supplier."),
            C("order_date", "date", "Date the LTB order was placed."),
            C("qty_ordered", "integer", "Units ordered."),
            C("expected_delivery_date", "date", "Expected delivery date."),
            C("order_reason", "text", "Reason for the LTB.", ("EOL Final Buy", "Service Buffer", "Safety Stock")),
            C("gsm_approver", "varchar", "Global Supply Manager who approved the order."),
        ),
        ("last time buy", "final buy", "ltb"),
    ),
    Table(
        "fact_excess_calculation",
        "Output of the excess engine: on-hand minus remaining forecast, with scrap/hold split.",
        "one row per part x supplier x calc_date",
        (
            C("calc_id", "integer", "Primary key."),
            C("part_id", "integer", "FK to dim_part."),
            C("supplier_id", "integer", "FK to dim_supplier."),
            C("calc_date", "date", "Date of the excess calculation run."),
            C("on_hand", "integer", "On-hand units used in the calculation."),
            C("total_forecast_remaining", "integer", "Sum of remaining forecast over the horizon."),
            C(
                "calculated_excess",
                "integer",
                "Excess units = on_hand - total_forecast_remaining. Excess $ = calculated_excess * dim_part.unit_cost.",
            ),
            C("scrap_recommended", "integer", "Units recommended to scrap (~70% of excess)."),
            C("hold_recommended", "integer", "Units recommended to hold (~30% of excess)."),
            C("consignment_eligible", "boolean", "True if excess can be moved to supplier consignment."),
        ),
        ("excess", "e&o", "obsolete", "overstock"),
    ),
    Table(
        "fact_scrap_approval",
        "Scrap approval workflow records for excess inventory write-offs.",
        "one row per approval request",
        (
            C("approval_id", "integer", "Primary key."),
            C("part_id", "integer", "FK to dim_part."),
            C("scrap_qty", "integer", "Units requested for scrap."),
            C("scrap_value", "numeric(12,2)", "USD value of the scrap request."),
            C("gsm_approver", "varchar", "Approver name."),
            C("approval_level", "integer", "Approval tier required (1-3, higher = larger value)."),
            C("approval_date", "date", "Decision date."),
            C("status", "varchar", "Workflow status.", ("Approved", "Pending", "Rejected")),
            C("regrello_workflow_id", "varchar", "External workflow id, e.g. WF-42382."),
        ),
        ("write off", "write-off", "disposal"),
    ),
)

JOIN_EDGES: tuple[JoinEdge, ...] = (
    JoinEdge("fact_bom", "platform_id", "dim_platform", "platform_id"),
    JoinEdge("fact_bom", "part_id", "dim_part", "part_id"),
    JoinEdge("fact_inventory", "part_id", "dim_part", "part_id"),
    JoinEdge("fact_inventory", "supplier_id", "dim_supplier", "supplier_id"),
    JoinEdge("fact_forecast", "platform_id", "dim_platform", "platform_id"),
    JoinEdge("fact_forecast", "part_id", "dim_part", "part_id"),
    JoinEdge("fact_ltb_orders", "platform_id", "dim_platform", "platform_id"),
    JoinEdge("fact_ltb_orders", "part_id", "dim_part", "part_id"),
    JoinEdge("fact_ltb_orders", "supplier_id", "dim_supplier", "supplier_id"),
    JoinEdge("fact_excess_calculation", "part_id", "dim_part", "part_id"),
    JoinEdge("fact_excess_calculation", "supplier_id", "dim_supplier", "supplier_id"),
    JoinEdge("fact_scrap_approval", "part_id", "dim_part", "part_id"),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}
ALLOWED_TABLES: frozenset[str] = frozenset(TABLE_BY_NAME)


def table_doc(t: Table) -> str:
    """Render a table as a compact, LLM-friendly schema chunk (DDL-like + semantics)."""
    lines = [f"TABLE {t.name} -- {t.description} Grain: {t.grain}."]
    if t.synonyms:
        lines.append(f"Also called: {', '.join(t.synonyms)}.")
    for c in t.columns:
        line = f"  {t.name}.{c.name} {c.type} -- {c.description}"
        if c.values:
            line += " Values: " + ", ".join(f"'{v}'" for v in c.values) + "."
        lines.append(line)
    edges = [e for e in JOIN_EDGES if t.name in (e.from_table, e.to_table)]
    if edges:
        lines.append("Joins:")
        lines.extend(f"  {e.from_table}.{e.from_column} = {e.to_table}.{e.to_column}" for e in edges)
    return "\n".join(lines)


def edge_doc(e: JoinEdge) -> str:
    return (
        f"JOIN EDGE {e.from_table} -> {e.to_table}: "
        f"{e.from_table}.{e.from_column} = {e.to_table}.{e.to_column} "
        f"(many {e.from_table} rows per {e.to_table} row)."
    )


def compact_schema() -> str:
    """One-line-per-table summary used in repair prompts (always included)."""
    return "\n".join(f"{t.name}({', '.join(c.name for c in t.columns)})" for t in TABLES)
