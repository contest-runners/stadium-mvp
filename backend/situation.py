"""Pure functions that name a goal's situation."""


def classify(s: int, o: int, trailed_before: bool) -> str:
    """Situation of a goal. s and o are the scoring team's and the opponent's score right after it."""
    if s == 1 and o == 0:
        return "선제골"
    if s == o:
        return "동점골"
    if s == o + 1:
        return "역전골" if trailed_before else "앞서가는골"
    if s < o:
        return "추격골"
    return "달아나는골"


def has_trailed(scoring_teams: list[str], team: str) -> bool:
    """Whether `team` was ever behind, replaying the earlier valid goals (scoring team of each, in order)."""
    mine = theirs = 0
    for t in scoring_teams:
        if t == team:
            mine += 1
        else:
            theirs += 1
        if mine < theirs:
            return True
    return False


def scorer_situation(valid_goals: list[dict], team: str, scorer: str, situation: str) -> str:
    """Situation for the 2nd commentary: 해트트릭 when the scorer now has exactly 3 valid goals for the team."""
    count = sum(1 for g in valid_goals if g["team"] == team and g["scorer"] == scorer)
    return "해트트릭" if count == 3 else situation
