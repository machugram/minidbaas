# Alembic migrations for the Mini-DBaaS metadata database.

Apply with:

```bash
alembic upgrade head
```

Postgres boots call this from `app.db.init_db()`. SQLite tests use `create_all`
and stamp `head` so the version table stays consistent.
