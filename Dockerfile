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

RUN cp vendor/pjproject-2.17/pjsip-apps/src/swig/python/dist/$(ls -AU vendor/pjproject-2.17/pjsip-apps/src/swig/python/dist | head -1) wheel.whl
RUN uv add ./wheel.whl
RUN uv sync --frozen

FROM python:3.14.6-slim-bookworm

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/wheel.whl ./wheel.whl
COPY emf-beer/ ./emf-beer
COPY test.wav .

CMD ["/app/.venv/bin/fastapi", "run"]
