-- db/schema.sql
-- ================
-- Dimension tables
-- ================
CREATE TABLE IF NOT EXISTS dim_part (
    part_id SERIAL PRIMARY KEY,
    part_number VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    commodity VARCHAR(100),
    unit_cost DECIMAL(10, 2),
    lifecycle_state VARCHAR(20),
    eol_date DATE,
    eoss_date DATE,
    platform_primary VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS dim_supplier (
    supplier_id SERIAL PRIMARY KEY,
    supplier_name VARCHAR(200) NOT NULL,
    country VARCHAR(50),
    consignment_partner BOOLEAN DEFAULT FALSE,
    lead_time_days INT,
    supplier_tier VARCHAR(10)
);
CREATE TABLE IF NOT EXISTS dim_platform (
    platform_id SERIAL PRIMARY KEY,
    platform_name VARCHAR(100) NOT NULL,
    business_unit VARCHAR(50),
    launch_date DATE,
    eol_date DATE
);
-- ==========
-- Fact tables
-- ==========
CREATE TABLE IF NOT EXISTS fact_bom (
    bom_id SERIAL PRIMARY KEY,
    platform_id INT REFERENCES dim_platform(platform_id),
    part_id INT REFERENCES dim_part(part_id),
    qty_per_unit INT,
    is_shared BOOLEAN DEFAULT FALSE,
    effective_start DATE,
    effective_end DATE
);
CREATE TABLE IF NOT EXISTS fact_inventory (
    inventory_id SERIAL PRIMARY KEY,
    part_id INT REFERENCES dim_part(part_id),
    supplier_id INT REFERENCES dim_supplier(supplier_id),
    location VARCHAR(100),
    on_hand_qty INT,
    consigned_qty INT,
    in_transit_qty INT,
    last_updated TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS fact_forecast (
    forecast_id SERIAL PRIMARY KEY,
    platform_id INT REFERENCES dim_platform(platform_id),
    part_id INT REFERENCES dim_part(part_id),
    forecast_week DATE,
    forecasted_units INT,
    forecast_type VARCHAR(20)
);
CREATE TABLE IF NOT EXISTS fact_ltb_orders (
    ltb_id SERIAL PRIMARY KEY,
    platform_id INT REFERENCES dim_platform(platform_id),
    part_id INT REFERENCES dim_part(part_id),
    supplier_id INT REFERENCES dim_supplier(supplier_id),
    order_date DATE,
    qty_ordered INT,
    expected_delivery_date DATE,
    order_reason TEXT,
    gsm_approver VARCHAR(100)
);
CREATE TABLE IF NOT EXISTS fact_excess_calculation (
    calc_id SERIAL PRIMARY KEY,
    part_id INT REFERENCES dim_part(part_id),
    supplier_id INT REFERENCES dim_supplier(supplier_id),
    calc_date DATE,
    on_hand INT,
    total_forecast_remaining INT,
    calculated_excess INT,
    scrap_recommended INT,
    hold_recommended INT,
    consignment_eligible BOOLEAN
);
CREATE TABLE IF NOT EXISTS fact_scrap_approval (
    approval_id SERIAL PRIMARY KEY,
    part_id INT REFERENCES dim_part(part_id),
    scrap_qty INT,
    scrap_value DECIMAL(12, 2),
    gsm_approver VARCHAR(100),
    approval_level INT,
    approval_date DATE,
    status VARCHAR(20),
    regrello_workflow_id VARCHAR(50)
);
-- ==================
-- Indexes (performance)
-- ==================
CREATE INDEX IF NOT EXISTS idx_part_lifecycle ON dim_part(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_part_eol ON dim_part(eol_date);
CREATE INDEX IF NOT EXISTS idx_inventory_part ON fact_inventory(part_id);
CREATE INDEX IF NOT EXISTS idx_excess_part ON fact_excess_calculation(part_id);