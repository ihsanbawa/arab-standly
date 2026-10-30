FROM python:3.13-slim AS tested
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY mizan ./mizan
COPY tests ./tests
RUN python -m pytest -q

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=tested /build/mizan ./mizan
CMD ["python", "-m", "mizan.bot"]
