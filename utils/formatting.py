"""Formatting utilities for sport health bot."""
from typing import Optional


def sparkline(values, width=7):
    """Unicode sparkline chart from numeric values."""
    clean = [v for v in values if v is not None]
    if not clean:
        return "\u2581" * width
    mn, mx = min(clean), max(clean)
    rng = mx - mn if mx != mn else 1
    chars = ["\u2581", "\u2582", "\u2583", "\u2584", "\u2585", "\u2586", "\u2587", "\u2588"]
    return "".join(chars[max(0, min(7, int(round((v - mn) / rng * 7)))) if v is not None else 0] for v in values[-width:])


def sparkline_colored(values, width=7, invert=False):
    """Colored sparkline using emoji circles."""
    clean = [v for v in values if v is not None]
    if not clean:
        return "\u2014" * width
    result = ""
    for v in values[-width:]:
        if v is None:
            result += "\u2014"
            continue
        if invert:
            if v >= 6: result += "\U0001f534"
            elif v >= 4: result += "\U0001f7e1"
            else: result += "\U0001f7e2"
        else:
            if v >= 6: result += "\U0001f7e2"
            elif v >= 4: result += "\U0001f7e1"
            else: result += "\U0001f534"
    return result


def score_bar(score, maximum=7):
    """Visual bar: filled + empty blocks with score."""
    if score is None:
        return "\u2014"
    filled = max(0, min(maximum, int(score)))
    return "\u2593" * filled + "\u2591" * (maximum - filled) + f" {score}/{maximum}"


def trend_arrow(current, previous, invert=False):
    """Trend indicator: up/down/stable."""
    if current is None or previous is None:
        return "\u2014"
    diff = current - previous
    if abs(diff) < 0.3:
        return "\u2192 \u0441\u0442\u0430\u0431\u0438\u043b\u044c\u043d\u043e"
    if invert:
        diff = -diff
    return f"\u2191 +{diff:.1f}" if diff > 0 else f"\u2193 {diff:.1f}"


def get_rank(streak):
    """Gamification rank based on survey streak."""
    if streak >= 30:
        return "\U0001f947 \u0417\u043e\u043b\u043e\u0442\u043e"
    elif streak >= 14:
        return "\U0001f948 \u0421\u0435\u0440\u0435\u0431\u0440\u043e"
    elif streak >= 7:
        return "\U0001f949 \u0411\u0440\u043e\u043d\u0437\u0430"
    elif streak >= 3:
        return "\u2b50 \u0421\u0442\u0430\u0440\u0442"
    return "\U0001f331 \u041d\u043e\u0432\u0438\u0447\u043e\u043a"


def get_score_emoji(score):
    """Color emoji for score value (1-7 scale)."""
    if score is None:
        return "\u2753"
    s = int(score)
    if s <= 2:
        return "\U0001f534"
    elif s <= 4:
        return "\U0001f7e1"
    elif s <= 6:
        return "\U0001f7e2"
    return "\U0001f48e"
