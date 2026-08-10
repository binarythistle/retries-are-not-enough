import os
import pathlib
import sys

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.environ.get("MODEL", "gpt-4o-mini")

client = OpenAI()


def ask(system, user):
    response = client.chat.completions.create(
        model=MODEL,
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
    body = getattr(error, "body", None)
    detail = str(error)
    if isinstance(body, dict):
        detail = body.get("message") or detail

    print(f"--- FAILED on {call} ---")
    print(f"{type(error).__name__}{f' (HTTP {status})' if status else ''}: {detail}")
    raise SystemExit(1)


def main():
    ticket_id = sys.argv[1]
    ticket = pathlib.Path("tickets", f"{ticket_id}.md").read_text()

    call = "analysis"
    try:
        analysis = understand(ticket)
        print(f"--- ANALYSIS ({ticket_id}) ---\n{analysis}\n")

        call = "response"
        print(f"--- RESPONSE ({ticket_id}) ---\n{respond(ticket, analysis)}")
    except openai.APIError as error:
        report(call, error)


main()
