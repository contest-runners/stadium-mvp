"""Pure functions that name a goal's situation."""


def classify(s: int, o: int, came_back: bool) -> str:
    """Situation of a goal. s and o are the scoring team's and the opponent's score right after it.

    came_back: the previous valid goal was also the scoring team's, so the tie this goal
    breaks was one they made from behind (역전골) rather than one the opponent made (앞서가는골).
    """
    if s == 1 and o == 0:
        return "선제골"
    if s == o:
        return "동점골"
    if s == o + 1:
        return "역전골" if came_back else "앞서가는골"
    if s < o:
        return "추격골"
    return "달아나는골"


def goal_situation(previous: list[str], team: str) -> str:
    """Situation of a new goal by `team`. previous is the scoring team of each earlier valid goal, in order."""
    s = previous.count(team) + 1
    o = len(previous) - previous.count(team)
    return classify(s, o, bool(previous) and previous[-1] == team)


def scorer_situation(player_goals: int, situation: str) -> str:
    """Situation for the 2nd commentary: 해트트릭 when the scorer now has exactly 3 valid goals."""
    return "해트트릭" if player_goals == 3 else situation
