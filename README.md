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

`MAX_RETRIES` (default `2`) sets the OpenAI client's retry budget — 1 initial
attempt plus that many retries. It is an **app** setting: the mock never reads it,
it only sees the requests the budget produces.

## 3. Failure scenarios

`.env` carries eight numbered scenarios; uncomment one and **restart the mock
server**. Forgetting the restart is the most common mistake — the banner always
prints the active config, so check it there.

## Workshop: scenario 8 — quota exhausted

Shows retries being spent on a failure they cannot fix, and completed work being
lost with no way to recreate it. No process kill needed: just run it twice.

**Optional warm-up.** Run scenario 2 (`RESPONSE_STATUS=500,500,200`) first to see
retries doing their job — two failures, third attempt succeeds. It makes the
contrast honest.

**1. Enable scenario 8.** In `.env`, uncomment scenario 8's three lines and
comment out any other scenario:

```
ANALYSIS_STATUS=200
RESPONSE_STATUS=429
STICKY_STATUS=429
```

`DELAY_SECONDS=1` keeps each run under 7 seconds.

**2. Restart the mock server.** Confirm the banner shows:

```
  analysis -> 200
  response -> 429
  sticky   -> once 429 is served, everything returns it
```

**3. First run.**

```bash
uv run main.py 1041
```

```
ticket 1041  |  http://localhost:8000/v1/

--- IN-PROCESS STATE (at start) ---
provider=openai  analysis=none

--- ANALYSIS (1041) via gpt-4o-mini ---
...

--- FAILED on response via gpt-4o-mini ---
RateLimitError / HTTP 429 / insufficient_quota: ...

--- DECISION ---
Tokens maxed out on gpt-4o-mini. Retrying will not help.
Fall back to Anthropic models.

--- IN-PROCESS STATE (at exit) ---
provider=anthropic  analysis=467 chars
```

The analysis was produced and billed. The app worked out that OpenAI is spent
and Anthropic is the way forward, and wrote both facts into `state` — the
in-memory dict that is the app's entire memory.

In the mock log, Call 2 made **three** attempts. The code made one call. `429`
means "retryable", but `insufficient_quota` means "terminal" — nothing about the
status code or the Python exception type distinguishes the two.

The app works out the right move, and then the process ends. The decision is
never acted on, and nothing records that it was ever made.

**4. Second run.** Same command again:

```bash
uv run main.py 1041
```

```
ticket 1041  |  http://localhost:8000/v1/

--- IN-PROCESS STATE (at start) ---
provider=openai  analysis=none

--- FAILED on analysis via gpt-4o-mini ---
RateLimitError / HTTP 429 / insufficient_quota: ...

--- DECISION ---
Tokens maxed out on gpt-4o-mini. Retrying will not help.
Fall back to Anthropic models.

--- IN-PROCESS STATE (at exit) ---
provider=anthropic  analysis=none
```

Compare the two state blocks across the runs. Run 1 ended with
`provider=anthropic, analysis=467 chars`. Run 2 begins with
`provider=openai, analysis=none` — the process is new, so the dict is new.

Three things went wrong here, and none of them are fixable in-process:

- **It went back to OpenAI.** Last run it concluded OpenAI was exhausted and
  Anthropic was the answer. That conclusion died with the process, so this run
  starts by calling the provider it already ruled out.
- **It spent three more requests re-learning it.** The decision wasn't cheap the
  first time and it wasn't cheap this time either.
- **The analysis is gone.** No analysis block this run — the work that succeeded
  and was billed in run 1 cannot be redone, because the quota that paid for it is
  spent.

**5. Reset.** Restart the mock server to clear the latch. Until you do, every
request returns 429 and other scenarios will look broken.

## Workshop: scenario 9 — durable retries

Shows the app losing its place in a retry sequence. This one needs a Ctrl+C, and
unlike scenario 8 the outcome never changes: a 504 is retried but never recovers,
so both runs fail. The only difference is whether the retry budget was honoured.

**1. Enable scenario 9.** In `.env`, uncomment scenario 9 and comment out any
other scenario — including scenario 8's `STICKY_STATUS`:

```
ANALYSIS_STATUS=200
RESPONSE_STATUS=504
MAX_RETRIES=10
```

`DELAY_SECONDS=0` is fine here; the client's own backoff paces the retries.

**2. Restart the mock server.** The banner ends with the log key:

```
  log key: retry = the app's own counter, back to 0 whenever it restarts
           seen  = calls of that kind this server has served, ever
```

**3. First run — then interrupt it.**

```bash
uv run main.py 1041
```

Watch the mock log. Within about ten seconds it reaches `retry=4`; press Ctrl+C
in the **app's** terminal there.

```
[2] response ticket=1041 retry=0 seen=1 -> 504
[3] response ticket=1041 retry=1 seen=2 -> 504
[4] response ticket=1041 retry=2 seen=3 -> 504
[5] response ticket=1041 retry=3 seen=4 -> 504
[6] response ticket=1041 retry=4 seen=5 -> 504     <- Ctrl+C here
```

Ctrl+C in the foreground works because it signals the whole process group.
Killing the `uv run` wrapper from another terminal does **not** work — it leaves
the Python child running, and it will quietly finish its retries.

**4. Second run.** Same command again:

```
[7] analysis ticket=1041 retry=0 seen=2 -> 200
[8] response ticket=1041 retry=0 seen=6 -> 504     <- back to zero
```

`retry` restarted at 0; `seen` carried on from 5 to 6. The upstream has now taken
six runs at this call while the app believes it is on its first. Leave run 2
alone and it will spend all 10 retries and fail — 11 response attempts,
`retry=0..10`, taking about 45 seconds as the backoff widens to its 8-second cap.
That is the budget working correctly, because nothing interrupted it.

**Why the count matters.** A retry budget is a contract: try this many times,
then stop and escalate. If the count resets on every restart, a crash-looping
worker never reaches "exhausted" — so it never gives up, never escalates, and
never tells anyone. It just retries forever, a handful of attempts at a time,
while `seen` climbs without limit.

## Layout

| Path | |
| --- | --- |
| `main.py` | The app — two LLM calls, no retries |
| `mock_llm.py` | Fake `/v1/chat/completions` that replays recordings |
| `tickets/` | Input tickets, one per file, named by ticket number |
| `recordings/` | Recorded real responses, per model |
