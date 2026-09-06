FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The address-conflict preflight uses the platform ICMP utility. python:slim
# intentionally omits it, so install the minimal runtime package explicitly.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN useradd --create-home --shell /usr/sbin/nologin infraops \
    && chown -R infraops:infraops /app
USER infraops

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
