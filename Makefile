.PHONY: up down test cov bdd load explain bench
up: ; cp -n .env.example .env; docker compose up --build
down: ; docker compose down -v
test: ; pytest -q
cov: ; pytest -q --cov --cov-report=term-missing --cov-report=xml --cov-report=html
bdd: ; behave features
explain: ; python db/explain/run_explain.py          # real EXPLAIN ANALYZE before/after on embedded Postgres 16
bench: ; python loadtest/bench_local.py
load: ; k6 run loadtest/k6_api.js
chaos: ; bash loadtest/chaos_kill_broker.sh
