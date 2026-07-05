# emf.beer

Get your credentials from [phones.emfcamp.org](https://phones.emfcamp.org) and set them as environment variables:

```sh
export SIP_USERNAME=
export SIP_PASSWORD=
```

Then you can start the server:

```sh
uv run uvicorn main:app --loop asyncio --host 0.0.0.0 --port 8000
```
