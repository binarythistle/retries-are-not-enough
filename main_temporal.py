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

from temporalio.client import Client

from activities import MODELS
from workflow import TicketWorkflow

TASK_QUEUE = "tickets"
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")

# Same display-only colour rules as main.py, so the two runs look alike on
# screen and the difference the attendee notices is the content, not the theme.
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
    response = await handle.result()
    state = await handle.query(TicketWorkflow.get_state)

    provider = state["provider"]
    print(paint(GREEN, f"--- ANALYSIS ({ticket_id}) via {MODELS[provider]} ---"))
    print(f"{state['analysis']}\n")
    print(paint(GREEN, f"--- RESPONSE ({ticket_id}) via {MODELS[provider]} ---"))
    print(f"{response}\n")

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
