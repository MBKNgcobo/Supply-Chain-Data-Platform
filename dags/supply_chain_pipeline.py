"""DAG supply_chain_pipeline: discover -> ingest -> transform -> validate -> report.
Manual trigger now (schedule=None); set SCHEDULE="@daily" once reliable.
Retries: transient only. DQ failures (exit 1/2, dbt test fail) never retry.
"""
from __future__ import annotations
import hashlib, logging, os, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
log = logging.getLogger(__name__)
SCHEDULE = None
DAG_ID = "supply_chain_pipeline"
REPO_ROOT = Path(os.getenv("SUPPLY_CHAIN_REPO_ROOT", Path(__file__).resolve().parents[1]))
INCOMING_DIR = Path(os.getenv("SUPPLY_CHAIN_INCOMING_DIR", REPO_ROOT / "data" / "incoming"))
FALLBACK_DATA_DIR = REPO_ROOT / "data"
DBT_PROJECT_DIR = Path(os.getenv("DBT_PROJECT_DIR", REPO_ROOT / "dbt"))
DBT_PROFILES_DIR = Path(os.getenv("DBT_PROFILES_DIR", os.path.expanduser("~/.dbt")))
PIPELINE_NAME = os.getenv("SUPPLY_CHAIN_PIPELINE_NAME", "supply_chain_ingestion_pipeline")
MAX_REJECT_RATE = os.getenv("SUPPLY_CHAIN_MAX_REJECT_RATE", "5.0")
try:
    from airflow.sdk import DAG, task
    from airflow.exceptions import AirflowFailException, AirflowSkipException, AirflowException
    try:
        from airflow.providers.standard.operators.bash import BashOperator
    except ImportError:
        from airflow.operators.bash import BashOperator  # type: ignore
except ImportError:
    from airflow import DAG  # type: ignore
    from airflow.decorators import task  # type: ignore
    from airflow.exceptions import AirflowFailException, AirflowSkipException, AirflowException  # type: ignore
    try:
        from airflow.providers.standard.operators.bash import BashOperator  # type: ignore
    except ImportError:
        from airflow.operators.bash import BashOperator  # type: ignore
DEFAULT_ARGS = {"owner": "data-engineering", "depends_on_past": False,
    "email_on_failure": False, "email_on_retry": False, "retries": 2,
    "retry_delay": timedelta(minutes=5), "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30)}
def _failure_log(context: dict) -> None:
    ti = context.get("task_instance"); exc = context.get("exception")
    log.error("DAG=%s task=%s run=%s try=%s failed: %s",
        getattr(ti, "dag_id", "?"), getattr(ti, "task_id", "?"),
        getattr(ti, "run_id", "?"), getattr(ti, "try_number", "?"), exc)
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
def _manifest_hashes() -> set:
    try:
        import psycopg2  # type: ignore
    except ImportError:
        log.warning("discover: no psycopg2, manifest check skipped"); return set()
    conn = psycopg2.connect(dbname=os.getenv("DB_NAME", "supply_chain"),
        user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD", "postgres"),
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_hash FROM etl.batch_manifest WHERE status='SUCCESS'")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
with DAG(dag_id=DAG_ID, default_args={**DEFAULT_ARGS, "on_failure_callback": _failure_log},
    description="discover -> ingest (CLI) -> dbt build -> dbt test -> report",
    schedule=SCHEDULE, start_date=datetime(2026, 10, 15), catchup=False,
    max_active_runs=1, max_active_tasks=4,
    tags=["supply-chain", "bronze", "dbt", "batch"], doc_md=__doc__,
    is_paused_upon_creation=False) as dag:
    @task(task_id="discover", retries=2, execution_timeout=timedelta(minutes=5))
    def discover() -> list:
        cands = sorted(INCOMING_DIR.glob("*.csv")); scanned = str(INCOMING_DIR)
        if not cands and FALLBACK_DATA_DIR.exists():
            cands = sorted(FALLBACK_DATA_DIR.glob("*.csv")); scanned = str(FALLBACK_DATA_DIR)
        log.info("discover: %d csv in %s", len(cands), scanned)
        if not cands:
            log.info("discover: empty -> downstream will skip"); return []
        done = _manifest_hashes()
        fresh = []
        for p in cands:
            d = _sha256(p)
            if d in done:
                log.info("discover SKIP %s (%.12s..)", p.name, d)
            else:
                log.info("discover NEW %s (%.12s..)", p.name, d); fresh.append(str(p))
        log.info("discover: %d/%d need ingestion", len(fresh), len(cands))
        return fresh
    @task(task_id="ingest", retries=2, execution_timeout=timedelta(minutes=30))
    def ingest(files: list) -> dict:
        if not files:
            log.info("ingest: no new files -> skip, not fail")
            raise AirflowSkipException("no unprocessed files")
        det, trans, details, ok = [], [], [], 0
        for fp in files:
            cmd = [sys.executable, "-m", "supply_chain_pipeline.cli", "--file", fp,
                   "--pipeline-name", PIPELINE_NAME, "--max-reject-rate-percent", MAX_REJECT_RATE]
            log.info("ingest: run %s", " ".join(cmd))
            try:
                p = subprocess.run(cmd, cwd=str(REPO_ROOT / "src"),
                    env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
                    capture_output=True, text=True, timeout=1500)
            except subprocess.TimeoutExpired as e:
                log.error("ingest TIMEOUT %s: %s", fp, e); trans.append(f"{fp}: timeout"); continue
            log.info("ingest exit=%d %s", p.returncode, fp)
            if p.stdout: log.info("stdout[%s]:\n%s", Path(fp).name, p.stdout[-4000:])
            if p.stderr: log.warning("stderr[%s]:\n%s", Path(fp).name, p.stderr[-4000:])
            details.append({"file": fp, "exit_code": p.returncode})
            if p.returncode == 0: ok += 1
            elif p.returncode in (1, 2): det.append(f"{fp}: exit {p.returncode}")
            else: trans.append(f"{fp}: exit {p.returncode}")
        summary = {"processed": len(files), "succeeded_or_skipped": ok,
                   "deterministic_failures": det, "transient_failures": trans, "details": details}
        log.info("ingest summary: %s", summary)
        if det: raise AirflowFailException(f"DQ failure, no retry: {det}")
        if trans:
            raise AirflowException(f"transient failure, will retry: {trans}")
        return summary
    transform = BashOperator(task_id="transform",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt build --profiles-dir {DBT_PROFILES_DIR} --project-dir {DBT_PROJECT_DIR}",
        retries=2, retry_delay=timedelta(minutes=5), retry_exponential_backoff=True,
        execution_timeout=timedelta(minutes=30), on_failure_callback=_failure_log)
    validate = BashOperator(task_id="validate",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt test --profiles-dir {DBT_PROFILES_DIR} --project-dir {DBT_PROJECT_DIR}",
        retries=0, execution_timeout=timedelta(minutes=20), on_failure_callback=_failure_log)
    @task(task_id="report", retries=0, execution_timeout=timedelta(minutes=5), trigger_rule="all_done")
    def report(**ctx) -> dict:
        ti = ctx["ti"]; run = ctx.get("dag_run")
        ing = ti.xcom_pull(task_ids="ingest") or {}
        states = {}
        try:
            dr = ti.get_dagrun()
            for t in ("discover", "ingest", "transform", "validate"):
                i = dr.get_task_instance(t); states[t] = i.state if i else "unknown"
        except Exception as e:
            log.warning("report states unavailable: %s", e)
        manifest: dict = {}
        try:
            import psycopg2  # type: ignore
            c = psycopg2.connect(dbname=os.getenv("DB_NAME", "supply_chain"),
                user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD", "postgres"),
                host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
                connect_timeout=10)
            with c.cursor() as cur:
                cur.execute("SELECT batch_id,source_file,status,rows_read,rows_loaded,rows_rejected,error_message FROM etl.batch_manifest WHERE pipeline_name=%s ORDER BY started_at DESC LIMIT 5", (PIPELINE_NAME,))
                manifest = {"recent_batches": [{"batch_id": r[0], "file": r[1], "status": r[2], "rows_read": r[3], "rows_loaded": r[4], "rows_rejected": r[5], "error": r[6]} for r in cur.fetchall()]}
            c.close()
        except Exception as e:
            manifest = {"lookup_error": str(e)}
        overall = "SUCCESS" if states and all(s == "success" for s in states.values()) else "FAILED"
        summary = {"dag": DAG_ID, "run_id": getattr(run, "run_id", "?"), "overall": overall,
                   "task_states": states, "ingest": ing, "manifest": manifest}
        log.info("REPORT %s", summary)
        return summary
    found = discover()
    try:
        loaded = ingest.expand(files=[found])  # single-element map keeps one instance
    except Exception:
        loaded = ingest(found)
    loaded >> transform >> validate >> report()

