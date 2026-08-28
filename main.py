import os
import pathlib
import sys
import time

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODELS = {
    "openai": os.environ.get("MODEL", "gpt-4o-mini"),
    "anthropic": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
}

# The only state this app has, and it lives in this process's memory. Every run
# starts from exactly these values, irrespective of how the last run ended.
state = {"provider": "openai", "analysis": None, "response": None}

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
 
# Gives the workshop participant time to crash the process
FALLBACK_PAUSE_SECONDS = float(os.environ.get("FALLBACK_PAUSE_SECONDS", "10"))

client = OpenAI(max_retries=MAX_RETRIES)

COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

GREEN = "\033[32m" if COLOUR else ""
RED = "\033[31m" if COLOUR else ""
DIM = "\033[2m" if COLOUR else ""
RESET = "\033[0m" if COLOUR else ""


def paint(colour, text):
    """Green = the model spoke. Red = it failed. Dim = the app talking about itself."""
    return f"{colour}{text}{RESET}"


# The DECISION banner.
# This is used to prompt the workshop participant to crash the process
BOLD_AMBER = "\033[1;33m" if COLOUR else ""
SPINE = f"{BOLD_AMBER}▌{RESET}"
RULE = f"{BOLD_AMBER}▌{'─' * 62}{RESET}"


def model():
    """Which model to call — decided by whatever is in state right now."""
    return MODELS[state["provider"]]


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
    """Print the failure legibly, and hand back the error `code` if it carried one.

    Reporting only. What to do about it is main()'s decision, because for one
    particular code there is something worth doing.
    """
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

    return code


def fall_back():
    """Switch provider, and say so.

    A terminal condition cannot be retried away, so the only move left is a
    different provider. Nothing about this is hard — which is the point. The app
    works the right answer out on its own, in one process, with no help.

    What it cannot do is keep the answer. `state` is this process's memory, so the
    conclusion below survives exactly as long as the process does. The pause makes
    that window long enough to see.
    """
    state["provider"] = "anthropic"

    print(
        f"\n{RULE}"
        f"\n{SPINE} ⚠️  DECISION: tokens maxed out on {MODELS['openai']}."
        " Retrying will not help."
        f"\n{SPINE} 🔀 Falling back to {MODELS['anthropic']} in "
        f"{FALLBACK_PAUSE_SECONDS:g}s."
        f"\n{SPINE} 💾 provider=anthropic is in this process's memory, nowhere else."
        f"\n{SPINE} 💥 Kill this process now and the decision dies with it."
        f"\n{RULE}\n",
        flush=True,
    )

    time.sleep(FALLBACK_PAUSE_SECONDS)


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

    while True:
        try:
            # We aim to only complete each step 1 time - no duplication even if we 
            # fallback to another provider. However, as state is only held in
            # memory, if the process crashes this state is not maintaind, and
            # we may duplicate work that has already been completed. 
            # In other words this code is not durable
            if state["analysis"] is None:
                state["analysis"] = understand(ticket)
                print(paint(GREEN, f"--- ANALYSIS ({ticket_id}) via {model()} ---"))
                print(f"{state['analysis']}\n")

            if state["response"] is None:
                state["response"] = respond(ticket, state["analysis"])
                print(paint(GREEN, f"--- RESPONSE ({ticket_id}) via {model()} ---"))
                print(f"{state['response']}\n")

            break
        except openai.APIError as error:
            if os.environ.get("TRACEBACK"):
                raise

            # Which call failed is whichever one state is still missing.
            call = "analysis" if state["analysis"] is None else "response"
            code = report(call, error)

            # One fallback, and only from the provider we started on.
            if code == "insufficient_quota" and state["provider"] == "openai":
                fall_back()
                continue

            show_state("at exit")
            raise SystemExit(1)

    show_state("at exit")


main()
