"""The process that actually runs the pipeline.

There was no equivalent of this file in main.py, because main.py both decided
what to do and did it, in one process, and the two died together.

Here they are separate. main_temporal.py asks for a ticket to be handled; this
process picks that request up off a task queue and does the work. Kill this
process and the request does not disappear — it waits for a worker to come back.
That is the demo in a later challenge. For now, just leave it running.
"""

import asyncio
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker

# Same reason as mock_llm.py. The challenge runs this with its output redirected
# to worker.log, and Python block-buffers a redirect, so the banner below would
# sit in a buffer instead of reaching the file. The solve script polls that file
# for the banner, so without this it waits out its timeout on a worker that is
# actually up.
sys.stdout.reconfigure(line_buffering=True)

# Imported through the sandbox boundary for the same reason as in workflow.py:
# activities.py builds an OpenAI client at import time.
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import load_ticket, respond, understand
    from workflow import TicketWorkflow

TASK_QUEUE = "tickets"
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")


async def main():
    # Connect first, announce second.
    #
    # This ordering is deliberate and it is the third time this track has needed
    # it. The mock server used to print "Serving" before binding its socket, and
    # start-temporal used to report ready while the Web UI was still returning
    # 500s. Both handed out a false ready. The banner below is what the
    # challenge's solve script waits for, so it must not print unless the thing
    # it announces is genuinely up.
    client = await Client.connect(ADDRESS)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[TicketWorkflow],
        activities=[load_ticket, understand, respond],
    )

    print(f"worker ready: {ADDRESS}, task queue '{TASK_QUEUE}'")
    print("waiting for work. Ctrl+C to stop.")
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nworker stopped.")
