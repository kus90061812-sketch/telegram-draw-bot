# SPORT NOW v16.1 — MLB ONLY / ODDS / KOREAN

Enabled:
- MLB only
- MLB Stats API schedule / probable starters / lineups
- MLB-related news analysis
- ESPN MLB pregame odds fallback (moneyline / spread / total when available)
- American-moneyline implied probability supplied to AI as a secondary signal
- AI reasons/comment forced to natural Korean
- All 30 MLB team names translated to Korean in Telegram output
- Player names requested as Korean transliterations in AI output
- English news headlines are summarized in Korean, not copied into the post
- T-30 new-pick cutoff
- PostgreSQL pick journal + HIT/MISS/PUSH + automatic post-game review
- Previous MLB review lessons fed into later MLB analysis

Odds are a supporting signal only. The model is instructed to prioritize actual
lineup, starter, injury/news and team context over the market.
