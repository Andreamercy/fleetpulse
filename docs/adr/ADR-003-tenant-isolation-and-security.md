# ADR-003: Tenant isolation enforced at three layers
**Status:** accepted
**Context:** A cross-tenant leak is the worst failure for a multi-tenant fleet platform.
**Decision:** (1) Tenant comes only from the verified JWT claim, never from request parameters or model output. (2) Every repository method requires `tenant` and filters in SQL. (3) PostgreSQL Row-Level Security via `SET app.tenant_id` per connection as defence in depth. Cross-tenant lookups return the same 404 as non-existent IDs (no enumeration). Roles: admin / fleet_manager / analyst / viewer; viewers get geohash-masked (~5 km) locations.
**Auth:** OIDC JWT (RS256 via JWKS in prod; HS256 only when ENV=dev; outside dev the API fails closed (HTTP 500) unless JWKS_URL is configured). `exp, iat, aud, iss, sub` are mandatory; `alg=none` and wrong-key tokens are rejected (tests).
**Consequences:** (+) three independent failures needed for a leak; (-) per-request `set_config` round trip (batched with the query).
