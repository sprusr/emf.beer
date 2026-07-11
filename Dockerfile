FROM python:3.14.6-bookworm AS builder

# UV_COMPILE_BYTECODE makes `uv` write .pyc files during install. Without it the
# copied .venv is pure source, so every cold-start recompiles the whole torch/
# scipy/librosa stack to bytecode on first import (~a minute on 2 vCPUs).
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    HF_HOME=/app/hf-cache

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


RUN .venv/bin/python -c "from pocket_tts import TTSModel; m = TTSModel.load_model(); m.get_state_for_audio_prompt('alba')"

FROM python:3.14.6-slim-bookworm

# HF_HUB_OFFLINE stops huggingface_hub from making a network call to check each
# file's etag on every load. The weights are baked into /app/hf-cache at build
# time, so those HEAD requests are pure latency (and stall for ~a minute on
# Fly's egress). Offline mode reads straight from the cache.
ENV HF_HOME=/app/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/wheels ./wheels
COPY --from=builder /app/hf-cache ./hf-cache
COPY --from=builder /app/pyproject.toml /app/uv.lock ./
COPY test.wav .
COPY emf-beer/ ./emf-beer

# Fly's Firecracker VMs don't add the container hostname to /etc/hosts. pjsip
# resolves the local hostname during init, so without an entry it blocks on a
# DNS timeout (seconds) on every startup. Map it to loopback before booting.
CMD ["sh", "-c", "grep -q \"$(cat /etc/hostname)\" /etc/hosts || echo \"127.0.0.1 $(cat /etc/hostname)\" >> /etc/hosts; exec /app/.venv/bin/uvicorn emf-beer.main:app --host 0.0.0.0 --port 8000 --workers 1"]
