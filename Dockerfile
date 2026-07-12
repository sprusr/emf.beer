FROM python:3.14.6-bookworm AS builder

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

ENV HF_HOME=/app/hf-cache
RUN .venv/bin/python -c "from pocket_tts import TTSModel; m = TTSModel.load_model(); m.get_state_for_audio_prompt('alba')"

FROM python:3.14.6-slim-bookworm

ENV HF_HOME=/app/hf-cache HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/wheels ./wheels
COPY --from=builder /app/hf-cache ./hf-cache
COPY --from=builder /app/pyproject.toml /app/uv.lock ./
COPY test.wav .
COPY emf-beer/ ./emf-beer

CMD ["/app/.venv/bin/uvicorn", "emf-beer.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
