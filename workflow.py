"""The pipeline itself. Compare this against main() in main.py.

Same two steps in the same order. The differences are the whole workshop:

  main.py                         workflow.py
  -------                         -----------
  state = {...} in process memory self.* recorded in the Workflow's history
  client retries, invisibly       RetryPolicy, declared and durable
  crash = start again from zero   crash = resume from the last completed step
  state visible only via print()  state readable from outside, via a Query
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Imported inside this block because the Workflow sandbox re-imports this
# module, and activities.py builds an OpenAI client at import time. Passing it
# through means the sandbox uses the already-imported module rather than
# constructing a second client per Workflow run.
with workflow.unsafe.imports_passed_through():
    from activities import (
        AnalysisInput,
        ResponseInput,
        load_ticket,
        respond,
        understand,
    )

# Retry configuration is code here, not an environment variable.
#
# main.py reads MAX_RETRIES from .env at import and hands it to the OpenAI
# client. That cannot work in a Workflow: Workflow code is replayed, and reading
# the environment during a replay could produce a different answer than it did
# on the original run, which is a non-determinism bug.
#
# maximum_attempts is total attempts, not retries after the first. 3 here is the
# same budget as main.py's default MAX_RETRIES=2.
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)

# How long a single attempt is allowed to take before Temporal gives up on it
# and applies the policy above.
ATTEMPT_TIMEOUT = timedelta(seconds=30)


@workflow.defn
class TicketWorkflow:
    def __init__(self):
        # The same three fields as the `state` dict on line 18 of main.py,
        # deliberately named the same so the two can be read side by side.
        #
        # In main.py these live in process memory and are gone the moment the
        # process exits. Here every assignment below is reconstructible from the
        # Workflow's event history, so they survive the process that set them.
        self.provider = "openai"
        self.analysis = None
        self.response = None

    @workflow.run
    async def run(self, ticket_id: str) -> str:
        ticket = await workflow.execute_activity(
            load_ticket,
            ticket_id,
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )

        self.analysis = await workflow.execute_activity(
            understand,
            AnalysisInput(ticket=ticket, provider=self.provider),
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )

        self.response = await workflow.execute_activity(
            respond,
            ResponseInput(
                ticket=ticket,
                analysis=self.analysis,
                provider=self.provider,
            ),
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )

        return self.response

    @workflow.query
    def get_state(self) -> dict:
        """The equivalent of show_state() in main.py, with one difference.

        show_state() can only print to the terminal of the process that owns the
        state. This can be called from anywhere, while the Workflow is running,
        by something that is not this process:

            temporal workflow query --workflow-id ticket-1041 --type get_state
        """
        return {
            "provider": self.provider,
            "analysis": self.analysis,
            "response": self.response,
        }
