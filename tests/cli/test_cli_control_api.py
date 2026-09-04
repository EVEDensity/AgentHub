from unittest.mock import Mock

from app.cli.control_api import ArtifactApi, DecisionApi, MissionApi


def test_control_api_facades_delegate_without_business_state():
    client = Mock()
    client.create_and_start_mission.return_value = {"id": "m-1"}
    client.work_units.return_value = [{"id": "wu-1"}]
    client.decisions.return_value = [{"id": "d-1"}]
    client.artifacts.return_value = [{"id": "a-1"}]

    assert MissionApi(client).create(title="t", objective="o", time_seconds=10) == {"id": "m-1"}
    assert MissionApi(client).work_units("m-1") == [{"id": "wu-1"}]
    assert DecisionApi(client).pending("m-1") == [{"id": "d-1"}]
    assert ArtifactApi(client).list("m-1") == [{"id": "a-1"}]
    client.resolve_decision.assert_not_called()
