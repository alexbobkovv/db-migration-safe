---
name: Bug report / wrong verdict
about: A crash, a false positive, or a hazard the tool missed
title: ""
labels: bug
---

<!-- The most valuable reports are "the verdict was wrong": a safe migration flagged as
unsafe (false positive), or an unsafe one that passed (a missed hazard). -->

## What happened

<!-- One or two sentences. -->

## Migration SQL

```sql
-- paste the statement(s); redacting table/column names is fine
```

## How you ran it

- Script + flags: <!-- e.g. analyze.py migration.sql --dialect postgres -->
- Dialect: <!-- postgres / mysql -->

## Versions

- db-migration-safe: <!-- python3 scripts/analyze.py --version -->
- squawk: <!-- squawk --version, or n/a -->
- eugene: <!-- eugene --version, or n/a -->
- Python: <!-- python3 --version -->

## Expected vs actual

- Expected:
- Actual (paste the full output):

```
```

## Does it touch a production database?

<!-- yes / no — helps us prioritize. -->
