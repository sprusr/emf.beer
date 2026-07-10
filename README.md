# emf.beer

First build `pjsua2`:

```sh
wget https://github.com/pjsip/pjproject/archive/refs/tags/2.17.zip vendor/pjproject-2.17.zip
unzip vendor/pjproject-2.17.zip
cd vendor/pjproject-2.17
./configure CFLAGS="-fPIC"
make dep
make
cd pjsip-apps/src/swig/python
make
make wheel
```

Next install dependencies, including the build `pjsua2` wheel:

```sh
uv sync
```

Get your credentials from [phones.emfcamp.org](https://phones.emfcamp.org) and set them as environment variables:

```sh
export SIP_USERNAME=
export SIP_PASSWORD=
```

Then you can start the server:

```sh
uv run uvicorn main:app --loop asyncio --host 0.0.0.0 --port 8000 --workers 1
```

You will need to have [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed (or your preferred way of doing Python stuff, but then you probably know what you're doing). You must use `asyncio` rather than an alternative, as other UDP implementations are incompatible.
