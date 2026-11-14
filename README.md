# SIG Predictions Cup Trading Bot


A Python bot that trades US 2026 election markets (Senate, House, Governor and chamber control) on SIG's prediction exchange. It compares SIG's prices with the prices on Kalshi and Polymarket and buys where SIG looks wrong. Prices are in SUSQies.

Disclaimer: Not financial advice.May not work with new competition formats. With DRY_RUN = False the bot sends real orders. 

How it works
Each loop, which takes a few seconds, the bot does the following:

Fetch prices from SIG, Kalshi and Polymarket at the same time.
Build a fair value for each outcome.
Compare the fair value with SIG's price to get an edge.
Rank and size the opportunities across two books.
Place limit orders, cancel whatever doesn't fill, and log everything.
Fair value
The fair value is the 50/50 average of the Kalshi and Polymarket midpoints.
A source is dropped if it has no two-sided quote, a quote older than 60s, or a spread over 3c.
If both sources are present and differ by more than 4c, there is no fair value and the bot skips the market.
Each race's fair values are scaled to add up to exactly 1.
On election night Polymarket is the required source and Kalshi is optional. When Kalshi settles or drops out, the race trades on Polymarket alone.
Edge and scoring
Edge = fair − price, where price is the ask for a Yes and the equivalent price for a No.
ROI = edge ÷ price.
z = (fair − price − haircut) ÷ √(fair × (1 − fair)). This is the edge per unit of the bet's own risk, a per-bet Sharpe ratio. It is the same at any price, and it doesn't favour a long shot whose small edge rides on a big chance of losing.
Kelly (v1 only) = (fair − price) ÷ (1 − price), sized at half Kelly.
Strategy v1 (the default)
It takes the top 4 opportunities by Kelly with a minimum edge of 7c. It swaps a held bet out when a new one beats it by a set margin. It sells only when the edge turns negative.

Strategy v2 (python3 main.py --strategy v2)
It runs two books that share the account.

Hold book (structural mispricings, held to resolution)

Bets are grouped by fair probability into five buckets, with a share of the book each:

Bucket	Probability	Share of the hold book
longshot	0–20%	2%
unlikely	20–40%	10%
coinflip	40–60%	15%
favourite	60–90%	33%
sure bet	90–100%	40%
Each bucket has a limited number of slots (3, 5, 4, 6 and 6, from sure bet down to longshot).

Entry needs edge ≥ 5c, z ≥ 0.25 and a SIG spread no wider than 5c. A riskier bucket's pick must also beat the ROI of the safer picks.

The minimum ROI that z ≥ 0.25 implies is Z√(p(1−p)) / (p − Z√(p(1−p))), about 6% at p 0.95, 17% at 0.75 and 33% at 0.5.

A held bet is replaced only when a new one has at least 0.25 more z. The only exit is a negative edge.

Fast book (SIG lags the real markets)

Usual gap M = the median of (fair − SIG midpoint) over 48 hours, sampled every 15 minutes.
A market is structural if M ≥ 3c and the 10th percentile of the 48-hour gap and the median of the last 2 hours both stay above hurdle = min(0.8·M, M − 0.01).
Fast part = executable edge − the usual gap. The bot buys when the fast part is at least 4c and the SIG spread is at most 3c.
It holds at most 5 positions, each at most 20% of the fast book. They are ranked by fast part in cents.
It sells when SIG has caught up to the usual gap, after 48 hours (a time stop), or on a swap that needs 0.25 more z.
Entries also need minimum traded volume, with smaller slices in thin markets.
Election-night guard (REGIME = "election")
Rule	Setting
Polymarket must be fresh	fetched within 15s, else the race is skipped
Breaker	a fair that moves over 15c in 60s and is backed only by Kalshi pauses the race for 3 minutes
Persistence	a changed fair is used after it holds within 3c for 2 loops
Uncertainty haircut	s = max(1c, source disagreement, half the widest spread, 0.5 × the 60s move), taken off the edge
Half size	buys in races not in calls.json get half the single-order cap
Hand controls	calls.json marks a decided race; pause.json leaves a race alone. Both are read every loop
Regimes
Timing comes from REGIME. Order timeout and loop pause are in seconds.

slow: 30 / 30
normal: 10 / 20
election: 4 / 6
Risk controls
Limits in settings.json: max invested, per race, per position, per book, single order as a share of equity (2%), daily loss, and consecutive errors.
Kill switches:
a STOP file in the project folder
a daily-loss limit
5 loop errors in a row
Other protections:
limit orders only, at the planned price, cancelled if unfilled
a 5-minute cooldown against flip-flopping
top-ups of a bet down 10c or more are frozen for 6 hours
sell-side exits are never blocked by the invest cap
API rate budgets of 95 reads and 27 writes a minute
Defaults: DRY_RUN should default to True.
Optional features (all off by default)
Patient exits and entries: resting limit orders instead of crossing the spread.
Early exits: listing fast positions at their structural value.
Project layout
File	What it does
main.py	the loop
strategy.py	fair value, edge and z
gapmodel.py, fast.py, hold.py	the gap model and the two books
planner.py, trading.py	planning and sizing
guard.py	election guard
sig.py, kalshi.py, polymarket.py	data sources
trader.py, patient.py	order execution
safety.py	kill switches
lean.py, display.py	console reports
tests/	about 540 offline tests (python3 -m unittest discover -s tests -t .)
Setup

pip install -r requirements.txt
echo "SIG_API_KEY=your_key" > .env
python3 main.py --strategy v2 --once      # dry run, one loop
Before you publish, check these. I haven't put them in the draft.

What SIG is: I don't know how you want to describe the competition, so write that line yourself.
Your results: I haven't included any, so add them if you want. The log shows a first live run that lost money to churn on 24 Oct. The numbers above are the current settings, not a record of what worked.
settings.json: your current limits are 50% invested, 25% per book and a 50% daily loss. I left the values out of the README so it doesn't go stale.
