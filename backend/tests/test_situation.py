import pytest

from backend.situation import classify, goal_situation


@pytest.mark.parametrize(
    "s, o, came_back, expected",
    [
        (1, 0, False, "선제골"),
        (1, 1, True, "동점골"),
        (2, 1, True, "역전골"),
        (2, 1, False, "앞서가는골"),
        (1, 2, False, "추격골"),
        (1, 3, False, "추격골"),
        (2, 0, True, "달아나는골"),
        (3, 1, True, "달아나는골"),
    ],
)
def test_classify(s, o, came_back, expected):
    assert classify(s, o, came_back) == expected


@pytest.mark.parametrize(
    "previous, expected",
    [
        (["B", "A"], "역전골"),  # 0:1 -> 1:1 -> 2:1
        (["A", "B"], "앞서가는골"),  # 1:0 -> 1:1 -> 2:1
        (["B", "A", "A", "B"], "앞서가는골"),  # 0:1 -> 1:1 -> 2:1 -> 2:2 -> 3:2
    ],
)
def test_goal_situation_looks_at_who_made_the_tie(previous, expected):
    assert goal_situation(previous, "A") == expected
