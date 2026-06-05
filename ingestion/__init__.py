"""Data-ingestion package for the water-quality monitoring platform.

Modules
-------
groundwater_client    : pulls groundwater level + salinity from the public
                        groundwater REST API.
rainfall_client       : pulls daily rainfall from the public climate data API.
surface_water_client  : pulls near-real-time surface-water levels.
load_to_sql           : upserts the collected frames into Azure SQL.
run_ingestion         : CLI entry point used by the Data Factory pipeline.
"""
