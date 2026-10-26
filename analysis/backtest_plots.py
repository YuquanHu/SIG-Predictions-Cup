"""Charts of a backtest's SUSQies performance, saved as PNG files next to summary.txt. Takes the Results and summaries from
backtest_v2 (the first result is the baseline run; the others are shown behind it)."""
import os
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from backtest_metrics import daily_closes  # noqa: E402

GAIN, LOSS, BASE, FAINT = "#2a9d6f", "#d1495b", "#3d5a99", "#b8c0cc"
START_EQUITY = 100_000.0


def dates(times):
    return [datetime.fromtimestamp(t, timezone.utc) for t in times]


def style(ax, ylabel):
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def equity_and_drawdown(results, path):
    base = results[0]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for r in results[1:]:
        top.plot(dates([t for t, _ in r.equity]), [e for _, e in r.equity], color=FAINT, lw=1)
    t = dates([t for t, _ in base.equity])
    top.plot(t, [e for _, e in base.equity], color=BASE, lw=2.2, label=f"total ({base.name})")
    for book, color in (("hold", "#8a6fbf"), ("fast", "#e08a2e")):
        top.plot(t, [START_EQUITY + p for _, p in base.book_pnl[book]], color=color, lw=1.2, ls="--",
                 label=f"{book} book (start + its profit)")
    top.axhline(START_EQUITY, color="#888", lw=0.8)
    style(top, "equity (SUSQies)")
    top.set_title("Backtest equity in SUSQies" + (f" (grey: the other {len(results) - 1} runs)" if len(results) > 1 else ""))
    top.legend(loc="best", frameon=False)
    peak, under = 0.0, []
    for _, e in base.equity:
        peak = max(peak, e)
        under.append((e / peak - 1) * 100)
    bottom.fill_between(t, under, 0, color=LOSS, alpha=0.5)
    low = min(range(len(under)), key=under.__getitem__)
    bottom.annotate(f"max drawdown {under[low]:.2f}%", (t[low], under[low]), xytext=(8, -4), textcoords="offset points", color=LOSS)
    style(bottom, "drawdown (%)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def daily_pnl(results, path):
    closes = daily_closes(results[0].equity)
    days = dates([d for d, _ in closes[1:]])
    pnl = [after - before for (_, before), (_, after) in zip(closes, closes[1:])]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar(days, pnl, color=[GAIN if p >= 0 else LOSS for p in pnl], width=0.8)
    ax.axhline(0, color="#888", lw=0.8)
    style(ax, "daily profit (SUSQies)")
    ax.set_title(f"Daily profit, {results[0].name}")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fast_trades(results, path):
    closed = [t for t in results[0].trades if t.exit_time]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.5))
    tiers = ["high", "medium", "thin", "quiet", "unknown"]
    for i, tier in enumerate(tiers):
        pnl = [t.pnl for t in closed if t.tier == tier]
        left.scatter([i + (j % 7 - 3) * 0.04 for j in range(len(pnl))], pnl, color=[GAIN if p >= 0 else LOSS for p in pnl], alpha=0.7, s=18)
    left.set_xticks(range(len(tiers)), [f"{t}\n(n={sum(c.tier == t for c in closed)})" for t in tiers])
    left.axhline(0, color="#888", lw=0.8)
    style(left, "profit per closed fast trade (SUSQies)")
    left.set_title("Fast trades by liquidity tier")
    ordered = sorted(closed, key=lambda t: t.exit_time)
    total, curve = 0.0, []
    for t in ordered:
        total += t.pnl
        curve.append(total)
    if ordered:
        right.step(dates([t.exit_time for t in ordered]), curve, where="post", color=BASE)
    right.axhline(0, color="#888", lw=0.8)
    style(right, "cumulative realised profit (SUSQies)")
    right.set_title("Closed fast trades, cumulative")
    right.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def grid_compare(summaries, path):
    rows = [s for s in summaries if s.get("total")]
    labels = [s["name"] for s in rows]
    panels = (("return (%)", [s["total"]["return_pct"] * 100 for s in rows]),
              ("volatility per day (%)", [(s["total"]["volatility_daily"] or 0) * 100 for s in rows]),
              ("max drawdown (%)", [-s["total"]["max_drawdown"]["pct"] * 100 for s in rows]),
              ("Sharpe (annualised)", [s["total"]["sharpe_annual"] or 0 for s in rows]))
    fig, axes = plt.subplots(1, 4, figsize=(15, 0.55 * len(rows) + 2.2), sharey=True)
    for ax, (title, values) in zip(axes, panels):
        ax.barh(range(len(rows)), values, color=[BASE if i == 0 else FAINT for i in range(len(rows))])
        ax.set_title(title, fontsize=10)
        ax.axvline(0, color="#888", lw=0.8)
        ax.grid(axis="x", alpha=0.25)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0].set_yticks(range(len(rows)), labels)
    axes[0].invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_all(results, summaries, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    if not results or len(results[0].equity) < 2:
        print("  nothing to plot: no equity points")
        return
    equity_and_drawdown(results, os.path.join(out_dir, "equity_drawdown.png"))
    daily_pnl(results, os.path.join(out_dir, "daily_pnl.png"))
    fast_trades(results, os.path.join(out_dir, "fast_trades.png"))
    if len(results) > 1:
        grid_compare(summaries, os.path.join(out_dir, "grid_compare.png"))
