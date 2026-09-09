# One image, five roles: api, opc-sim, opc-agent, erp-sync, mock-erp.
# The compose file picks the role via the container command.
FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY config ./config
RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000
CMD ["fsmes", "run-api", "--host", "0.0.0.0"]
