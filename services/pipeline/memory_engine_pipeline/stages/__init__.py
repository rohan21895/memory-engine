"""Pipeline stages.

This file imports nothing, following the convention the intelligence packages
already established: an eager re-export here would make importing any one stage
drag in every other stage, so a missing optional dependency in the render probe
would break a test that only wanted the inventory. Import the stage you need.
"""
