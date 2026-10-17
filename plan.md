Trading Bot Plan: Review and Functions
Steps in detail

0.	Cancel all open orders at the start of every loop, and get the filled share count from your SIG positions
1.	Test calls just reads fetch data, place a tiny order, cancel it, then buy and sell one small position. Make this one, markets number: 388 (Will the Republican Party win the Rhode Island Senate? ) on SIG’s site and buy a single yes contract then sell it at market, both at limit order at best current price. 
2.	Use a Data structure (hash table) for storing race object, then make the initial api call on Polymarket, Kalshi and SIG to fill it up. Each subsequent call just calls price and not everything else.
3.	Calculate the edge after each call by comparing with my fair value function, and use a list to store edges that makes it past a 4% edge (4 cents of edge) and beats the current minimum. Then convert the edge into Kelly criterion. Keep the top 4 kelly only in that list, so if the list is full and a better edge arrived then replace the lowest with the current edge. Then sort as needed. This should be logged and later displayed on a dashboard.
4.	Sort the list by Kelly. biggest first. Size = half Kelly × bankroll, investing at most 80% of total Susqies so 20% stays as cash. Subtract what is already held in that bet, so the bot doesn't keep buying every loop. Treat Dem Yes and Rep No as the same bet: buy only the cheaper one, and count both toward one limit per race.
5.	Decided whether or not to swap current position for new ones or use cash.
Value a held position at its sell price (the bid), since that's what selling returns. Its remaining expected return is (fair − bid) ÷ bid.
Only swap when there is no investable cash left. With cash available, just buy the new opportunity.
Swap if the new opportunity's expected return beats the held position's by at least 1%.
Extra exit rule: if a held position's fair value falls below its sell price, its edge is negative, so sell it whether anything better is available. If my current portfolio is already holding the best positions then do nothing and repeat the loop. \ This should be logged and later displayed on a dashboard.

6.	Execute the trade at a limit price which the edge was calculate. IF it doesn’t fill within the order time out limit, cancel, If it partially fill, cancel what didn’t fill. The timer is temporary and should be updated when entering a different regime, such as debate night or election night. This should be logged and later displayed on a dashboard. Loop pause. 




Other notes:
- every transaction should be logged and able to be displayed later. The dashboard comes later and don’t build it now. The dashboard  takes CSV file per type (opportunities, orders, fills, errors), with columns like time, race, market, side, price, fair value, edge, shares.
 
Position sizing per-race cap (30% of bankroll)
quote staleness limit (60 seconds)
kill-switch limits (stop if down 5% in a day, or 5 errors in a row)
Regime: 
Regime	Order timeout	Loop pause	When
Slow	20 seconds	60 seconds	Overnight US time, weekends, nothing happening
Normal	10 seconds	20 seconds	Most days, polls and news coming out
Election night	2 seconds	3 seconds	Results coming in, prices moving fast



Functions (not exhaustive) 
1.	Fair value: for one outcome, take Kalshi's and Polymarket's midpoints (50/50 weighting), drop any source that is stale, failed, or has a spread over 3¢. Return nothing if no source is usable, or if the two disagree by more than about 4¢. The data should come from the Race object. 

2.	Kill switch to top all trading 

3.	Dry run, True or False, with it on, it only logs the transactions but not execute them. 

Setup
•	Load races: read the config file and create a race object for each entry, with its IDs, its group and empty prices.
•	Load setting: Includes the regime, so slow, normal or election. Also include Dry Run, where it only logs trades but not execute.
Data
•	Fetch SIG prices: get the bid, ask and available size for every SIG market, and store them in the matching race with the current time.
•	Fetch Kalshi prices: the same for Kalshi.
•	Fetch Polymarket prices: the same for Polymarket.
•	Fetch account: get current cash and every position held (market, side, shares, average price).
Strategy
•	fair_value(outcome): blend healthy Kalshi and Polymarket midpoints; return nothing if neither is usable or they disagree by more than 4¢.
•	find_opportunities(): for every SIG market, compute the edge of buying Yes and No; keep edges of 4¢ or more, with their price, edge, Kelly and available size.
•	Size trade: for one opportunity, target = half Kelly × bankroll, scaled down if all targets together exceed 80%. Subtract what is already held, then cap by the race limit, the 80% investment limit and the shares available at that price. Return the number of shares to buy, or zero.
•	keep_top(list, n=4): keep only the best 4 by Kelly, one per race (cheaper of Dem Yes / Rep No).
•	Value held position: for each position owned, its fair value, its sell price, and its remaining expected return if kept.
•	Decide swaps: if cash is used up and a new opportunity beats the worst held position by at least 1%, mark that position to sell. Also mark any position with negative edge to sell.
Execution
•	Place order: send a limit order (market, side, shares, price). In dry-run mode, only log it.
•	Cancel unfilled: after the order time limit, cancel whatever part of an order didn't fill.
•	Cancel all: cancel every open order. Used by the kill switch.
Safety and records
•	Check kill switch: return true if the stop file exists, or if losses or errors have gone past their limits.
•	Log: append a line to a file for every opportunity found, order placed, fill and error.
test_connection(): fetch prices read-only; place a tiny order and cancel it; buy then sell one small position in market 388; log every step.






Main loop and logic:
load settings and races loop: cancel all → check kill switch → fetch prices and account → find opportunities → keep top 4 → exits and swaps → size → place and cancel orders → log → wait the regime’s loop pause. On exit, cancel all.

Build in stages: (1) read-only data fetching, (2) full logic in dry run, (3) live. Stop after each stage so I can test. DRY_RUN defaults to True.”

API docs
•	Kalshi: https://docs.kalshi.com 
•	Polymarket: https://docs.polymarket.com
•	SIG: attached to as a json file
•	SIG api key leave as a placeholder, I will change it later, the other 2 don’t need api keys.


Here is an example of a race In python

races = {
    "RI-SEN": {
        # ---- fixed: loaded once from races.json ----
        "name": "Rhode Island Senate",
        "group": "Senate",

        # ---- one entry per possible winner ----
        "outcomes": {
            "D": {
                "kalshi_id": "SENATERI-26-D"
                "poly_id": " 630911”
                # live, updated every loop
                "kalshi": {"bid": 0.988, "ask": 0.993, "updated": 1792038000.0},
                "poly":   {"bid": 0.969, "ask": 0.988, "updated": 1792038001.2},
            },
            "R": {
                "kalshi_id": "SENATERI-26-R",
                "poly_id": " 630912",
                "kalshi": {"bid": 0.007, "ask": 0.010, "updated": 1792038000.0},
                "poly":   {"bid": 0.004, "ask": 0.029, "updated": 1792038001.2},
            },
        },

        # ---- the SIG markets for this race ----
        "contracts": [
            {
                "market_id": "387",       # "Will the Democratic Party win...?"
                "pays_if": "D",
                # live SIG Yes prices (No price = 1 − Yes price)
                "yes": {"bid": 0.845, "ask": 0.850,
                        "bid_size": 15000, "ask_size": 12000,
                        "updated": 1792038000.5},
            },
            {
                "market_id": "388",       # "Will the Republican Party win...?"
                "pays_if": "R",
                "yes": {"bid": 0.135, "ask": 0.140,
                        "bid_size": 21226, "ask_size": 9000,
                        "updated": 1792038000.5},
            },
        ],
    },
    # "NH-SEN": { ... same shape ... },
}
