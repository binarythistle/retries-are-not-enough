"""Start a run. The Temporal counterpart of main.py.

Deliberately the same command shape:

    uv run main.py 1041            <- does the work itself, in this process
    uv run main_temporal.py 1041   <- asks for the work to be done, and waits

The difference matters more than it looks. This process holds no state. Kill it
after the run has started and the ticket is still handled, by the worker, and
the result is still there when you come back for it.
"""

import asyncio
import os
import sys

from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ActivityError, RetryState

from activities import MODELS
from workflow import RETRY, TicketWorkflow

TASK_QUEUE = "tickets"
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")

COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

GREEN = "\033[32m" if COLOUR else ""
RED = "\033[31m" if COLOUR else ""
DIM = "\033[2m" if COLOUR else ""
RESET = "\033[0m" if COLOUR else ""

PROVIDER_WIDTH = max(len(name) for name in MODELS)
STATUS_WIDTH = len("* COMPLETED")


def paint(colour, text):
    return f"{colour}{text}{RESET}"


def status(value, width=0):
    text, colour = ("✔ COMPLETED", GREEN) if value else ("✘ MISSING", RED)
    return paint(colour, f"{text:<{width}}" if width else text)


def show_state(when, state):
    """The same shape as show_state() in main.py, from a different source.

    main.py reads a dict in its own memory. This reads the Workflow's state over
    the network, with a Query, from a process that does not own it.
    """
    print(paint(DIM, f"--- WORKFLOW STATE ({when}) ---"))
    print(
        paint(DIM, f"provider={state['provider']:<{PROVIDER_WIDTH}}  analysis=")
        + status(state["analysis"], STATUS_WIDTH)
        + paint(DIM, "  response=")
        + status(state["response"])
        + "\n"
    )

# Allows us to change the labeling of activites in the Temporl UI
STEPS = {
    "load_ticket": "the ticket read",
    "analysis": "analysis",
    "respond": "response",
}


def report(error, state, workflow_id):
    """The counterpart of report() in main.py, against a different error shape.

    main.py catches an openai.APIError directly — it made the call itself. This
    process did not, so a failed Workflow arrives as a chain:

        WorkflowFailureError   the Workflow failed
          ActivityError        which Activity, and why it stopped retrying
            ApplicationError   the exception the Activity actually raised

    Without this, the attendee gets that chain as a raw traceback where main.py
    prints four tidy lines.
    """
    if os.environ.get("TRACEBACK"):
        raise error

    activity = error.cause if isinstance(error.cause, ActivityError) else None
    application = getattr(activity, "cause", None) or error.cause

    step = STEPS.get(getattr(activity, "activity_type", None), "the pipeline")
    # ApplicationError.type is the name of the exception the Activity raised —
    # "InternalServerError" for a 504 — which survived the trip through the
    # server as a string. The status code and any error code are in the message.
    kind = getattr(application, "type", None) or type(application).__name__
    # .message, not str(): str() of an ApplicationError prepends its own type,
    # which is already the first half of the line printed below.
    detail = getattr(application, "message", None) or application

    print(paint(RED, f"--- FAILED on {step} via {MODELS[state['provider']]} ---"))
    print(paint(RED, f"{kind}: {detail}") + "\n")

    # main.py has no equivalent of this block, and cannot have one: its retry
    # budget lived inside a client object that no longer exists, so nothing is
    # left to ask how far through the budget it got.
    print(paint(DIM, "--- WHY IT STOPPED ---"))
    retry_state = getattr(activity, "retry_state", None)
    if retry_state == RetryState.MAXIMUM_ATTEMPTS_REACHED:
        print(
            paint(
                DIM,
                f"{RETRY.maximum_attempts} attempts, counted by the server rather "
                "than by this process,",
            )
        )
        print(paint(DIM, "then the policy in workflow.py was exhausted.") + "\n")
    else:
        print(paint(DIM, f"retry state: {retry_state}") + "\n")

    show_state("at exit", state)

    # main.py's version of this line is a dead end: it prints the state and the
    # process exits, taking it with it. Here the run failed and the state did not.
    if state["analysis"]:
        print(paint(DIM, "The run failed. The analysis it had already paid for did not:"))
    else:
        print(paint(DIM, "The run failed. Its state is still readable:"))
    print(
        paint(
            DIM,
            f"  temporal workflow query --workflow-id {workflow_id} --type get_state",
        )
        + "\n"
    )

    raise SystemExit(1)


async def main():
    ticket_id = sys.argv[1]
    client = await Client.connect(ADDRESS)

    # The Workflow ID is derived from the ticket, not random. That is what makes
    # a re-run of the same ticket recognisable as the same piece of work rather
    # than a second one.
    workflow_id = f"ticket-{ticket_id}"

    print(
        paint(DIM, f"ticket {ticket_id}  |  workflow {workflow_id}  |  queue {TASK_QUEUE}")
        + "\n"
    )

    handle = await client.start_workflow(
        TicketWorkflow.run,
        ticket_id,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    show_state("at start", await handle.query(TicketWorkflow.get_state))

    # Everything below prints at once, rather than analysis-then-pause-then-reply
    # the way main.py does. That is deliberate, and it is not buffering.
    #
    # main.py prints the analysis the moment it has it because it IS the process
    # making the call. This process is not; the worker is. Its only line of sight
    # is the line below, which returns when the whole Workflow is done.
    #
    # It could be made to match by polling get_state in a background task, but
    # that would be machinery to hide something true and worth saying out loud:
    # the process you are watching is not the process doing the work. Which is
    # why you can kill this one mid-run and still get the ticket handled.
    #
    # The first call's value is not trapped in the meantime, just elsewhere. Four
    # seconds into a run, from another terminal:
    #   temporal workflow query --workflow-id ticket-1041 --type get_state
    # returns the finished analysis with response still null.
    try:
        response = await handle.result()
    except WorkflowFailureError as error:
        # The Query still answers after a failed run, which is the whole point of
        # printing state here rather than giving up with a traceback.
        report(error, await handle.query(TicketWorkflow.get_state), workflow_id)

    state = await handle.query(TicketWorkflow.get_state)

    # Each block is labelled with the provider that actually produced it, not with
    # whichever one the Workflow is on now. After a fallback those differ, and
    # using state["provider"] for both claimed a gpt-4o-mini analysis had been
    # written by claude-opus-5.
    #
    # main.py cannot have this bug: it prints each block the moment it has it, so
    # the current provider is always the right label. This process prints both at
    # the end, which is why the Workflow has to record who did what.
    analysis_model = MODELS[state["analysis_provider"]]
    response_model = MODELS[state["response_provider"]]

    print(paint(GREEN, f"--- ANALYSIS ({ticket_id}) via {analysis_model} ---"))
    print(f"{state['analysis']}\n")
    print(paint(GREEN, f"--- RESPONSE ({ticket_id}) via {response_model} ---"))
    print(f"{response}\n")

    if analysis_model != response_model:
        print(
            paint(
                DIM,
                f"Two providers, one ticket: analysed on {analysis_model}, "
                f"replied on {response_model}.",
            )
            + "\n"
        )

    show_state("at exit", state)

    # Unlike main.py, this state did not die with the process that printed it.
    print(paint(DIM, "Still readable after this process exits:"))
    print(
        paint(
            DIM,
            f"  temporal workflow query --workflow-id {workflow_id} --type get_state",
        )
        + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
