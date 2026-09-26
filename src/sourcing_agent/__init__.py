"""Sourcing agent: BOM → CBOM from approved suppliers, plus discovery of new suppliers.

core/       shared engine: errors, paths, suppliers.toml, domain helpers, agent loop, live/batch runner, API pricing, calibration
cbom/       CBOM generation: BOM/CBOM files, quotes and price rule, sourcing worker, quote cache, pipeline, estimate, commands
suppliers/  supplier management: win rates, discovery scout, free screening, trials, candidate registry, commands

Imports flow one way: suppliers → cbom → core. core never imports cbom or suppliers; cbom never imports suppliers.
"""
