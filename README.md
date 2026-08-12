# SPORT NOW v13.3.3 — PostgreSQL compatibility fix

Fix:
- psycopg2 connection now wrapped with `PgCompatConnection`
- existing `conn.execute(...).fetchone()/fetchall()` calls can run
- SQLite `?` placeholders are converted to PostgreSQL `%s`
- common SQLite schema syntax is converted for PostgreSQL
- `psycopg2-binary` remains in requirements.txt

Existing behavior retained:
- unlimited picks
- FINAL COMBO removed
- Sportradar baseball integrations
- OpenAI / Telegram logic

Static scan:
- INSERT OR REPLACE occurrences before patch: 0
- remaining SQLite-specific patterns: none detected

Railway:
- Keep DATABASE_URL
- No need to restore MAX_PICKS_* / COMBO_* variables
