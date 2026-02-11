# scripts/generate_data.py
import os
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from faker import Faker

# Reproducibility
Faker.seed(42)
np.random.seed(42)
random.seed(42)
fake = Faker()

OUT_DIR = "data"
os.makedirs(OUT_DIR, exist_ok=True)

NUM_PARTS = 500
NUM_SUPPLIERS = 30
NUM_PLATFORMS = 8


def generate_platforms():
    platforms = [
        {"platform_name": "StorageX 1000", "business_unit": "Storage", "launch_date": "2020-05-01", "eol_date": "2024-12-31"},
        {"platform_name": "StorageX 3000", "business_unit": "Storage", "launch_date": "2021-03-15", "eol_date": None},
        {"platform_name": "ServerY R750",  "business_unit": "Servers", "launch_date": "2021-02-10", "eol_date": "2025-06-30"},
        {"platform_name": "ServerY R760",  "business_unit": "Servers", "launch_date": "2023-01-20", "eol_date": None},
        {"platform_name": "VaultZ ME5",    "business_unit": "Storage", "launch_date": "2019-08-01", "eol_date": "2024-03-31"},
        {"platform_name": "ServerY R650",  "business_unit": "Servers", "launch_date": "2020-11-05", "eol_date": "2024-11-30"},
        {"platform_name": "ScaleF 900",    "business_unit": "Storage", "launch_date": "2022-06-01", "eol_date": None},
        {"platform_name": "ServerY XE9",   "business_unit": "Servers", "launch_date": "2023-09-01", "eol_date": None},
    ]
    df = pd.DataFrame(platforms)
    df.insert(0, "platform_id", range(1, len(df) + 1))
    print(f"✅ Generated {len(df)} platforms")
    return df


def generate_suppliers():
    tiers = ["Tier 1", "Tier 2", "CM"]
    countries = ["China", "Taiwan", "Malaysia", "USA", "Mexico", "Vietnam"]

    suppliers = []
    for i in range(NUM_SUPPLIERS):
        suppliers.append({
            "supplier_id": i + 1,
            "supplier_name": fake.company(),
            "country": random.choice(countries),
            "consignment_partner": random.random() < 0.35,
            "lead_time_days": random.randint(30, 120),
            "supplier_tier": random.choice(tiers),
        })

    df = pd.DataFrame(suppliers)
    print(f"✅ Generated {len(df)} suppliers")
    return df


def generate_parts(platforms_df):
    """
    IMPORTANT: Guarantees unique part_number values so Postgres UNIQUE constraint won't fail.
    """
    commodities = ["DRAM", "SSD", "HDD", "Controller", "Power Supply", "Fan", "Enclosure", "NIC", "CPU", "RAID", "Cable", "Heatsink"]
    lifecycle_states = ["Active", "EOL", "EOSS", "Discontinued"]
    prefixes = ["0X", "AB", "CD", "EF", "1K", "2M", "3N", "4P"]

    platform_names = platforms_df["platform_name"].tolist()

    used_part_numbers = set()

    def make_unique_part_number():
        while True:
            pn = f"{random.choice(prefixes)}{random.randint(1000, 9999)}"
            if pn not in used_part_numbers:
                used_part_numbers.add(pn)
                return pn

    parts = []
    for i in range(NUM_PARTS):
        commodity = random.choice(commodities)
        lifecycle = np.random.choice(lifecycle_states, p=[0.65, 0.20, 0.10, 0.05])

        eol_date = None
        eoss_date = None
        if lifecycle in ["EOL", "EOSS", "Discontinued"]:
            eol_date = fake.date_between(start_date="-18m", end_date="+6m")
            if lifecycle in ["EOSS", "Discontinued"]:
                eoss_date = fake.date_between(start_date=eol_date, end_date="+12m")

        # Cost bands by commodity
        if commodity in ["DRAM", "SSD", "CPU"]:
            unit_cost = round(random.uniform(60, 600), 2)
        elif commodity in ["HDD", "Controller", "RAID"]:
            unit_cost = round(random.uniform(25, 250), 2)
        else:
            unit_cost = round(random.uniform(5, 120), 2)

        parts.append({
            "part_id": i + 1,
            "part_number": make_unique_part_number(),
            "description": f"{commodity} - {fake.company()}",
            "commodity": commodity,
            "unit_cost": unit_cost,
            "lifecycle_state": lifecycle,
            "eol_date": eol_date,
            "eoss_date": eoss_date,
            "platform_primary": random.choice(platform_names),
        })

    df = pd.DataFrame(parts)
    print(f"✅ Generated {len(df)} parts (unique part_number enforced)")
    return df


def generate_bom(parts_df, platforms_df):
    records = []
    bom_id = 1

    for _, platform in platforms_df.iterrows():
        # Each platform uses 25–60 parts
        n = random.randint(25, 60)
        selected = parts_df.sample(n=n, random_state=random.randint(1, 99999))
        for _, part in selected.iterrows():
            records.append({
                "bom_id": bom_id,
                "platform_id": int(platform["platform_id"]),
                "part_id": int(part["part_id"]),
                "qty_per_unit": random.randint(1, 8),
                "is_shared": random.random() < 0.20,
                "effective_start": platform["launch_date"],
                "effective_end": platform["eol_date"],
            })
            bom_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} BOM rows")
    return df


def generate_inventory(parts_df, suppliers_df):
    locations = ["Penang Hub", "Shanghai CM", "Austin DC", "Cork IE", "GDL MX"]

    records = []
    inv_id = 1

    for _, part in parts_df.iterrows():
        num_locs = random.randint(1, 3)
        chosen = suppliers_df.sample(n=num_locs, random_state=random.randint(1, 99999))

        for _, supplier in chosen.iterrows():
            if part["lifecycle_state"] in ["EOL", "EOSS"]:
                on_hand = random.randint(500, 7000)
                consigned = random.randint(200, 2500)
            else:
                on_hand = random.randint(50, 1200)
                consigned = random.randint(0, 600)

            records.append({
                "inventory_id": inv_id,
                "part_id": int(part["part_id"]),
                "supplier_id": int(supplier["supplier_id"]),
                "location": random.choice(locations),
                "on_hand_qty": on_hand,
                "consigned_qty": consigned,
                "in_transit_qty": random.randint(0, 250),
            })
            inv_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} inventory rows")
    return df


def generate_forecast(parts_df, platforms_df, weeks=12):
    records = []
    fc_id = 1
    start = datetime.now().date()

    for _, part in parts_df.iterrows():
        for w in range(weeks):
            week = start + timedelta(weeks=w)

            if part["lifecycle_state"] == "Active":
                base = random.randint(80, 500)
                ftype = "Sustaining"
            elif part["lifecycle_state"] == "EOL":
                base = random.randint(20, 160)
                ftype = "Pre-EOL"
            else:
                base = random.randint(0, 60)
                ftype = "Final-Buy"

            forecasted = max(0, base + random.randint(-50, 50))

            records.append({
                "forecast_id": fc_id,
                "platform_id": random.randint(1, len(platforms_df)),
                "part_id": int(part["part_id"]),
                "forecast_week": week,
                "forecasted_units": forecasted,
                "forecast_type": ftype,
            })
            fc_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} forecast rows")
    return df


def generate_ltb_orders(parts_df, platforms_df, suppliers_df):
    eol_parts = parts_df[parts_df["lifecycle_state"].isin(["EOL", "EOSS"])]

    records = []
    ltb_id = 1

    for _, part in eol_parts.iterrows():
        if random.random() < 0.60:
            order_date = fake.date_between(start_date="-12m", end_date="today")
            records.append({
                "ltb_id": ltb_id,
                "platform_id": random.randint(1, len(platforms_df)),
                "part_id": int(part["part_id"]),
                "supplier_id": random.randint(1, len(suppliers_df)),
                "order_date": order_date,
                "qty_ordered": random.randint(1000, 12000),
                "expected_delivery_date": fake.date_between(start_date="today", end_date="+4m"),
                "order_reason": random.choice(["EOL Final Buy", "Service Buffer", "Safety Stock"]),
                "gsm_approver": fake.name(),
            })
            ltb_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} LTB order rows")
    return df


def generate_excess(parts_df, inventory_df, forecast_df):
    records = []
    calc_id = 1

    fc_sum = forecast_df.groupby("part_id")["forecasted_units"].sum().to_dict()

    for _, inv in inventory_df.iterrows():
        part_id = int(inv["part_id"])
        total_fc = int(fc_sum.get(part_id, 0))
        on_hand = int(inv["on_hand_qty"])
        excess = on_hand - total_fc

        if excess > 100:
            scrap = int(excess * 0.70)
            hold = excess - scrap
            records.append({
                "calc_id": calc_id,
                "part_id": part_id,
                "supplier_id": int(inv["supplier_id"]),
                "calc_date": datetime.now().date(),
                "on_hand": on_hand,
                "total_forecast_remaining": total_fc,
                "calculated_excess": excess,
                "scrap_recommended": scrap,
                "hold_recommended": hold,
                "consignment_eligible": excess > 500,
            })
            calc_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} excess calc rows")
    return df


def generate_scrap_approvals(parts_df, excess_df):
    if len(excess_df) == 0:
        df = pd.DataFrame(columns=[
            "approval_id", "part_id", "scrap_qty", "scrap_value", "gsm_approver",
            "approval_level", "approval_date", "status", "regrello_workflow_id"
        ])
        print("✅ Generated 0 scrap approvals (no excess)")
        return df

    sample = excess_df.sample(frac=0.30, random_state=42)
    part_cost = parts_df.set_index("part_id")["unit_cost"].to_dict()

    records = []
    appr_id = 1
    for _, row in sample.iterrows():
        pid = int(row["part_id"])
        scrap_qty = int(row["scrap_recommended"])
        scrap_value = round(scrap_qty * float(part_cost.get(pid, 10.0)), 2)

        records.append({
            "approval_id": appr_id,
            "part_id": pid,
            "scrap_qty": scrap_qty,
            "scrap_value": scrap_value,
            "gsm_approver": fake.name(),
            "approval_level": random.randint(1, 3),
            "approval_date": fake.date_between(start_date="-90d", end_date="today"),
            "status": random.choice(["Approved", "Pending", "Rejected"]),
            "regrello_workflow_id": f"WF-{random.randint(10000, 99999)}",
        })
        appr_id += 1

    df = pd.DataFrame(records)
    print(f"✅ Generated {len(df)} scrap approval rows")
    return df


def save(df, name):
    path = os.path.join(OUT_DIR, f"{name}.csv")
    df.to_csv(path, index=False)
    print(f"💾 saved: {path}  rows={len(df)}")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  SYNTHETIC E&O DATA GENERATOR")
    print("=" * 50 + "\n")

    platforms_df = generate_platforms()
    suppliers_df = generate_suppliers()
    parts_df = generate_parts(platforms_df)
    bom_df = generate_bom(parts_df, platforms_df)
    inventory_df = generate_inventory(parts_df, suppliers_df)
    forecast_df = generate_forecast(parts_df, platforms_df)
    ltb_df = generate_ltb_orders(parts_df, platforms_df, suppliers_df)
    excess_df = generate_excess(parts_df, inventory_df, forecast_df)
    scrap_df = generate_scrap_approvals(parts_df, excess_df)

    save(platforms_df, "dim_platform")
    save(suppliers_df, "dim_supplier")
    save(parts_df, "dim_part")
    save(bom_df, "fact_bom")
    save(inventory_df, "fact_inventory")
    save(forecast_df, "fact_forecast")
    save(ltb_df, "fact_ltb_orders")
    save(excess_df, "fact_excess_calculation")
    save(scrap_df, "fact_scrap_approval")

    # Sanity check: uniqueness
    uniq = parts_df["part_number"].nunique()
    print(f"\n✅ Part numbers unique: {uniq}/{len(parts_df)}")

    print("\nDone.\n")
