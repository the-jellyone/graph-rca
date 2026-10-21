import json
from types import SimpleNamespace

import pytest

from rca_investigation.faults import LabDriver
from rca_investigation.contracts import TopologySnapshot


def test_lab_driver_targets_isolated_compose_project_and_checks_labels(tmp_path):
    topology = TopologySnapshot("v1", ("orders",), ("Service",), ())
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert kwargs["shell"] is False
        if argv[1:3] == ["compose", "-p"]:
            assert argv[3] == "rca-lab"
            payload = [{"Service": "orders", "Project": "rca-lab", "ID": "a" * 64}]
        else:
            payload = [{"Id": "a" * 64,
                        "Config": {"Labels": {"com.docker.compose.project": "rca-lab",
                                                "com.docker.compose.service": "orders"}},
                        "State": {"Status": "running"}}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    driver = LabDriver(topology, tmp_path, runner=runner)
    assert driver.resolve() == {"orders": "a" * 64}
    assert driver.state("orders")["state"] == "running"
    assert len(calls) == 2


def test_lab_driver_refuses_wrong_project_label(tmp_path):
    topology = TopologySnapshot("v1", ("orders",), ("Service",), ())

    def runner(argv, **kwargs):
        payload = ([{"Service": "orders", "Project": "rca-lab", "ID": "a" * 64}]
                   if argv[1] == "compose" else
                   [{"Id": "a" * 64,
                     "Config": {"Labels": {"com.docker.compose.project": "other", "com.docker.compose.service": "orders"}},
                     "State": {"Status": "running"}}])
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    driver = LabDriver(topology, tmp_path, runner=runner)
    driver.resolve()
    with pytest.raises(RuntimeError, match="registry mismatch"):
        driver.state("orders")
