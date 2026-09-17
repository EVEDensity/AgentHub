from app.errors import error_envelope

def test_error_envelope_stable_shape_for_validation():
    value = error_envelope(ValueError("bad"))
    assert value.to_dict() == {
        "errorType": "invalid_request", "category": "validation",
        "retryable": False, "message": "bad", "details": {},
    }
