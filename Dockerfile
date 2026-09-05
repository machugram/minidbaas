FROM python:3.12-slim

WORKDIR /srv

# Install deps first (better layer caching), then the package + migrations.
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY cli ./cli
COPY migrations ./migrations
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
