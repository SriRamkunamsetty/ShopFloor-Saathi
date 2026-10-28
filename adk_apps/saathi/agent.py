"""Entry point for `adk web adk_apps` / `adk eval adk_apps/saathi eval/...`."""

from saathi.agents import build_root_agent

root_agent = build_root_agent()
