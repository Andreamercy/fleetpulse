-- FleetPulse relational core (system of record). PostgreSQL 16 + pgvector.
-- Third Normal Form, with ONE deliberate, documented denormalisation: tenant_id is repeated on
-- vehicle, alert and vehicle_risk (kept consistent by composite foreign keys) so every query can be
-- tenant-scoped via a single index and Row-Level Security without a join. See docs/adr/ADR-002.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenant      (id int PRIMARY KEY, name text NOT NULL UNIQUE);
CREATE TABLE plan        (id smallint PRIMARY KEY, name text NOT NULL UNIQUE, max_vehicles int NOT NULL, price_cents int NOT NULL);
CREATE TABLE subscription(id serial PRIMARY KEY, tenant_id int NOT NULL REFERENCES tenant, plan_id smallint NOT NULL REFERENCES plan,
                          status text NOT NULL CHECK (status IN ('ACTIVE','PAST_DUE','CANCELLED')), valid_from date NOT NULL, valid_to date);
CREATE TABLE role        (id smallint PRIMARY KEY, name text NOT NULL UNIQUE);
CREATE TABLE app_user    (id serial PRIMARY KEY, tenant_id int NOT NULL REFERENCES tenant, email text NOT NULL UNIQUE);
CREATE TABLE user_role   (user_id int REFERENCES app_user, role_id smallint REFERENCES role, PRIMARY KEY (user_id, role_id));

CREATE TABLE fleet (id int PRIMARY KEY, tenant_id int NOT NULL REFERENCES tenant, name text NOT NULL, UNIQUE (id, tenant_id));
-- fuel_type depends on the model, not on the vehicle -> its own table (3NF)
CREATE TABLE vehicle_model (id int PRIMARY KEY, oem text NOT NULL, model text NOT NULL, model_year smallint NOT NULL,
                            fuel_type text NOT NULL CHECK (fuel_type IN ('ICE','HYBRID','EV')), UNIQUE (oem, model, model_year));
CREATE TABLE vehicle (
  id int PRIMARY KEY, vin char(17) NOT NULL UNIQUE CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
  tenant_id int NOT NULL, fleet_id int NOT NULL, model_id int NOT NULL REFERENCES vehicle_model,
  registered_on date NOT NULL, status text NOT NULL DEFAULT 'ACTIVE',
  FOREIGN KEY (fleet_id, tenant_id) REFERENCES fleet (id, tenant_id));          -- denormalised tenant_id, FK-guarded

CREATE TABLE driver (id int PRIMARY KEY, tenant_id int NOT NULL REFERENCES tenant, pseudonym text NOT NULL);   -- no real names (synthetic/pseudonymised)
CREATE TABLE driver_assignment (id serial PRIMARY KEY, driver_id int NOT NULL REFERENCES driver, vehicle_id int NOT NULL REFERENCES vehicle,
                                valid_from timestamptz NOT NULL, valid_to timestamptz);
CREATE TABLE trip (id bigint PRIMARY KEY, vehicle_id int NOT NULL REFERENCES vehicle, driver_id int REFERENCES driver,
                   started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL, distance_km real NOT NULL, harsh_events smallint NOT NULL DEFAULT 0);

CREATE TABLE dtc_catalog (code char(5) PRIMARY KEY, system text NOT NULL, description text NOT NULL, severity smallint NOT NULL CHECK (severity BETWEEN 1 AND 5));
CREATE TABLE alert_type  (code text PRIMARY KEY, default_severity smallint NOT NULL, description text NOT NULL);
CREATE TABLE alert (
  id bigint PRIMARY KEY, vehicle_id int NOT NULL REFERENCES vehicle, tenant_id int NOT NULL REFERENCES tenant,
  type_code text NOT NULL REFERENCES alert_type, severity smallint NOT NULL, raised_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKED','CLOSED')), detail text);

CREATE TABLE vehicle_risk (vehicle_id int PRIMARY KEY REFERENCES vehicle, tenant_id int NOT NULL REFERENCES tenant,
                           score real NOT NULL CHECK (score BETWEEN 0 AND 1), model_version text NOT NULL,
                           computed_at timestamptz NOT NULL, factors jsonb NOT NULL DEFAULT '[]');
CREATE TABLE work_order (id uuid PRIMARY KEY, vehicle_id int NOT NULL REFERENCES vehicle, tenant_id int NOT NULL REFERENCES tenant,
                         reason text NOT NULL, status text NOT NULL CHECK (status IN ('PENDING_APPROVAL','APPROVED','DONE','REJECTED')),
                         created_by text NOT NULL, approved_by text, created_at timestamptz NOT NULL DEFAULT now());
-- vector layer: historical failure cases (13 = number of model features)
CREATE TABLE fault_case (id bigserial PRIMARY KEY, vehicle_id int NOT NULL REFERENCES vehicle, observed_day int NOT NULL,
                         embedding vector(13) NOT NULL, outcome text NOT NULL DEFAULT 'BREAKDOWN_7D');
CREATE TABLE audit_log (id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(), actor text NOT NULL, tenant_id int,
                        action text NOT NULL, resource text NOT NULL, detail jsonb NOT NULL DEFAULT '{}', prev_hash char(64) NOT NULL, hash char(64) NOT NULL);
-- Audit is append-only: production role gets INSERT/SELECT only.
--   REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM fleetpulse_app;

-- Row-Level Security: defence in depth behind the API's tenant filter
ALTER TABLE vehicle ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_iso_vehicle ON vehicle USING (tenant_id = current_setting('app.tenant_id', true)::int);
CREATE POLICY tenant_iso_alert   ON alert   USING (tenant_id = current_setting('app.tenant_id', true)::int);
