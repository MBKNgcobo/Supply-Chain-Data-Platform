Supply Chain Data Platform
Proposed repository: supply-chain-data-platform

Business problem
Supply chain data often arrives in separate files, contains inconsistent values, and requires manual SQL analysis. This platform automates the process of turning that source data into reliable operational information.


Primary users
Procurement teams, inventory planners, operations managers, and business analysts.


Primary deliverable
An automated batch data pipeline backed by a PostgreSQL warehouse, with tested transformations, run history, failure handling, and a Power BI report.

Engineering focus
Reliability and automation

Analytical focus
Operational performance

Primary language
Python

Warehouse
PostgreSQL


How the technologies fit together

Python	
Ingest files, validate source structure, track batches, handle ingestion errors

PostgreSQL	
Store raw data, transformed data, warehouse tables, and audit records

dbt	
Build SQL transformations, manage model dependencies, run data tests

Apache Airflow	
Schedule pipeline tasks, control execution order, manage retries

Docker Compose	
Run the application and its supporting services consistently

pytest	
Test Python functions and ingestion logic

GitHub Actions	
Run automated tests and code-quality checks on changes

Power BI	
Consume warehouse tables and present operational insights

Environment requirements
Use:
- Python in a project-specific virtual environment or container.
- PostgreSQL matching the major version used for development.
- Git and GitHub for source control.
- VS Code for development.
- Docker Desktop for reproducible services.

supply-chain-data-platform/
│
├── src/
│   └── supply_chain_pipeline/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── extract.py
│       ├── validate.py
│       ├── load.py
│       ├── manifest.py
│       └── logging_config.py
│
├── sql/
│   └── bootstrap/
│       ├── 001_create_schemas.sql
│       └── 002_create_audit_tables.sql
│
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml.example
│   ├── models/
│   │   ├── staging/
│   │   ├── intermediate/
│   │   └── marts/
│   ├── tests/
│   └── seeds/
│
├── dags/
│   └── supply_chain_pipeline.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── data/
│   ├── incoming/
│   └── sample/
│
├── docs/
│   ├── architecture.md
│   ├── source-data-contract.md
│   ├── data-dictionary.md
│   ├── data-quality.md
│   └── decisions/
│
├── dashboards/
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── .env.example
├── .gitignore
├── Dockerfile
├── compose.yaml
├── pyproject.toml
└── README.md

## Reproducible dev environment (Docker Compose)

Prerequisites: Docker Desktop (Compose v2), no local Postgres on 5432
(or set HOST_POSTGRES_PORT in `.env`).

Services (see `compose.yaml`):
- `postgres` (postgres:16) -- warehouse + Airflow metadata DBs. Waits via
  `pg_isready` healthcheck; data persists in the `pgdata` volume.
- `airflow` -- Airflow 3 standalone, starts only once postgres is healthy
  (`depends_on: service_healthy`), metadata in `airflow_db`.
- `pipeline` (profile `pipeline` only) -- one-shot ETL: CLI ingest, then
  `dbt build` + `dbt test`. Its exit code propagates, so a failed run exits
  non-zero.

Fresh-install schema init: `./sql/bootstrap/*.sql` runs automatically into an
empty `pgdata` volume (`000_*` creates `airflow_db`; `001-003` create the
bronze/etl schemas and tables). App containers reach the DB as `postgres`
(the Compose service name); credentials come from `.env`, never hardcoded.

Start (target command):

```bash
docker compose up --build
```

Run one batch end-to-end (fails loudly -- non-zero exit on ETL/dbt failure):

```bash
docker compose --profile pipeline run --rm pipeline
echo $?   # must be non-zero when ingest or dbt fails
```

Test:

```bash
# unit suite (host venv)
python -m pytest
# full ETL + dbt inside containers (needs the stack up)
docker compose --profile pipeline run --rm pipeline
```

Reset (clean checkout state -- drops the pgdata volume so bootstrap re-runs):

```bash
docker compose down -v
docker compose up --build
```

Stop:

```bash
docker compose down        # keep data (pgdata preserved)
docker compose down -v     # drop data (fresh install next start)
```

Configure from a clean checkout (optional overrides):

```bash
cp .env.example .env   # edit secrets/ports; defaults work without it
```

## Continuous integration (GitHub Actions)

`.github/workflows/ci.yml` runs on every push to `main` and every pull
request. It is **validation-only**: it proves a change is safe to merge. It
does not run a production pipeline on a schedule -- local scheduling stays
with Airflow (`dags/`) and one-shot runs with
`docker compose --profile pipeline run --rm pipeline`.

Pipeline (Python 3.11, Postgres 16 service, pinned `requirements-dev.txt`):

1. `ruff format --check` + `ruff check` -- formatting and lint.
2. `pytest` -- unit suite, no database.
3. `scripts/check_schema_compatibility.py` -- loader columns vs bronze DDL.
4. `scripts/check_contract_parity.py` -- docs describe implemented behaviour.
5. Bootstrap `sql/bootstrap/001-003` into the Postgres service.
6. `pytest -m integration` -- real pipeline against the test database.
7. CLI ingest of `tests/fixtures/happy_path.csv` + `dbt build` + `dbt test`.

### Quality gates

A change is not complete while any gate fails:

| Gate | Command | Blocks on |
| --- | --- | --- |
| Formatting / lint | `ruff format --check`, `ruff check` | Unformatted or unlinted code |
| Unit tests | `pytest` | Any unit failure |
| Schema compatibility | `python scripts/check_schema_compatibility.py` | Loader columns diverging from `bronze.supply_chain_raw` DDL |
| Contract parity | `python scripts/check_contract_parity.py` | Docs describing behaviour the code does not implement |
| Integration tests | `pytest -m integration` | Pipeline failure against Postgres |
| dbt models + tests | `dbt build`, `dbt test` | Failing model or required data test |

Each gate is a separate CI step, so GitHub names the failing gate instead of
burying it in a log. Reproduce any gate locally with the command above.

```bash
python -m pip install -r requirements-dev.txt
ruff format --check src tests scripts && ruff check src tests scripts
python -m pytest                                            # unit
python scripts/check_schema_compatibility.py
python scripts/check_contract_parity.py
```
