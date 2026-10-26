"""Performance numbers for an equity curve. Pure: a list of (unix time, equity in SUSQies) in, numbers out.

Daily returns use the last equity of each UTC day, starting from the first point. Volatility and Sharpe are annualised with
sqrt(365) (the market trades every day); the risk-free rate is 0 because SUSQies earn no interest. With a few weeks of data
these are rough: the Sharpe ratio of 24 daily returns has a wide error band.
"""
from math import sqrt
from statistics import mean, stdev

DAY = 86400
DAYS_PER_YEAR = 365


def daily_closes(equity):
    """[(day start, last equity of that UTC day)] for the days after the first point, led by the first point itself."""
    last = {}
    for t, value in equity[1:]:
        last[t - t % DAY] = value
    return [(equity[0][0], equity[0][1])] + sorted(last.items())


def daily_returns(equity):
    """[(day start, return of that day)]: each UTC day's close against the previous close (the first against the first point)."""
    closes = daily_closes(equity)
    return [(day, value / before - 1) for (_, before), (day, value) in zip(closes, closes[1:]) if before]


def volatility(returns, annualise=False):
    """Sample standard deviation of the returns (None under 2 of them)."""
    if len(returns) < 2:
        return None
    sd = stdev(returns)
    return sd * sqrt(DAYS_PER_YEAR) if annualise else sd


def sharpe(returns, annualise=False):
    """Mean return over its standard deviation (None when there is no spread to divide by)."""
    sd = volatility(returns)
    if not sd:
        return None
    ratio = mean(returns) / sd
    return ratio * sqrt(DAYS_PER_YEAR) if annualise else ratio


def max_drawdown(equity):
    """Largest peak-to-trough fall: {"value", "pct", "peak_time", "trough_time", "recovery_time"} (recovery_time is when the
    curve first got back to the peak, None while it has not)."""
    peak_t, peak = equity[0]
    worst = (0.0, equity[0][0], equity[0][0], peak)  # (fall, peak time, trough time, peak value)
    for t, value in equity:
        if value >= peak:
            peak_t, peak = t, value
        elif peak - value > worst[0]:
            worst = (peak - value, peak_t, t, peak)
    fall, peak_time, trough_time, peak_value = worst
    recovery = next((t for t, value in equity if t > trough_time and value >= peak_value), None) if fall else None
    return {"value": fall, "pct": fall / peak_value if peak_value else 0.0, "peak_time": peak_time,
            "trough_time": trough_time, "recovery_time": recovery}


def summarize_metrics(equity):
    """Every number the backtest reports for one equity curve (a list of at least two (time, equity) points)."""
    start, end = equity[0][1], equity[-1][1]
    returns = [r for _, r in daily_returns(equity)]
    days = len(returns)
    return {
        "start": start, "end": end, "gain": end - start, "return_pct": (end / start - 1) if start else None,
        "gain_per_day": (end - start) / days if days else None, "days": days,
        "volatility_daily": volatility(returns), "volatility_annual": volatility(returns, annualise=True),
        "sharpe_daily": sharpe(returns), "sharpe_annual": sharpe(returns, annualise=True),
        "max_drawdown": max_drawdown(equity),
        "best_day": max(returns, default=None), "worst_day": min(returns, default=None),
        "days_up": sum(r > 0 for r in returns) / days if days else None,
    }
