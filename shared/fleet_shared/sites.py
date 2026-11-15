"""Sites (warehouses) and the AWS names that belong to each. Standard library only.

  demo  Demo site: simulated fleet (hosted on ECS), 3-aisle mission map. Owns the shared
        pieces too: sign-in, website, device registry, SageMaker role.
  wh1   Warehouse 1: the real tabletop, edge laptop + Free-WiLi.
"""

SITES = {
    "demo": {"name": "Demo site (simulated)", "map": "mission", "stack": "ForkliftFleet", "prefix": "Fleet"},
    "wh1": {"name": "Warehouse 1", "map": "table", "stack": "ForkliftFleet-wh1", "prefix": "FleetWh1"},
}


def table_name(site: str, name: str) -> str:
    """table_name("wh1", "Forklifts") -> "FleetWh1Forklifts"."""
    return SITES[site]["prefix"] + name


def stack_name(site: str) -> str:
    return SITES[site]["stack"]
