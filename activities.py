"""The parts of the pipeline that touch the outside world.

Everything in here is an Activity. That is not a Temporal formality: an Activity
is where side effects are allowed to live, because Temporal records the *result*
of one and replays that recording rather than running the code again. Workflow
code has to be replayable, so it cannot read files or call an API.

Note what did NOT change from main.py. The prompts are identical, the client is
the same OpenAI client, and ask() is the same three lines. The workshop's
argument is that the calls were never the problem.
"""

import os
import pathlib
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import AsyncOpenAI
from temporalio import activity

load_dotenv()

# Same two providers as main.py. Which one is used is decided by the Workflow
# and passed in, because that decision is state — and losing it is what
# challenge 03 was about.
MODELS = {
    "openai": os.environ.get("MODEL", "gpt-4o-mini"),
    "anthropic": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
}

# max_retries=0 is the hinge of this whole workshop.
#
# main.py hands its retry budget to the OpenAI SDK, which retries inside one
# process, from memory, with no record of having done it. Here the retry policy
# belongs to the Workflow instead (see workflow.py), so the SDK must not also
# retry underneath it. Two retry layers stacked would make the attempt counts in
# the mock log impossible to explain.
#
# Visible consequence: the SDK never sends its retry header now, so the retry=
# column in mock.log stays 0 for every Temporal challenge. Watch seen= instead.
client = AsyncOpenAI(max_retries=0)


# Temporal passes a single argument to an Activity, so inputs that need more
# than one value are dataclasses. This is the only shape change from main.py.
@dataclass
class AnalysisInput:
    ticket: str
    provider: str = "openai"


@dataclass
class ResponseInput:
    ticket: str
    analysis: str
    provider: str = "openai"


async def ask(provider, system, user):
    response = await client.chat.completions.create(
        model=MODELS[provider],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content


@activity.defn
async def load_ticket(ticket_id: str) -> str:
    """Read the ticket off disk.

    An Activity purely because it is I/O. Nothing calls an LLM here and nothing
    appears in mock.log for it, which is the point worth making: Activities are
    not "the expensive calls", they are "the things that cannot be replayed".
    """
    return pathlib.Path("tickets", f"{ticket_id}.md").read_text()


@activity.defn
async def understand(args: AnalysisInput) -> str:
    # "triage analyst" is load-bearing. mock_llm.py decides whether an incoming
    # request is the analysis call or the response call by looking for that
    # exact phrase in the system prompt. Reword this and the mock serves the
    # wrong recording, silently.
    return await ask(
        args.provider,
        "You are a support triage analyst. In three bullets, state the "
        "customer's core problem, the likely cause, and the urgency.",
        args.ticket,
    )


@activity.defn
async def respond(args: ResponseInput) -> str:
    # The full ticket text goes in the user message, exactly as main.py sends
    # it. The mock identifies which ticket this is by finding the ticket's title
    # line in that text, so trimming it to just the analysis would break the
    # lookup.
    return await ask(
        args.provider,
        "You are a support agent. Write a short, warm reply to the customer. "
        "One line of greeting at most, then get to the point.",
        f"Ticket:\n{args.ticket}\n\nAnalysis:\n{args.analysis}",
    )
