# ADR-002: Polyglot persistence - PostgreSQL + ClickHouse + Redis (+ pgvector)
**Status:** accepted
**Context:** One SQL primary cannot absorb ~100k inserts/s into multi-index B-trees, and analytic scans would starve transactional queries (brief section 4.1). Yet billing/ownership/audit need ACID.
| Data | Store | Why | CAP / PACELC stance |
|---|---|---|---|
| Tenants, fleets, vehicles, users, subscriptions, work orders, audit | PostgreSQL (3NF) | ACID, FKs, RLS, small volume | **CP**; PC/EC: pay latency for consistency |
| Raw telemetry + history | ClickHouse (columnar, partitioned by day) | batched LSM-style inserts, 8-10x compression, fast time-range scans | **AP**; PA/EL: replicas eventually consistent, low latency |
| Latest vehicle state, rate-limit counters | Redis | O(1) key access, TTL | **AP**, loss is tolerable (rebuilt from stream) |
| Fault-case embeddings | pgvector (HNSW) | co-located with vehicles, avoids a 4th datastore at this scale | CP |
**Deliberate denormalisation:** `tenant_id` is duplicated on `vehicle`, `alert`, `vehicle_risk`, protected by a composite FK `(fleet_id, tenant_id)` so it cannot drift. Benefit: every hot query is tenant-scoped through one index and RLS needs no join. Measured effect in docs/query_plans.md (Q1, Q2).
**Consequences:** (+) each store does what it is good at; (-) 3 systems to operate; cross-store consistency is by event flow, not transactions (risk is recomputed, so it self-heals). A dedicated vector DB (Qdrant) is deferred until >10M cases.
