"""A fake OpenAI chat-completions endpoint that replays recorded responses.

    uv run mock_llm.py
    uv run mock_llm.py > mock.log            # or capture it

Force errors on either of the app's two calls. A comma-separated sequence is
consumed one entry per attempt, then cycles, so every run behaves the same:

    RESPONSE_STATUS=500 uv run mock_llm.py          # call 2 always fails
    RESPONSE_STATUS=500,500,200 uv run mock_llm.py  # fails twice, then succeeds
    ANALYSIS_STATUS=429,200 uv run mock_llm.py      # call 1 recovers on retry 1

The app reaches it via OPENAI_BASE_URL in .env, so `uv run main.py 1041`
needs no extra flags.
"""

import json
import os
import pathlib
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv

load_dotenv()

# Line-buffer stdout so `> mock.log` shows the banner immediately, not once
# the first request happens to flush the buffer.
sys.stdout.reconfigure(line_buffering=True)

# Colour is written into mock.log deliberately. The Mock Server tab reads the
# file with `tail -f`, so the escape codes in it are what the attendee's
# terminal renders. The usual isatty() check would disable colour in exactly the
# case that needs it, because stdout here is a redirect to the log, never a tty.
# NO_COLOR=1 or MOCK_COLOR=0 turns it off.
COLOR = os.environ.get("MOCK_COLOR", "1") != "0" and not os.environ.get("NO_COLOR")


def sgr(code):
    return f"\033[{code}m" if COLOR else ""


RESET, BOLD, DIM = sgr(0), sgr(1), sgr(2)
HEADER = sgr("38;5;110")


def tint(status):
    """Line colour keyed to the HTTP status.

    Applied to the whole line, never around the status number itself. The
    challenge check scripts grep mock.log for patterns like
    "analysis .* -> 200", and an escape sequence inserted mid-pattern would stop
    them matching.
    """
    if status == 200:
        return sgr("38;5;114")  # green
    if 400 <= status < 500:
        return sgr("38;5;179")  # amber
    return sgr("38;5;174")  # red


RECORDINGS = os.environ.get("RECORDINGS", "recordings/gpt-4o-mini")

# The model the app starts on, and more than a label: the status sequences below
# describe THIS model. A request naming a different model means the app fell back
# to another provider, which is served on its own terms. See status_for().
PRIMARY_MODEL = os.environ.get("MODEL", "gpt-4o-mini")
PORT = int(os.environ.get("PORT", "8000"))
DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "2"))

# HTTP status per call, as a comma-separated sequence consumed one entry per
# attempt and then cycled: "500,500,200" fails twice and succeeds on the third.
# 200 replays the recording.
def statuses(name):
    return [int(s) for s in os.environ.get(name, "200").split(",")]


STATUS = {
    "analysis": statuses("ANALYSIS_STATUS"),
    "response": statuses("RESPONSE_STATUS"),
}

# Keyed by (kind, model) rather than kind alone, so a fallback provider's calls
# get their own count and cannot advance the primary model's position in its
# sequence. With one model in play — every challenge before 08 — the numbers come
# out identical to the fixed two-key version this replaces.
attempts = {}

# Once this status is served, every later request to that model returns it too,
# whatever the sequences say. Models an account-level failure like exhausted
# quota, which no amount of retrying or restarting can clear.
STICKY_STATUS = os.environ.get("STICKY_STATUS")
STICKY_STATUS = int(STICKY_STATUS) if STICKY_STATUS else None

# Which models have hit the sticky status. A set rather than a single flag,
# because exhausted quota belongs to an account and not to this server: latching
# gpt-4o-mini must not latch whatever the app falls back to, or the fallback
# could never succeed. With one model in play the behaviour is unchanged.
latched = set()


def status_for(kind, model, seen):
    """The status this request gets, before the sticky latch is considered.

    The sequences describe the primary model. Another model here means the app
    fell back to a second provider, and a fallback returning the same failure
    would leave it nowhere to go — so it is served normally. Nothing has needed a
    failing fallback yet; this is where that knob would go.
    """
    if model != PRIMARY_MODEL:
        return 200
    sequence = STATUS[kind]
    return sequence[seen % len(sequence)]


def error_code(status, is_latched):
    """The `code` OpenAI would send, so the app can tell transient from terminal."""
    if status == 429:
        return "insufficient_quota" if is_latched else "rate_limit_exceeded"
    if status == 400:
        return "invalid_request_error"
    return None

BLOCK = re.compile(r"^--- (ANALYSIS|RESPONSE) \((\d+)\) ---$", re.MULTILINE)


def load_recordings(directory):
    """Map (ticket_id, "analysis"|"response") -> the recorded text."""
    out = {}
    for path in sorted(pathlib.Path(directory).glob("*.txt")):
        text = path.read_text()
        marks = list(BLOCK.finditer(text))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            key = (mark.group(2), mark.group(1).lower())
            out[key] = text[mark.end() : end].strip()
    return out


def load_titles():
    """Map ticket_id -> its title line, used to recognise incoming tickets."""
    return {
        path.stem: path.read_text().splitlines()[0].lstrip("#").strip()
        for path in pathlib.Path("tickets").glob("*.md")
    }


def load_all(root="recordings"):
    """Map (model, ticket_id, kind) -> recorded text, across every model.

    The directory name IS the model name, so recordings/claude-opus-5 answers
    requests whose model field is claude-opus-5. That is the entire mechanism
    behind serving a provider fallback: one mock, two sets of recordings.
    """
    out = {}
    for directory in sorted(pathlib.Path(root).iterdir()):
        if directory.is_dir():
            for key, text in load_recordings(directory).items():
                out[(directory.name, *key)] = text
    return out


RECORDED = load_all()

# A request for a model with no recordings of its own falls back to RECORDINGS.
# That keeps the optional MODEL knob in .env behaving as it did when this file
# only knew about one model: point MODEL at anything and it still gets served.
FALLBACK_MODEL = pathlib.Path(RECORDINGS).name
RECORDED.update(
    {(FALLBACK_MODEL, *key): text for key, text in load_recordings(RECORDINGS).items()}
)

LOADED_MODELS = sorted({model for model, _, _ in RECORDED})
TITLES = load_titles()
count = 0


def recording(model, ticket_id, kind):
    """The recorded text for this call, or None if nothing matches."""
    for key in ((model, ticket_id, kind), (FALLBACK_MODEL, ticket_id, kind)):
        if key in RECORDED:
            return RECORDED[key]
    return None


def choose(system, user):
    """Work out which recording this request is asking for."""
    kind = "analysis" if "triage analyst" in system else "response"
    for ticket_id, title in TITLES.items():
        if title in user:
            return ticket_id, kind
    return None, kind


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global count

        if self.path != "/v1/chat/completions":
            return self.send_json(404, {"error": {"message": f"no route {self.path}"}})

        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = {m["role"]: m["content"] for m in body["messages"]}
        ticket_id, kind = choose(messages.get("system", ""), messages.get("user", ""))

        # Which model the app asked for. Before challenge 08 this was always the
        # primary one and the field was ignored; now it decides which recordings
        # answer, whether the sequences apply, and which latch is checked.
        model = body.get("model") or PRIMARY_MODEL

        attempt = attempts.get((kind, model), 0)
        attempts[(kind, model)] = attempt + 1

        is_latched = model in latched
        if is_latched:
            status = STICKY_STATUS
        else:
            status = status_for(kind, model, attempt)
            if status == STICKY_STATUS:
                latched.add(model)
                is_latched = True

        # The SDK reports how many times *it* has retried this call. That count
        # lives in the client process, so it restarts from 0 when the app does.
        retries = self.headers.get("x-stainless-retry-count", "?")

        count += 1
        # The leading bar gives the tiled pane a consistent spine, so the mock's
        # output is distinguishable from the app's at a glance.
        #
        # model= is new for challenge 08 and sits between two fields the check
        # scripts read. It is safe there because every one of them matches either
        # the "[n] kind " prefix or the " -> status" suffix, never a field index.
        print(
            f"{tint(status)}▎[{count}] {kind:<8} ticket={ticket_id} model={model} "
            f"retry={retries} seen={attempt + 1} "
            f"-> {status}{' (latched)' if is_latched else ''} "
            f"(waiting {DELAY_SECONDS}s){RESET}"
        )
        time.sleep(DELAY_SECONDS)

        if status != 200:
            error = {"message": f"mock server returned {status} for {kind}"}
            code = error_code(status, is_latched)
            if code:
                error["type"] = error["code"] = code
            return self.send_json(status, {"error": error})

        text = recording(model, ticket_id, kind)
        if text is None:
            return self.send_json(
                400,
                {
                    "error": {
                        "message": f"no recording for model={model} "
                        f"ticket={ticket_id} {kind}"
                    }
                },
            )

        self.send_json(
            200,
            {
                "id": f"chatcmpl-mock-{ticket_id}-{kind}",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": text,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def send_json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # our own one-line log above is enough


# Bind before announcing. The banner used to print first, which meant
# "Serving" appeared in mock.log even when the bind failed with "Address
# already in use", and the setup scripts polling for that word handed the
# attendee a sandbox with no mock server running. Bind first and a failure is
# a traceback with no banner, which is what the readiness poll should see.
server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

print(f"{HEADER}{BOLD}▎ MOCK LLM SERVER{RESET}")
# "Serving" is load-bearing. The per-challenge setup scripts poll
# `grep -q "Serving" mock.log` to know the server is up before handing the
# sandbox to the attendee. Renaming this line hangs every challenge start.
print(f"{HEADER}▎{RESET} Serving {len(RECORDED)} recordings on port {PORT}")
print(f"{HEADER}▎{RESET} models:  {', '.join(LOADED_MODELS)}")
print(f"{HEADER}▎{RESET} primary: {PRIMARY_MODEL}  (the statuses below apply to it)")
print(f"{HEADER}▎{RESET} tickets: {', '.join(sorted({t for _, t, _ in RECORDED}))}")
print(f"{HEADER}▎{RESET} delay:   {DELAY_SECONDS}s per response")
for kind, sequence in STATUS.items():
    print(f"{HEADER}▎{RESET} {kind:<8} -> {','.join(str(s) for s in sequence)}")
if STICKY_STATUS:
    print(
        f"{HEADER}▎{RESET} sticky   -> once {STICKY_STATUS} is served, "
        f"that model returns it for everything"
    )
print(f"{HEADER}▎{RESET}{DIM} log key: retry = the app's own counter, back to 0 on restart{RESET}")
print(f"{HEADER}▎{RESET}{DIM}          seen  = calls of that kind served for that model, ever{RESET}")
server.serve_forever()
