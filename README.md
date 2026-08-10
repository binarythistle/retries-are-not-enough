# Retries Are Not Enough

A support-ticket pipeline that makes two LLM calls: one to analyse a ticket, one to draft a reply.

## Setup

```bash
uv sync
cp .env.example .env
```

Put an OpenAI key in `.env`. If you only ever use the mock server, any non-empty
value works — the SDK requires the field but the mock ignores it.

## 1. Run the mock server

```bash
uv run mock_llm.py
```

Replays recorded responses on `http://localhost:8000`. No network, no real API key.

Configure in `.env` (or as an env var, which wins):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DELAY_SECONDS` | `2` | Wait before each response, so replays feel like real calls |
| `RECORDINGS` | `recordings/gpt-4o-mini` | Which recordings to serve |
| `PORT` | `8000` | Port to listen on |

The delay applies per response, so a run costs twice `DELAY_SECONDS`.
Recordings load at startup — restart after editing a `.txt`.

## 2. Run the app

```bash
uv run main.py 1041
```

Tickets are `1041`, `1042`, `1043`.

Which endpoint it hits comes from `OPENAI_BASE_URL` in `.env`, set to the mock
server by default. To use the live OpenAI API, swap the commented line in `.env`
— or override it for a single run:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1 uv run main.py 1041
```

`main.py` is identical either way; it never knows which one it's talking to.

## Layout

| Path | |
| --- | --- |
| `main.py` | The app — two LLM calls, no retries |
| `mock_llm.py` | Fake `/v1/chat/completions` that replays recordings |
| `tickets/` | Input tickets, one per file, named by ticket number |
| `recordings/` | Recorded real responses, per model |
