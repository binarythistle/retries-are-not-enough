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

import openai
from dotenv import load_dotenv
from openai import AsyncOpenAI
from temporalio import activity
from temporalio.exceptions import ApplicationError

load_dotenv()

# The error type the Workflow watches for. A string, because that is all that
# survives the trip to the server and back — see the comment on ask().
QUOTA_EXHAUSTED = "QuotaExhausted"

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
    """The same three lines as main.py's ask(), plus one judgement call.

    The judgement is the interesting part of this workshop, so it is worth being
    precise about what it is and is not.

    A 429 can mean two opposite things. Rate limited: wait and try again, it will
    work. Quota exhausted: it will never work, stop. **Nothing in the shape of the
    failure separates them.** Both are HTTP 429 and both arrive as
    openai.RateLimitError, so neither the SDK's retry logic (which reads the
    status code and two headers, never the body) nor a RetryPolicy's
    non_retryable_error_types (which matches on the exception type) can tell them
    apart. The difference is in the response body, and reading it is an act of
    interpretation that belongs to the application.

    So something has to classify, and it is this. Temporal does not remove that
    obligation — what it does is give the resulting verdict somewhere to live that
    outlives this process. Compare main.py, which reaches the same verdict in
    report() and then dies holding it.
    """
    try:
        response = await client.chat.completions.create(
            model=MODELS[provider],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except openai.APIStatusError as error:
        if (getattr(error, "body", None) or {}).get("code") == "insufficient_quota":
            # non_retryable stops the RetryPolicy dead. Retrying this is exactly
            # the waste challenges 03 and 07 are about, so the budget is not spent
            # on it — the failure goes straight back to the Workflow to act on.
            #
            # provider rides along in details because "quota exhausted" is only
            # meaningful about a particular account.
            raise ApplicationError(
                str(error),
                provider,
                type=QUOTA_EXHAUSTED,
                non_retryable=True,
            ) from error
        # Everything else is unchanged, and deliberately so: a 500 or a 504 is
        # still a shape the RetryPolicy can handle, and challenges 06 and 08 rely
        # on it doing exactly that.
        raise

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
