"""Propose a code change to one of the operator's repos.

pi (a coding agent on the operator's home box) makes the edit; the result
arrives as a patch-based pull request on tangled that the operator reviews
before anything merges. phi writes the pull request herself — the flow
publishes her words verbatim and composes none of its own.

Owner-gated, so it follows the same like-as-approval path as the other
gated tools: say what you want to change and why, and act once the
operator likes it.
"""

import logging
from typing import Annotated, Literal

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.tools._helpers import PhiDeps, _is_owner

logger = logging.getLogger("bot.tools.code_change")

# the repos pi may be pointed at. a closed set on purpose: the tool is the
# enforcement boundary, so "which codebase" can never come from a prompt.
Repo = Literal["my-prefect-server", "find-bufo", "bot", "tangled-mcp"]

DEPLOYMENT = "pi-pr/pi-pr"


def register(agent):
    @agent.tool
    async def propose_code_change(
        ctx: RunContext[PhiDeps],
        repo: Annotated[Repo, Field(description="which of the operator's repos to change")],
        instructions: Annotated[
            str,
            Field(
                description=(
                    "what the coding agent should do, concretely — it starts from "
                    "a fresh clone with no memory of this conversation, so name "
                    "files and behaviour rather than referring back to the thread"
                )
            ),
        ],
        title: Annotated[
            str,
            Field(description="pull request title, in your own words (<=72 chars)"),
        ],
        body: Annotated[
            str,
            Field(
                description=(
                    "pull request description, in your own words: what you want "
                    "changed and why. published verbatim under your identity, so "
                    "write it as yourself"
                )
            ),
        ],
    ) -> str:
        """Have a coding agent draft a change to one of the operator's repos and open a pull request for review.

        Owner-gated. Post what you want to change and why; the operator's like
        on that post authorizes exactly that change, and you can call this on
        the next batch.

        Returns as soon as the run is queued — the edit and the pull request
        take a few minutes. Check the run with the prefect tools, and report
        the outcome yourself when it lands.
        """
        if not _is_owner(ctx):
            return (
                f"only @{settings.owner_handle} can authorize a code change. post "
                "what you want to change and why; their like on that post "
                "authorizes it, and you can call this on the next batch."
            )
        if not settings.prefect_api_auth_string:
            return "prefect credentials are not configured, so i can't queue the run"
        if not title.strip() or not body.strip():
            return "write the title and body yourself — they are published as you"

        base = settings.prefect_api_url.rstrip("/")
        auth = httpx.BasicAuth(*settings.prefect_api_auth_string.split(":", 1))
        try:
            async with httpx.AsyncClient(timeout=30, auth=auth) as http:
                found = await http.get(f"{base}/deployments/name/{DEPLOYMENT}")
                found.raise_for_status()
                deployment_id = found.json()["id"]
                run = await http.post(
                    f"{base}/deployments/{deployment_id}/create_flow_run",
                    json={
                        "parameters": {
                            "task": instructions,
                            "title": title.strip(),
                            "body": body.strip(),
                            "repo": repo,
                        },
                        "tags": ["phi"],
                    },
                )
                run.raise_for_status()
                created = run.json()
        except Exception as e:
            logger.warning(f"could not queue {DEPLOYMENT}: {e}")
            return f"could not queue the change: {e}"

        logger.info(f"queued {DEPLOYMENT} for {repo}: {created.get('name')}")
        return (
            f"queued a change to {repo} as '{title.strip()}' "
            f"(run {created.get('name')}). it takes a few minutes; the pull "
            f"request will appear at https://tangled.org/{settings.owner_handle}/{repo}/pulls "
            "for the operator to review."
        )
