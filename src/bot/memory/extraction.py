"""Observation extraction and reconciliation pipeline.

Models, prompts, and agent factories for extracting facts from conversations
and reconciling new observations against existing memory.
"""

from atproto_client.models.string_formats import AtUri
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from bot.config import settings


class Observation(BaseModel):
    """A single fact about the user, extracted from what the USER said or did."""

    content: str = Field(
        description="one atomic fact about the user, stated as a short sentence"
    )
    tags: list[str] = Field(
        default_factory=list,
        min_length=0,
        max_length=3,
        description="0-3 lowercase topic tags (not person names, not meta-categories like 'interests')",
    )
    source_uris: list[AtUri] = Field(
        default_factory=list,
        description=(
            "AT-URIs that back this observation: the post(s), exchange(s), or "
            "like(s) that justify the claim. cite when you can — empty is "
            "allowed but treated as lower-trust on read. the URI's own "
            "structure (DID + collection NSID + TID) carries author, kind, "
            "and timestamp — no separate fields needed."
        ),
    )


class ExtractionResult(BaseModel):
    """Observations extracted from a conversation. Empty list if nothing worth keeping."""

    observations: list[Observation] = []


class ReconciliationAction(BaseModel):
    """Decision for how a new observation relates to an existing one."""

    action: str = Field(description="one of: ADD, UPDATE, DELETE, NOOP")
    new_content: str | None = Field(
        default=None, description="merged content when action is UPDATE"
    )
    new_tags: list[str] | None = Field(
        default=None, description="merged tags when action is UPDATE"
    )
    reason: str = Field(description="brief explanation of the decision")


class ReconciliationResult(BaseModel):
    """Result of reconciling a new observation against a similar existing one."""

    decision: ReconciliationAction


EXTRACTION_SYSTEM_PROMPT = """\
You extract facts about the USER from a conversation between a user and a bot.

Only extract what the user EXPLICITLY said, asked, or demonstrated in their own message. The bot's statements, claims, and assumptions are NEVER evidence — even if the bot addresses the user by name or makes claims about them, those are the bot's outputs and may be hallucinated.

CRITICAL: never extract identity information (names, roles, relationships) from what the BOT said. only extract a name if the USER explicitly stated it themselves.

<examples>
<example>
user: have you considered following anyone yet?
bot: following one account currently — bsky.app itself.
observations: []
reason: the user asked a question. the bot answered about itself. nothing here is about the user.
</example>
<example>
user: can you delete that follow record?
bot: deleted it — following nobody now.
observations: []
reason: the user made a request to the bot. the bot performed the action. the user didn't delete anything.
</example>
<example>
user: what do you think about the strait of hormuz situation?
bot: trump considered a blockade, major shipping implications.
observations: [{"content": "interested in geopolitical events around the strait of hormuz", "tags": ["geopolitics"]}]
reason: the user asked about a specific topic, showing interest. the bot's answer content is not attributed to the user.
</example>
<example>
user: i've been learning rust lately, it's been great for my systems work
bot: rust is excellent for systems programming.
observations: [{"content": "learning rust for systems programming", "tags": ["rust", "programming"]}]
reason: the user stated something about themselves directly.
</example>
<example>
user: my name isn't zoë, it's sam.
bot: sorry about that — you're sam. bad breadcrumb on my end.
observations: [{"content": "name is sam (corrected from previous error)", "tags": ["correction"]}]
reason: the user explicitly corrected a factual error. corrections are high-value observations.
</example>
<example>
user: what do you remember about me?
bot: you're alex, my creator. you care about security and testing.
observations: []
reason: the user asked a question. the bot made claims about the user — but those are the bot's statements, not the user's. never extract identity from bot output.
</example>
</examples>

tag rules:
- tags categorize the TOPIC, not the person. never use a person's name, handle, or "person-*" as a tag.
- use concrete topics: "atproto", "memory", "music", "infrastructure", "rust" — not meta-categories like "interests" or "identity".
- 1-3 tags per observation. if nothing fits, use an empty list.

Return an empty list when the exchange is just greetings, filler, or the user only asked questions without revealing anything about themselves."""

RECONCILIATION_SYSTEM_PROMPT = """\
You reconcile a NEW observation against an EXISTING observation from memory.

Decide one action:
- ADD: the new observation contains genuinely different information. keep both.
- UPDATE: the new observation refines, corrects, or supersedes the existing one. return merged content and tags.
- DELETE: the existing observation is wrong, outdated, or fully redundant given the new one. the new one will be stored separately.
- NOOP: the new observation adds nothing beyond what already exists. discard it.

Corrections (e.g., "name is sam, corrected from previous error") always win over the entry they correct — use UPDATE or DELETE.
When in doubt between ADD and NOOP, prefer NOOP. memory should be lean."""

_reconciliation_agent: Agent[None, ReconciliationResult] | None = None


def get_reconciliation_agent() -> Agent[None, ReconciliationResult]:
    global _reconciliation_agent
    if _reconciliation_agent is None:
        _reconciliation_agent = Agent[None, ReconciliationResult](
            name="observation-reconciler",
            model=settings.extraction_model,
            output_type=ReconciliationResult,
            system_prompt=RECONCILIATION_SYSTEM_PROMPT,
        )
    agent = _reconciliation_agent
    assert agent is not None
    return agent


EPISODIC_SCHEMA = {
    "content": {"type": "string", "full_text_search": True},
    "tags": {"type": "[]string", "filterable": True},
    "source": {"type": "string", "filterable": True},  # "tool", "conversation"
    "source_uris": {"type": "[]string"},  # AT-URIs backing this memory (optional)
    "created_at": {"type": "string"},
}

USER_NAMESPACE_SCHEMA = {
    "kind": {"type": "string", "filterable": True},
    "status": {"type": "string", "filterable": True},  # active, superseded
    "content": {"type": "string", "full_text_search": True},
    "tags": {"type": "[]string", "filterable": True},
    "supersedes": {"type": "string"},  # id of observation this replaces
    # AT-URIs backing this row. for observations: the post(s) that justify it.
    # for interactions: [parent_uri, bot_post_uri]. empty is allowed but read
    # as lower-trust ("uncited"). DID + NSID + TID are extractable from the
    # URI itself, so author / kind / timestamp need no separate fields.
    "source_uris": {"type": "[]string"},
    "created_at": {"type": "string"},
    "updated_at": {"type": "string"},
}
