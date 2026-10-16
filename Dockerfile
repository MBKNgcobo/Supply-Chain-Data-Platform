FROM apache/airflow:3.3.2-python3.11
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
USER airflow
COPY --chown=airflow:root pyproject.toml /opt/airflow/build/
RUN pip install --no-cache-dir psycopg2-binary dbt-core dbt-postgres pydantic pytest
COPY --chown=airflow:root src /opt/project/src
ENV PYTHONPATH=/opt/project/src:/opt/project
ENV DBT_PROFILES_DIR=/opt/project/dbt


