import pytest

from backend.situation import classify


@pytest.mark.parametrize(
    "s, o, trailed, expected",
    [
        (1, 0, False, "선제골"),
        (1, 1, True, "동점골"),
        (2, 1, True, "역전골"),
        (2, 1, False, "앞서가는골"),
        (1, 2, True, "추격골"),
        (1, 3, True, "추격골"),
        (2, 0, False, "달아나는골"),
        (3, 1, False, "달아나는골"),
    ],
)
def test_classify(s, o, trailed, expected):
    assert classify(s, o, trailed) == expected
