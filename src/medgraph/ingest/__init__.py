"""Ingestion: read source records into medgraph's typed model.

Sources are FHIR R4 bundles and, through validated LLM extraction, free-text or PDF lab
reports. Every ingested item keeps a reference to where it came from.
"""
