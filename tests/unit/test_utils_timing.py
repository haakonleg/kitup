import pytest

from kitup._utils.timing import timed


def test_timed_records_boundaries_and_duration():
    with timed() as timing:
        pass

    assert timing.end is not None
    assert timing.duration >= 0
    data = timing.as_dict()
    assert data["start"] == timing.start.isoformat()
    assert data["end"] == timing.end.isoformat()
    assert data["duration"] == timing.duration


def test_timed_records_timing_when_block_raises():
    with pytest.raises(RuntimeError, match="boom"), timed() as timing:
        raise RuntimeError("boom")

    assert timing.end is not None
    assert timing.duration >= 0
