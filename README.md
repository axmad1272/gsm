# GSM — учёт топлива

Единая система учёта отгрузок топлива со складов Рохат, Алмалык и Навои с интеграцией Didox и сверкой с 1С.

- Спецификация модуля «Расход»: [docs/modules/rashod-spec.md](docs/modules/rashod-spec.md)
- Архитектура, решения и статус: [docs/architecture.md](docs/architecture.md)

## Запуск

```bash
docker compose up --build
docker compose exec api python -m app.seed   # склады и пользователи; выводит X-User-Id
```

API: http://localhost:8000/docs. В dev-режиме пользователь передаётся заголовком `X-User-Id`.

## Разработка

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
export GSM_DATABASE_URL=postgresql+psycopg://gsm:gsm@localhost:5432/gsm_test
pytest -q
ruff check . && ruff format --check .
alembic revision --autogenerate -m "..."   # после изменения моделей
```

Тестам нужен PostgreSQL: блокировки, частичные индексы и `SKIP LOCKED` проверяются на настоящей БД.
