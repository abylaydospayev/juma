FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY ops/container-entrypoint.sh /usr/local/bin/juma-entrypoint
RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && useradd --create-home --uid 10001 juma \
    && chmod 0555 /usr/local/bin/juma-entrypoint
USER 10001
EXPOSE 8000
ENTRYPOINT ["juma-entrypoint"]
CMD ["uvicorn", "juma.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
