"""Typed contract smoke tests using replay examples."""

import json
from pathlib import Path

from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotResultBundle
from hpc_site_preflight.site_info.models import SiteInfo

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_example_site_info_validates() -> None:
    model = SiteInfo.model_validate(_load("examples/replay/anvil/site-info.json"))
    assert model.site_id == "purdue-anvil"
    assert model.scheduler_hint == "slurm"


def test_example_measurements_validate() -> None:
    model = MeasurementBundle.model_validate(
        _load("examples/replay/anvil/login-measurements.json")
    )
    assert model.mode == "replay"


def test_example_pilot_results_validate() -> None:
    model = PilotResultBundle.model_validate(_load("examples/replay/anvil/pilot-results.json"))
    assert model.site_id == "purdue-anvil"
