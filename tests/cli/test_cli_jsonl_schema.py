import json


def test_jsonl_record_shapes_are_versioned():
    records = [
        {"schemaVersion": 1, "type": "event", "event": {"type": "mission.created"}},
        {"schemaVersion": 1, "type": "state", "state": {"eventCount": 1}},
        {"schemaVersion": 1, "type": "result", "result": {"status": "SUCCEEDED"}},
    ]
    for record in records:
        parsed = json.loads(json.dumps(record))
        assert parsed["schemaVersion"] == 1
        assert parsed["type"] in {"event", "state", "result"}
