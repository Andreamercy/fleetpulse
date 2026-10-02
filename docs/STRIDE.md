# STRIDE threat model

Trust boundaries: (1) vehicle/OEM -> broker, (2) browser -> API, (3) API/ingest -> datastores, (4) agent planner -> tools, (5) CI/CD -> cluster.

| # | Threat (STRIDE) | Asset / flow | Mitigation | Evidence |
|---|---|---|---|---|
| 1 | **S**poofing a vehicle | Device -> broker | mTLS client certs per OEM/device; VIN validated; schema-validated payloads; unknown format -> DLQ | tests: invalid VIN/DTC/oem -> DLQ |
| 2 | **S**poofing a user | Browser -> API | OIDC JWT; mandatory exp/iat/aud/iss; reject alg=none & wrong key | test_api authn tests |
| 3 | **T**ampering with telemetry | Broker/ingest | TLS 1.3; idempotency key (vin,seq); Avro/JSON schema validation; ACLs: only ingest may write `alerts` | processor tests |
| 4 | **T**ampering with audit | Audit log | hash chain (SHA-256, prev-hash); DB role INSERT/SELECT only; WORM export | test_audit_endpoint (tamper detected) |
| 5 | **R**epudiation | User/agent actions | every read, agent call, approval, erasure -> audit with actor+tenant | agent/API tests |
| 6 | **I**nformation disclosure - cross-tenant | API/DB | tenant from JWT; tenant filter in SQL; Postgres RLS; same 404 for foreign/absent IDs | isolation tests, BDD scenario |
| 7 | **I**nformation disclosure - location privacy | Responses | viewers/analysts get geohash-masked coordinates; erasure endpoint; retention TTL | masking + erasure tests |
| 8 | **I**nformation disclosure - secrets | Config | no secrets in repo (.env.example only); secret manager + External Secrets; RDS-managed password | Terraform `manage_master_user_password` |
| 9 | **D**oS on API | API | token-bucket rate limit (429 + Retry-After); pagination caps (limit<=100); bounded queries | rate-limit tests |
| 10 | **D**oS on pipeline | Broker/ingest | bounded buffers + back-pressure (pause consume, producer queue full -> wait); burst headroom via partitions & HPA; DLQ for poison messages | ingest design; chaos script |
| 11 | **E**levation - RBAC bypass | API | role dependency on each route; mutating agent tool needs fleet_manager + human approval | RBAC & agent tests |
| 12 | **E**levation - prompt injection | Agent | planner output untrusted: allow-list, arg validation, tenant from JWT, 3-step cap, structured results only | test_agent_resists_malicious_planner_output |
| 13 | **T/E** supply chain | CI/CD, images | pip-audit, Trivy, non-root read-only containers, pinned base image, branch protection | pip-audit run: no known vulns; Trivy defined in CI |
| 14 | **I** web client injection (XSS) | UI | all API strings HTML-escaped before rendering; CSP header; no inline user content | UI code review |

Residual risks: audit entries retain a VIN after erasure (legal-obligation retention; pseudonymise on a schedule); the dev token endpoint must stay disabled outside ENV=dev; DAST (OWASP ZAP) and Trivy are wired in CI but were not executed in the authoring sandbox.
