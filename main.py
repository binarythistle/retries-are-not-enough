import os
import pathlib
import sys

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODELS = {
    "openai": os.environ.get("MODEL", "gpt-4o-mini"),
    "anthropic": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
}

# The only state this app has, and it lives in this process's memory. Every run
# starts from exactly these values, however the last run ended.
state = {"provider": "openai", "analysis": None, "response": None}

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))

client = OpenAI(max_retries=MAX_RETRIES)

# Colour is display only. Off when stdout is not a terminal, so `> run.log`
# stays free of escape codes, and off when NO_COLOR is set. When it is off the
# codes are empty strings, so every paint() below is a no-op.
COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

GREEN = "\033[32m" if COLOUR else ""
RED = "\033[31m" if COLOUR else ""
DIM = "\033[2m" if COLOUR else ""
RESET = "\033[0m" if COLOUR else ""


def paint(colour, text):
    """Green = the model spoke. Red = it failed. Dim = the app talking about itself."""
    return f"{colour}{text}{RESET}"


def model():
    """Which model to call — decided by whatever is in state right now."""
    return MODELS[state["provider"]]


# Column widths, so the "at start" and "at exit" lines align under each other
# and the audience can compare them vertically.
PROVIDER_WIDTH = max(len(name) for name in MODELS)
STATUS_WIDTH = len("* COMPLETED")


def status(value, width=0):
    """A step's work either survived into this state dict or it did not."""
    text, colour = ("✔ COMPLETED", GREEN) if value else ("✘ MISSING", RED)
    return paint(colour, f"{text:<{width}}" if width else text)


def show_state(when):
    print(paint(DIM, f"--- IN-PROCESS STATE ({when}) ---"))
    print(
        paint(DIM, f"provider={state['provider']:<{PROVIDER_WIDTH}}  analysis=")
        + status(state["analysis"], STATUS_WIDTH)
        + paint(DIM, "  response=")
        + status(state["response"])
        + "\n"
    )


def ask(system, user):
    response = client.chat.completions.create(
        model=model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content


def understand(ticket):
    return ask(
        "You are a support triage analyst. In three bullets, state the "
        "customer's core problem, the likely cause, and the urgency.",
        ticket,
    )


def respond(ticket, analysis):
    return ask(
        "You are a support agent. Write a short, warm reply to the customer. "
        "One line of greeting at most, then get to the point.",
        f"Ticket:\n{ticket}\n\nAnalysis:\n{analysis}",
    )


def report(call, error):
    """Print the failure legibly. No retry, no recovery — the run still dies."""
    if os.environ.get("TRACEBACK"):
        raise error

    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None) or {}
    detail = body.get("message") if isinstance(body, dict) else None
    code = body.get("code") if isinstance(body, dict) else None

    label = [type(error).__name__]
    if status:
        label.append(f"HTTP {status}")
    if code:
        label.append(code)

    print(paint(RED, f"--- FAILED on {call} via {model()} ---"))
    print(paint(RED, f"{' / '.join(label)}: {detail or error}") + "\n")

    # Terminal conditions can't be retried away, so the only move left is a
    # different provider. Record the decision in state — where it will sit until
    # this process exits, a few lines from now.
    if code == "insufficient_quota":
        state["provider"] = "anthropic"
        print(paint(DIM, "--- DECISION ---"))
        print(
            paint(
                DIM,
                f"Tokens maxed out on {MODELS['openai']}. Retrying will not help.",
            )
        )
        print(paint(DIM, "Fall back to Anthropic models.") + "\n")

    show_state("at exit")
    raise SystemExit(1)


def main():
    ticket_id = sys.argv[1]
    ticket = pathlib.Path("tickets", f"{ticket_id}.md").read_text()

    print(
        paint(
            DIM,
            f"ticket {ticket_id}  |  {client.base_url}  |  "
            f"max_retries={MAX_RETRIES}",
        )
        + "\n"
    )
    show_state("at start")

    call = "analysis"
    try:
        state["analysis"] = understand(ticket)
        print(paint(GREEN, f"--- ANALYSIS ({ticket_id}) via {model()} ---"))
        print(f"{state['analysis']}\n")

        call = "response"
        state["response"] = respond(ticket, state["analysis"])
        print(paint(GREEN, f"--- RESPONSE ({ticket_id}) via {model()} ---"))
        print(f"{state['response']}\n")
    except openai.APIError as error:
        report(call, error)

    show_state("at exit")


main()
