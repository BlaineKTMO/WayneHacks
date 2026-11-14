"""Shared contract for the forklift fleet: MQTT topics, message shapes, warehouse graph.

Standard library only, so it runs on the edge laptop, in Lambda and in tests unchanged.
Conventions: metres, radians, world frame from the corner tags, ts = epoch milliseconds,
forklift IDs look like "fl-01".
"""
