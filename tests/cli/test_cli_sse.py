from app.cli.sse import SseFrame, iter_sse_frames


def test_sse_parser_handles_metadata_multiline_data_and_heartbeat():
    frames = list(iter_sse_frames([
        ": keep-alive",
        "id: evt-1",
        "event: assistant.delta",
        "data: {\"text\":\"hello",
        "data: world\"}",
        "retry: 1500",
        "",
    ]))
    assert frames == [SseFrame('{"text":"hello\nworld"}', "assistant.delta", "evt-1", 1500)]


def test_sse_parser_ignores_invalid_retry_and_nul_ids():
    frames = list(iter_sse_frames([
        "id: bad\x00id",
        "retry: nope",
        "data: ok",
        "",
    ]))
    assert frames == [SseFrame("ok")]


def test_sse_parser_flushes_final_frame_without_blank_line():
    assert list(iter_sse_frames([b"data: final"])) == [SseFrame("final")]
