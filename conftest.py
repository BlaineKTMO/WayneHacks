"""Tests run against the demo site and the classic demo map unless a test opts into another.
(Importing edge/fleet_edge/bridge.py sets SITE/WAREHOUSE_MAP defaults for real runs; pin them
here, at collection time and around every test, so nothing leaks between tests.)"""

import os

import pytest

os.environ["SITE"] = "demo"
os.environ["WAREHOUSE_MAP"] = "demo"


@pytest.fixture(autouse=True)
def _pin_site_and_map():
    saved = dict(os.environ)
    os.environ["SITE"] = "demo"
    os.environ["WAREHOUSE_MAP"] = "demo"
    yield
    os.environ.clear()
    os.environ.update(saved)
