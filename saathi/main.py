"""ASGI entry point: `uvicorn saathi.main:app`."""

from saathi.api import create_app

app = create_app()
