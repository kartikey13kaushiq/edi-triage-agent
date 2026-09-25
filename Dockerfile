FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres,api,observability]"
COPY sql ./sql
USER nobody
EXPOSE 8000
CMD ["uvicorn", "edi_triage.api:app", "--host", "0.0.0.0", "--port", "8000"]
