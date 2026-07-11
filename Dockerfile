FROM python:3.14.6-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update
RUN apt-get install -y swig
RUN pip install uv setuptools

ADD https://github.com/pjsip/pjproject/archive/refs/tags/2.17.zip vendor/pjproject-2.17.zip
RUN cd vendor && unzip pjproject-2.17.zip
RUN cd vendor/pjproject-2.17 && ./configure CFLAGS="-fPIC" && make dep && make
RUN cd vendor/pjproject-2.17/pjsip-apps/src/swig/python && make && make wheel

COPY pyproject.toml uv.lock ./

RUN cp -r vendor/pjproject-2.17/pjsip-apps/src/swig/python/dist ./wheels
RUN uv add ./wheels/$(ls -AU wheels | head -1)
RUN uv sync --frozen

FROM python:3.14.6-slim-bookworm

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/wheels ./wheels
COPY pyproject.toml uv.lock ./
COPY emf-beer/ ./emf-beer
COPY test.wav .

CMD ["/app/.venv/bin/fastapi", "run", "--workers", "1"]
