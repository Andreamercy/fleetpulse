"""Entrypoint: `uvicorn fleetpulse.api.main:app`. REPO=sql selects Postgres/ClickHouse; default is the in-memory demo repo."""
import os

from .app import create_app

repo = None
if os.getenv("REPO", "memory") == "sql":  # pragma: no cover
    from .sqlrepo import SqlRepo
    repo = SqlRepo()
app = create_app(repo)
