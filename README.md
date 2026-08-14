# Retries Are Not Enough

A support-ticket pipeline that makes two LLM calls: one to analyse a ticket, one to draft a reply.

Two versions of it: `main.py`, which does the obvious thing and breaks in
instructive ways, and a Temporal version alongside it that does not. Everything
below up to "The Temporal version" is about `main.py`.

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
| `MODEL` | `gpt-4o-mini` | The **primary** model — the one the failure scenarios below apply to |
| `RECORDINGS` | `recordings/gpt-4o-mini` | Fallback recordings, for a model with no directory of its own |
| `PORT` | `8000` | Port to listen on |

The delay applies per response, so a run costs twice `DELAY_SECONDS`.
Recordings load at startup — restart after editing a `.txt`.

**One mock, several models.** Every directory under `recordings/` is loaded at
startup and **the directory name is the model name**, so a request for
`claude-opus-5` is answered from `recordings/claude-opus-5`. The banner lists what
it found. This is what lets the app fall back from one provider to another and get
real recorded replies from both.

Two things follow, and both matter for the scenarios below:

- **The status sequences apply to the primary model only.** A request for any
  other model is a fallback, and is served normally — a fallback that returned the
  same failure would be no escape at all.
- **`STICKY_STATUS` latches per model, not globally.** Exhausted quota belongs to
  an account, so latching `gpt-4o-mini` leaves `claude-opus-5` working.

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

`FALLBACK_PAUSE_SECONDS` (default `5`) is how long the app waits before failing
over to a second provider. Backing off before failover is ordinary caution — you
don't want to hammer a new provider the instant the first one hiccups — and it is
also the window scenario 8 asks you to interrupt. Also an **app** setting.

## 3. Failure scenarios

`.env` carries nine numbered scenarios; uncomment one and **restart the mock
server**. Forgetting the restart is the most common mistake — the banner always
prints the active config, so check it there.

## Workshop: scenario 8 — the decision that didn't survive

The app hits a wall it can't retry its way past, works out the right move, and
then dies before it can act on it.

**The fallback is not what fails here.** The app handles the quota wall correctly
and completely: it reads the error, works out that OpenAI is spent, switches to
Anthropic, and finishes the ticket. Twelve lines of `try`/`except`. You do not
need durable execution for that, and this scenario does not pretend you do.

What fails is *remembering*. Kill the process in the seconds between the decision
and the call that acts on it, and everything it just paid to learn goes with it —
so the next run starts by calling the provider it had already ruled out, and
redoing work it had already completed.

**1. Enable scenario 8.** In `.env`, uncomment scenario 8's lines and comment out
any other scenario:

```
ANALYSIS_STATUS=200
RESPONSE_STATUS=429
STICKY_STATUS=429
```

`DELAY_SECONDS=1` and `FALLBACK_PAUSE_SECONDS=5` are comfortable at a desk. On a
projector, give yourself 10.

**2. Restart the mock server.** Confirm the banner shows:

```
  primary: gpt-4o-mini  (the statuses below apply to it)
  analysis -> 200
  response -> 429
  sticky   -> once 429 is served, that model returns it for everything
```

`sticky` is what makes the wall a wall: once `gpt-4o-mini` has served a 429, it
serves nothing else, across restarts, until you restart the mock. The latch is per
model, so `claude-opus-5` keeps working — which is what makes the fallback a real
escape route rather than a second dead end.

### First, watch it work

Run it once and **let it finish**:

```bash
uv run main.py 1041
```

```
--- ANALYSIS (1041) via gpt-4o-mini ---
...

--- FAILED on response via gpt-4o-mini ---
RateLimitError / HTTP 429 / insufficient_quota: ...

--- DECISION ---
Tokens maxed out on gpt-4o-mini. Retrying will not help.
Fall back to Anthropic models.
Waiting 5s before failing over to claude-opus-5...

--- RESPONSE (1041) via claude-opus-5 ---
...

--- IN-PROCESS STATE (at exit) ---
provider=anthropic  analysis=✔ COMPLETED  response=✔ COMPLETED
```

The ticket is handled. Nothing is broken and nothing was lost. Note the mock log
though — the app made one response call and the mock served **three**, because a
429 is retryable by status code and the SDK has no idea this one isn't. Three
attempts against a wall before the app ever sees the error.

**Restart the mock server** to clear the latch before continuing.

### Now crash it

Same command, but this time interrupt it:

```bash
uv run main.py 1041
```

When you see `Waiting 5s before failing over...`, press **Ctrl+C**. You'll get a
`KeyboardInterrupt` traceback, which is what a crash looks like — an evicted pod
or an OOM kill gets you to the same place with less warning.

```
[1] analysis ticket=1041 model=gpt-4o-mini retry=0 seen=1 -> 200
[2] response ticket=1041 model=gpt-4o-mini retry=0 seen=1 -> 429 (latched)
[3] response ticket=1041 model=gpt-4o-mini retry=1 seen=2 -> 429 (latched)
[4] response ticket=1041 model=gpt-4o-mini retry=2 seen=3 -> 429 (latched)
                                                    <- Ctrl+C in the pause
```

At the moment you killed it, the app knew two things it had paid for: the analysis
was **done**, and OpenAI was **spent**.

**Run it again.** Same command, no changes:

```bash
uv run main.py 1041
```

```
--- IN-PROCESS STATE (at start) ---
provider=openai     analysis=✘ MISSING    response=✘ MISSING
```

That line is the whole scenario. `provider=openai` — it does not know it ruled
that out four seconds ago. `analysis=MISSING` — it does not know it already has
one. The dict is new because the process is new.

So it does the only thing it can: calls OpenAI again.

```
[5] analysis ticket=1041 model=gpt-4o-mini retry=0 seen=2 -> 429 (latched)
[6] analysis ticket=1041 model=gpt-4o-mini retry=1 seen=3 -> 429 (latched)
[7] analysis ticket=1041 model=gpt-4o-mini retry=2 seen=4 -> 429 (latched)
[8] analysis ticket=1041 model=claude-opus-5 retry=0 seen=1 -> 200
[9] response ticket=1041 model=claude-opus-5 retry=0 seen=1 -> 200
```

It gets there. The ticket is answered and the customer is served. Count what it
cost:

| | |
| --- | --- |
| Calls after the crash | **5** |
| Spent re-learning that OpenAI is exhausted | 3 |
| Spent redoing an analysis that was already complete and already paid for | 1 |
| Actual new work | **1** |

Four of those five calls existed only because the process forgot.

### What this does and doesn't prove

Two things went wrong, and both are about memory rather than logic:

- **It forgot the model swap.** The conclusion "OpenAI is spent, use Anthropic"
  was correct, expensive, and thrown away. So the new process paid three more
  calls to reach the same conclusion.
- **It forgot the completed analysis.** That work had already succeeded and
  already been billed. It was done a second time, on a second provider.

Be precise about the limit, because overclaiming here is easy: **nothing is
unrecoverable.** The app is not stuck, the ticket is not lost, and no customer
goes unanswered. It arrives at the right answer — just more expensively, having
thrown away work it had already paid for. On one ticket that's a rounding error.
On a queue of ten thousand during a bad deploy, it isn't.

And notice what would have to change to fix it in-process: nothing about the
retry logic, the fallback, or the error handling — all of that is already correct.
The only thing missing is somewhere for a decision to live that isn't the memory
of a process that might not survive the next five seconds.

That is the one thing the Temporal version of this scenario changes. Same crash,
same second — see "The Temporal version" below.

**Reset.** Restart the mock server to clear the latch. Until you do, every
`gpt-4o-mini` request returns 429 and other scenarios will look broken.

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

## The Temporal version

The same pipeline, run as a Temporal Workflow. `main.py` is untouched — the two
versions sit side by side so they can be read against each other.

Where `main.py` is one process that decides what to do and does it, this splits
into two: a **worker** that does the work, and a **starter** that asks for it.
Kill the starter after a run has begun and the ticket is still handled.

### What runs

Four processes, so four terminals.

| # | Terminal | Command |
| --- | --- | --- |
| 1 | Temporal dev server | `temporal server start-dev --ip 127.0.0.1 --db-filename temporal.db --log-level warn` |
| 2 | Mock LLM server | `uv run mock_llm.py` |
| 3 | Worker — leave running | `uv run worker.py` |
| 4 | Starter | `uv run main_temporal.py 1041` |

The Web UI is on http://localhost:8233. Wait for the worker to print
`worker ready: 127.0.0.1:7233, task queue 'tickets'` before starting a run —
though starting one first is worth trying deliberately, because the workflow
does not fail. It waits until a worker appears.

Requires the Temporal CLI: `brew install temporal`.

### What to look for

Run scenario 1 (`ANALYSIS_STATUS=200`, `RESPONSE_STATUS=200`) and the output is
the same analysis and reply as `uv run main.py 1041`, from the same recordings.
Two things differ.

**The state block is read over the network.** `main.py` prints a dict from its
own memory. `main_temporal.py` gets the same three fields with a Query, from a
process that does not own them — which also works from anywhere else, during a
run or long after it has finished:

```bash
temporal workflow query --workflow-id ticket-1041 --type get_state
```

**The `retry` column in the mock log is dead.** The Temporal version sets
`max_retries=0` on the OpenAI client, so the SDK never retries and never sends
its retry header. Retries belong to the Workflow's `RetryPolicy` now
(`workflow.py`), so `retry=` stays `0` and **`seen=` is the column that climbs.**

### Why the starter's output arrives all at once

Run the two side by side and the pacing differs. `main.py` prints the analysis,
pauses, then prints the reply. `main_temporal.py` prints the `at start` state
immediately, pauses, then prints everything else in one go — while the mock log
ticks along call by call in both cases.

This is not buffering. `main.py` prints the analysis the moment it has it
because it *is* the process making the call:

```python
state["analysis"] = understand(ticket)   # main.py
print(... ANALYSIS ...)                  # prints now
```

`main_temporal.py` is not making the calls. The worker is. The starter's only
line of sight is `await handle.result()`, which returns when the whole workflow
is done, so there is nothing it could print sooner.

**This is left as-is deliberately.** It could be made to match `main.py` by
polling the Query in the background and printing each field as it appears, but
that adds machinery to hide something true and worth saying out loud: the
process you are watching is not the process doing the work. That is exactly why
you can kill the starter mid-run and the ticket still gets handled.

The value is not lost in the meantime — it is just somewhere else. Start a run,
and about four seconds in, from a third terminal:

```bash
temporal workflow query --workflow-id ticket-1041 --type get_state
```

```
analysis  = "- **Core Problem:** The customer is unable to receive..."
response  = null
```

The first call's result is complete and readable while the second is still in
flight. `main.py` has no equivalent — its analysis exists only inside a process
that has not finished, and cannot be asked for it.

### Workshop: killing the worker mid-run

The counterpart to scenario 9, and the point of the whole exercise. With
`DELAY_SECONDS=2` and scenario 1 active:

**1. Start a run** in the starter terminal:

```bash
uv run main_temporal.py 1043
```

**2. After about 4 seconds**, Ctrl+C the **worker**. The analysis call has
completed by then and the response call is in flight.

**3. Restart the worker**: `uv run worker.py`

The run finishes on its own and the starter, which never noticed, prints the
reply. The mock log:

```
[9]  analysis ticket=1043 retry=0 seen=5 -> 200     completed before the kill
[10] response ticket=1043 retry=0 seen=5 -> 200     in flight when killed
[11] response ticket=1043 retry=0 seen=6 -> 200     retried after the restart
```

One analysis call, two response calls. Compare against scenario 9, where a
Ctrl+C sends the app back to the start: the analysis is re-called and re-billed,
and the retry counter resets to 0. Here the completed step is not repeated,
because its result is recorded rather than remembered. Only the step that was
genuinely interrupted runs again.

Open the workflow in the Web UI afterwards and the event history shows it: one
`ActivityTaskCompleted` for the analysis, two attempts at the response.

### Two things that will catch you out

- **Changing `.env` means restarting the mock *and* the worker.** `activities.py`
  reads `.env` at import exactly as `mock_llm.py` does, so a worker left running
  from an earlier scenario holds stale config. `restart-mock` only covers the mock.
- **`MAX_RETRIES` does nothing here.** The retry budget lives in `workflow.py` as
  a `RetryPolicy`, deliberately: workflow code is replayed, and reading the
  environment during a replay could give a different answer than the original
  run. Change the policy in the file.

## Layout

| Path | |
| --- | --- |
| `main.py` | The app — two LLM calls, the SDK's retry budget, and a provider fallback |
| `mock_llm.py` | Fake `/v1/chat/completions` that replays recordings |
| `tickets/` | Input tickets, one per file, named by ticket number |
| `recordings/` | Recorded real responses, per model |

The Temporal version, which leaves `main.py` alone:

| Path | |
| --- | --- |
| `workflow.py` | `TicketWorkflow` — read this against `main()` in `main.py` |
| `activities.py` | The LLM calls and the ticket read. Same prompts as `main.py` |
| `worker.py` | The process that runs workflows and activities |
| `main_temporal.py` | Starts a run and waits for it, mirroring `uv run main.py 1041` |
