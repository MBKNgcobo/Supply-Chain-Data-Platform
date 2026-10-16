# Deliverable 2 — A clean repository another developer can set up

| Field | Value |
| --- | --- |
| Status | **NOT MET — repository cannot yet be set up from a fresh clone without knowledge that lives outside it.** This document records what exists, what is missing, and the exact path to done. |
| Date | 2026-10-14 |
| Evidence | File inventory and contents verified directly in the workspace on 2026-10-14 |

## 1. What "clean" means here (definition of done)

A developer who clones the repository and has nothing but Docker + git can: create the database, install pinned dependencies, run the pipeline, run `dbt build`, and know which environment variables to set — **using only files in the repository**, with no personal paths, no hand-me-down credentials, and no undocumented steps.

## 2. Inventory — what exists

| Area | State |
| --- | --- |
| `README` | Business problem, stack, proposed layout — orientation only; no setup commands |
| `src/supply_chain_pipeline/` | extract → validate → load → manifest, CLI entry point (`cli.py --file <path>`) |
| `sql/bootstrap/001–003` | Schemas, audit tables, bronze DDL (executable in order) |
| `dbt/` | `dbt_project.yml` (layering + `+schema` placement), `profiles.yml.example` (env-var driven), 4 models, 21 tests, macro override |
| `docs/` | SPEC-001, ADR-001/002/003/004, contracts, data dictionary, `gold-warehouse.md` |
| `GLOSSARY.md` | Canonical terms |
| `compose.yaml`, `Dockerfile`, `pyproject.toml`, `.gitignore`, `.env.example` | **exist but are 0 bytes** |
| `src/supply_chain_pipeline/config.py` | **0 bytes** |
| `data/`, `dags/`, `dashboards/`, `tests/`, `.github/` | **directories exist, contain no files** |
| Git | On `main` with **no commits yet** |

## 3. Machine-independence: what is already portable vs what is not

**Portable (good):**

- `dbt/profiles.yml.example` reads every connection value from `DBT_*` env vars with defaults; its header forbids committing a filled-in copy.
- No absolute personal paths appear anywhere in SQL or dbt models.
- `macros/generate_schema_name.sql` pins models to `bronze`/`silver`/`gold` so schema placement does not depend on a target name.
- Bootstrap SQL is idempotent (`IF NOT EXISTS` throughout).

**Not portable (defects):**

1. **No dependency declaration.** `pyproject.toml` is 0 bytes: `psycopg2`, `pydantic` (and their versions) are imported by the pipeline but nowhere declared. A fresh clone cannot `pip install` this project.
2. **Two disjoint env-var families.** `cli.py` reads `DB_NAME` (default `pipeline_db`), `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`; dbt reads `DBT_HOST/DBT_USER/DBT_PASSWORD/DBT_DATABASE` (default `supply_chain`). Set one family and dbt still cannot see the database the loader wrote to — the defaults even name **different databases** (`pipeline_db` vs `supply_chain`).
3. **`.env.example` is empty**, so the env-var contract above is discoverable only by reading source.
4. **`.gitignore` is empty** — a commit made today would include `dbt/target/` (build artifacts already present), and would offer no protection for `.env`, `dbt/profiles.yml`, or `__pycache__/`.
5. **No container definitions.** `Dockerfile` and `compose.yaml` are 0 bytes, so "Postgres + pipeline" is not reproducible; the README promises Docker Compose.
6. **No CI.** `.github/` is empty; SPEC-001 §10's `ci.yml` does not exist.
7. **No tests.** `tests/` (pytest) is empty; `pyproject.toml` cannot declare them either.
8. **No source file** in `data/` and no "where to get it" pointer from any setup instructions (`docs/source-data-contract.md` does name the Kaggle URL, but nothing links a developer to it).
9. **`supply chain data platform.session.sql`** — a VS Code session file with a space-laden name; harmless, but evidence the repo has never been tidied for a second developer.

## 4. Setup path as the repo stands (reproducing the gap)

What a fresh-clone developer can actually do today:

1. `git clone …` → **no commits to clone** (repository not initialised on the remote).
2. Create a Postgres database — *which name?* Undocumented; the two defaults disagree (§3.2).
3. `psql -f sql/bootstrap/001_create_schemas.sql` → `002` → `003` — this works.
4. Install Python dependencies — *from what?* No `pyproject.toml` content; must guess `psycopg2-binary`, `pydantic`.
5. Set `DBT_*` vars from `profiles.yml.example` — works, if you know the variable names (they exist only in that file).
6. Drop `supply_chain_data.csv` into `data/incoming/` — *from where?* Kaggle, per `docs/source-data-contract.md`.
7. `python -m supply_chain_pipeline.cli --file data/incoming/supply_chain_data.csv` — imports are bare module names (`from extract import …`), so it must run with `src/supply_chain_pipeline` on `sys.path`, i.e. from inside that directory. **Not documented anywhere.**
8. `dbt build` from `dbt/` — works (parse/compile verified, ADR-003), provided `DBT_*` points at the same database as step 7.

Every *undocumented* and *guess* above is a violation of this deliverable.

## 5. Smallest path to done (ordered — matches SPEC-001 Q10's agreed sequence)

1. **`pyproject.toml`** — declare and pin `psycopg2-binary`, `pydantic`; define the package so the bare imports resolve (or convert to relative imports); register `pytest`.
2. **`.env.example`** — one env-var contract. Reconcile the two families: make `cli.py` read the same variables dbt does, or document `DB_*` + `DBT_*` side by side with **identical defaults** (pick one database name).
3. **`.gitignore`** — `dbt/target/`, `.env`, `dbt/profiles.yml`, `__pycache__/`, `.venv/`. Untrack `dbt/target/` before the first commit.
4. **`README` → Setup section** — prerequisites; bootstrap SQL order; env vars; where to get the CSV; exact pipeline and dbt commands; expected exit codes (0/1/2/3/99 from `cli.py`).
5. **`compose.yaml` + `Dockerfile`** — Postgres service + app image so the stack is reproducible.
6. **`tests/` + `.github/workflows/ci.yml`** — pytest for extract/validate/load; `dbt parse` (and later `dbt build`) gate on PRs.
7. First git commit — after that, the repository *is* what a new developer gets.

Until steps 1–4 land, this deliverable is honestly **not met** — recorded here rather than claimed.

