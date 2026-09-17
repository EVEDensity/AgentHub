from app.cli.sse import iter_sse_frames


def test_oversized_sse_frame_is_dropped_and_next_frame_survives():
    lines = ["data: 12345", "", "data: ok", ""]
    frames = list(iter_sse_frames(lines, max_frame_chars=4))
    assert [frame.data for frame in frames] == ["ok"]


def test_oversized_line_is_dropped():
    frames = list(iter_sse_frames(["data: too-long", "", "data: ok", ""], max_line_chars=8))
    assert [frame.data for frame in frames] == ["ok"]

