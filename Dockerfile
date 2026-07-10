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

ARG TARGETARCH
RUN if [ $TARGETARCH = "arm64" ]; then \
        uv add vendor/pjproject-2.17/pjsip-apps/src/swig/python/dist/pjsua2-2.17-cp314-cp314-linux_aarch64.whl \
    ; fi
RUN if [ $TARGETARCH = "amd64" ]; then \
        uv add vendor/pjproject-2.17/pjsip-apps/src/swig/python/dist/pjsua2-2.17-cp314-cp314-linux_x86_64.whl \
    ; fi
RUN uv sync --frozen

FROM python:3.14.6-slim-bookworm

WORKDIR /app

COPY --from=builder /app .
COPY emf-beer/ ./emf-beer

CMD ["/app/.venv/bin/fastapi", "run"]
