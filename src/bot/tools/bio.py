"""Bio tool — phi rewrites her own bsky profile bio.

Lives as a tool on the main agent (rather than a separate `_bio_agent`)
specifically so phi has access to all the dynamic context blocks she'd
have during normal notification handling — `[OPERATOR]`, `[GOALS AND INTERESTS]`,
etc. without those, she falls back to training-
context guesses for things like the operator's handle, which produces
wrong-but-plausible text.

The 256-char cap is enforced structurally via `Annotated[str, Field(...)]`
on the tool parameter — pydantic refuses to dispatch the call if phi
overruns. Triggered at startup by `PhiAgent.process_bio`; phi can also
call it on her own initiative if she wants to update mid-run.
"""

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext

from bot.core.atproto_client import bot_client
from bot.core.profile_manager import ProfileManager
from bot.tools._helpers import PhiDeps


def register(agent):
    @agent.tool
    async def write_bio(
        ctx: RunContext[PhiDeps],
        text: Annotated[
            str,
            Field(
                max_length=256,
                description=(
                    "Your new bsky profile bio. 256 chars max (structurally "
                    "enforced). Plain text. Include a 🟢 somewhere if you "
                    "want the pause/resume system to be able to swap it to "
                    "🔴 on shutdown."
                ),
            ),
        ],
    ) -> str:
        """Rewrite your bsky profile bio.

        The bio should communicate what you are, your capabilities, and who
        your operator is — pull the operator's handle from the [OPERATOR]
        block in your context, not from training memory. 256 character cap
        is structurally enforced; the tool will refuse longer text.
        """
        # Late import: bot.main imports the agent (which imports this tool)
        # at startup, so the module-level import would cycle.
        try:
            from bot.main import app

            pm: ProfileManager | None = getattr(app.state, "profile_manager", None)
            if pm is not None:
                await pm.set_description(text)
            else:
                # No live ProfileManager (e.g. unit-test path) — fall back to
                # a direct profile write so the tool still does something
                # observable.
                from bot.core.profile_manager import (
                    _build_profile_data,
                    _read_profile,
                    _write_profile,
                )

                current = _read_profile(bot_client.client)
                profile_data = _build_profile_data(current)
                profile_data["description"] = text
                _write_profile(bot_client.client, profile_data)
        except Exception as e:
            return f"failed to update bio: {e}"

        return f"bio updated ({len(text)} chars)"
