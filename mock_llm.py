"""A fake OpenAI chat-completions endpoint that replays recorded responses.

    uv run mock_llm.py
    uv run mock_llm.py > mock.log      # or capture it

The app reaches it via OPENAI_BASE_URL in .env, so `uv run main.py 1041`
needs no extra flags.
"""

import json
import os
import pathlib
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv

load_dotenv()

RECORDINGS = os.environ.get("RECORDINGS", "recordings/gpt-4o-mini")
MODEL = os.environ.get("MODEL", "gpt-4o-mini")
PORT = int(os.environ.get("PORT", "8000"))
DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "2"))

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


RECORDED = load_recordings(RECORDINGS)
TITLES = load_titles()
count = 0


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

        count += 1
        print(
            f"[{count}] {kind:<8} ticket={ticket_id} (waiting {DELAY_SECONDS}s)",
            flush=True,
        )
        time.sleep(DELAY_SECONDS)

        if (ticket_id, kind) not in RECORDED:
            return self.send_json(
                400,
                {"error": {"message": f"no recording for ticket={ticket_id} {kind}"}},
            )

        self.send_json(
            200,
            {
                "id": f"chatcmpl-mock-{ticket_id}-{kind}",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": RECORDED[(ticket_id, kind)],
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


print(f"Serving {len(RECORDED)} recordings from {RECORDINGS} on port {PORT}")
print(f"  tickets: {', '.join(sorted({t for t, _ in RECORDED}))}")
print(f"  delay:   {DELAY_SECONDS}s per response")
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
