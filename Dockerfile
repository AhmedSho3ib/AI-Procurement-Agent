# syntax=docker/dockerfile:1

# ---- Stage 1: build dependencies into an isolated virtualenv ----------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies are installed from the pinned requirements file first so the layer
# is cached across source changes. `pyproject.toml` remains the source of truth
# (a test keeps requirements.txt in sync with it).
COPY pyproject.toml requirements.txt README.md ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# Then the application itself, without touching already-installed dependencies.
COPY src ./src
RUN pip install --no-deps .

# ---- Stage 2: slim runtime image -------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8000

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Package sources and templates are needed at runtime (prompts + Jinja templates).
COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser templates ./templates
COPY --chown=appuser:appuser pyproject.toml README.md ./

# Runtime artifacts (sqlite database, generated reports) live in a writable volume.
RUN mkdir -p /app/data /app/ai-agent-output && chown -R appuser:appuser /app/data /app/ai-agent-output

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
