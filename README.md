# SPORT NOW v13.3.1
DB startup fix.

- db() PostgreSQL helper restored
- DATABASE_URL startup path restored
- unlimited picks retained
- FINAL COMBO remains removed from the v13.3 posting flow
- Python compile check passed

Do not restore deleted MAX_PICKS_* or COMBO_* Railway variables.
Keep DATABASE_URL.
