FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/backend apps/backend
COPY apps/__init__.py apps/__init__.py
COPY packages packages
COPY prompts prompts
COPY scripts scripts
COPY alembic.ini .
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
EXPOSE 8000
# migrate -> seed (idempotent) -> serve
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed.py && uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000"]
