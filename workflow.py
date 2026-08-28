"""The pipeline itself. Compare this against main() in main.py.

Same two steps in the same order. The differences are the whole workshop:

  main.py                               workflow.py
  -------                               -----------
  state = {...} in process memory       self.* recorded in the Workflow's history
  client retries, invisibly             RetryPolicy, declared and durable
  crash = start again from zero         crash = resume from the last completed step
  state visible only via print()        state readable from outside, via a Query
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# Imported inside this block because the Workflow sandbox re-imports this
# module, and activities.py builds an OpenAI client at import time. Passing it
# through means the sandbox uses the already-imported module rather than
# constructing a second client per Workflow run.
with workflow.unsafe.imports_passed_through():
    from activities import (
        MODELS,
        QUOTA_EXHAUSTED,
        AnalysisInput,
        ResponseInput,
        analysis,
        load_ticket,
        respond,
    )

# 
# 
# 
#  
# 
# 
#
# 
# Retry configuration must be deterministic for a Workflow execution.
# Do not read mutable environment or external configuration during Workflow replay.
# These values are literals here so replay always schedules the same retry policy.
# Configuration can instead be passed into the Workflow as input
# or fixed as part of the deployed code.
# 
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=8,
)

# How long a single attempt is allowed to take before Temporal gives up on it
# and applies the policy above.
ATTEMPT_TIMEOUT = timedelta(seconds=30)

# The DECISION banner, as escape codes rather than a plain string.
#
# No isatty() check, unlike main.py, and for two reasons. Workflow code must not
# read the environment — the rule that keeps the retry policy a literal applies
# here too. And this output is only ever read by a human: either live in the
# Worker tab, or in worker.log, which the attendee opens directly. mock_llm.py
# made the same call for the same reason.
#
# Colour the whole line, never the middle of one. Any script that greps worker.log
# for DECISION must be unanchored or strip escapes first — the third rake this
# project has stepped on, see "Colour broke every anchored check script".
BOLD_AMBER = "\033[1;33m"
RESET = "\033[0m"
SPINE = f"{BOLD_AMBER}▌{RESET}"
RULE = f"{BOLD_AMBER}▌{'─' * 62}{RESET}"

# How long to wait before failing over to a second provider.
#
# main.py reads this from FALLBACK_PAUSE_SECONDS in .env. This cannot, for the
# same reason the retry policy above cannot: Workflow code is replayed, and a
# value read from the environment during a replay could differ from the original
# run. So it is a literal, and the asymmetry with main.py is the point.
#
# The pause is real engineering, not a stage prop — you do not want to hammer a
# new provider the instant the first one hiccups. But it is also the window the
# workshop asks you to kill the worker in, and unlike main.py's time.sleep() it
# is a durable timer held by the server. It keeps running while nothing is alive
# to wait for it.
FALLBACK_PAUSE = timedelta(seconds=10)


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

        # Which provider actually produced each artifact, as opposed to which one
        # the pipeline is using *now*. They differ the moment a fallback happens,
        # and main.py has no equivalent — it prints each block as it is produced,
        # so it never needs to remember. This starter prints both at the end, from
        # state, and without these two it labelled a gpt-4o-mini analysis as
        # claude-opus-5. Found by Les playing challenge 08.
        self.analysis_provider = None
        self.response_provider = None

    @workflow.run
    async def run(self, ticket_id: str) -> str:
        ticket = await workflow.execute_activity(
            load_ticket,
            ticket_id,
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )

        self.analysis = await workflow.execute_activity(
            analysis,
            AnalysisInput(ticket=ticket, provider=self.provider),
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )
        self.analysis_provider = self.provider

        try:
            self.response = await self.draft(ticket)
        except ActivityError as error:
            # Only one failure is worth catching here: the one that says trying
            # again will never work. Everything else has already been retried by
            # the policy and has earned the right to fail.
            if getattr(error.cause, "type", None) != QUOTA_EXHAUSTED:
                raise

            # This assignment is the whole challenge. main.py makes the identical
            # decision in report() (main.py:117) and then exits, taking it with
            # it. Here it is recorded before anything acts on it, so a crash in
            # the next ten seconds costs nothing.
            self.provider = "anthropic"

            # main.py's DECISION block, in the only place a Workflow can print:
            # the worker's log. Without it the attendee's only cue that the
            # failover is pending is the tail of a 50-line traceback, and this is
            # the moment the challenge asks them to kill the worker.
            #
            # warning(), not info(), and that is not a style choice: worker.py
            # configures no logging, so Python's last-resort handler applies and
            # anything below WARNING is silently dropped. An info() here is
            # invisible — verified the hard way. Raising it also needs no
            # basicConfig(), which would switch on temporalio's own INFO chatter
            # and bury the one line that matters.
            #
            # workflow.logger is replay-safe — it stays quiet while replaying, so
            # a restarted worker does not reprint this.
            workflow.logger.warning(
                "\n%s\n%s ⚠️  DECISION: tokens maxed out on %s. Retrying will not help."
                "\n%s 🔀 Falling back to %s in %ss."
                "\n%s 💾 provider=anthropic is recorded in the Workflow now,"
                " not in this process."
                "\n%s\n",
                RULE,
                SPINE,
                MODELS["openai"],
                SPINE,
                MODELS["anthropic"],
                int(FALLBACK_PAUSE.total_seconds()),
                SPINE,
                RULE,
            )

            # A durable timer, not a sleep in a process. The wait outlives the
            # worker that started it — kill the worker here and the failover still
            # happens, on time, once a worker exists again.
            await workflow.sleep(FALLBACK_PAUSE)

            self.response = await self.draft(ticket)

        return self.response

    async def draft(self, ticket: str) -> str:
        """The reply, on whichever provider self.provider currently names."""
        provider = self.provider
        reply = await workflow.execute_activity(
            respond,
            ResponseInput(
                ticket=ticket,
                analysis=self.analysis,
                provider=provider,
            ),
            start_to_close_timeout=ATTEMPT_TIMEOUT,
            retry_policy=RETRY,
        )
        # Recorded after the await, so a call that failed never claims credit.
        self.response_provider = provider
        return reply

    @workflow.query
    def get_state(self) -> dict:
        """The equivalent of show_state() in main.py, with one difference.

        show_state() can only print to the terminal of the process that owns the
        state. This can be called from anywhere, while the Workflow is running,
        by something that is not this process:

            temporal workflow query --workflow-id ticket-1041 --type get_state

        Needs a worker running, though — a Query executes this code, so the server
        cannot answer one on its own. Killing the worker and querying does not
        work, however good a proof of durability it would be.
        """
        return {
            "provider": self.provider,
            "analysis": self.analysis,
            "response": self.response,
            # Not in main.py's state dict, because main.py cannot need them: it
            # prints each block as it is produced. After a fallback these two say
            # who produced what, which is how the starter labels the output.
            "analysis_provider": self.analysis_provider,
            "response_provider": self.response_provider,
        }
