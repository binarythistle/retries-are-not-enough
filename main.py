import os
import pathlib
import sys

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


def main():
    ticket_id = sys.argv[1]
    ticket = pathlib.Path("tickets", f"{ticket_id}.md").read_text()

    analysis = understand(ticket)
    print(f"--- ANALYSIS ({ticket_id}) ---\n{analysis}\n")

    print(f"--- RESPONSE ({ticket_id}) ---\n{respond(ticket, analysis)}")


main()
