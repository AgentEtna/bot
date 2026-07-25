"""What each of phi's tools can cost if it goes wrong.

A tool without a risk declaration is not a valid tool — `tests/test_abilities.py`
fails on any registered tool missing an entry here, and on any entry that no
longer matches a real tool. That bijection is the point: the declaration cannot
drift from the code in either direction.

The shape is defined by `lexicons/io/zzstoatzz/phi/abilities.json` and served at
`/xrpc/io.zzstoatzz.phi.getAbilities`, so phi's own risk surface is public and
readable the same way any other atproto service publishes its capabilities.

Magnitude is ordered by **reach and reversibility**, never by how often a tool
gets used:

    none      reads only; changes nothing anywhere
    low       changes phi's own private state; reversible
    moderate  publicly visible under her name; reversible by her
    high      reaches other people's attention, spends real money, or cannot
              be undone with the tools she actually has

`reason` names the concrete failure, in one sentence. It is read by a person in
the cockpit and by the policy judge when it evaluates a proposed call — so
"could post something bad" is useless and "a reply lands in someone's
notifications and cannot be un-notified" is not.
"""

from typing import Literal, TypedDict

Magnitude = Literal["none", "low", "moderate", "high"]

ORDER: dict[Magnitude, int] = {"none": 0, "low": 1, "moderate": 2, "high": 3}


class Risk(TypedDict):
    magnitude: Magnitude
    reason: str


RISK: dict[str, Risk] = {
    # --- reads: nothing changes -------------------------------------------
    "check_infra": {
        "magnitude": "none",
        "reason": "reads service health, the relay fleet, and phi's own changelog; changes nothing.",
    },
    "check_top_chicken": {
        "magnitude": "none",
        "reason": "reads the market board and phi's position; placing a trade is a separate tool.",
    },
    "check_urls": {
        "magnitude": "none",
        "reason": "fetches URLs, so it tells the other end that someone looked, but changes nothing on phi's side.",
    },
    "get_own_posts": {
        "magnitude": "none",
        "reason": "reads phi's own timeline; harmless, except that reading her own posts back is the voice-drift loop this repo keeps re-earning.",
    },
    "get_trending": {
        "magnitude": "none",
        "reason": "reads coral's trending entities, which phi's own editorial notes help produce — so it is one side of a feedback loop she should not mistake for the world.",
    },
    "inspect_atlas": {
        "magnitude": "none",
        "reason": "reads the daily atlas blob that is already in her context as a digest, so the cost is context spent re-reading what she has.",
    },
    "inspect_record_media": {
        "magnitude": "none",
        "reason": "fetches blobs from a record so she can actually look at an image instead of inferring it.",
    },
    "list_blog_posts": {
        "magnitude": "none",
        "reason": "reads her published documents; the drafting risk lives in publish_blog_post, not here.",
    },
    "list_goals": {
        "magnitude": "none",
        "reason": "reads her goal records, which are also already summarised in her context every run.",
    },
    "query_traces": {
        "magnitude": "none",
        "reason": "reads her own execution traces; an unbounded query wastes context but breaks nothing.",
    },
    "read_feed": {
        "magnitude": "none",
        "reason": "reads a feed; a post found this way is not an invitation to reply to it, which is the uninvited-reply policy.",
    },
    "search_memory": {
        "magnitude": "none",
        "reason": "reads her private vector memory, where a bad note stored earlier resurfaces looking like an established fact.",
    },
    "search_posts": {
        "magnitude": "none",
        "reason": "searches the network; finding a post is not an invitation to reply to it.",
    },
    "web_search": {
        "magnitude": "none",
        "reason": "searches the open web via tavily; costs a metered request but changes nothing.",
    },
    # --- low: her own private state ---------------------------------------
    "save_memory": {
        "magnitude": "low",
        "reason": "writes to her private vector store; a wrong note resurfaces later as if it were true, which is the extraction feedback loop this repo has hit before.",
    },
    # --- moderate: public under her name, reversible ------------------------
    "write_bio": {
        "magnitude": "moderate",
        "reason": "rewrites her public profile description; visible to everyone who looks at her, and she can rewrite it again.",
    },
    "like_post": {
        "magnitude": "moderate",
        "reason": "notifies one person that phi read them, which is a social act she cannot un-send even after unliking.",
    },
    "follow_user": {
        "magnitude": "moderate",
        "reason": "notifies the account and changes who phi is publicly associated with; reversible, and owner-gated because who she follows reads as endorsement.",
    },
    "manage_account": {
        "magnitude": "moderate",
        "reason": "edits the mention-consent allowlist, which decides whose notifications phi is able to reach at all.",
    },
    "propose_goal_change": {
        "magnitude": "moderate",
        "reason": "rewrites the constitutional fields of a public goal record; owner-gated because goals are what she is for.",
    },
    "update_goal_progress": {
        "magnitude": "moderate",
        "reason": "writes progress onto a public goal record; wrong state here misleads her own later reasoning.",
    },
    "update_chicken_strategy": {
        "magnitude": "moderate",
        "reason": "rewrites her public trading doctrine record, which she then trades against.",
    },
    # --- high: reaches others, spends money, or cannot be undone -----------
    "post": {
        "magnitude": "high",
        "reason": "a reply lands in someone's notifications and cannot be un-notified; a top-level post is read by people who were not in the conversation that produced it.",
    },
    "repost_post": {
        "magnitude": "high",
        "reason": "amplifies someone else's post to phi's followers under her name, which endorses whatever it turns out to say.",
    },
    "publish_blog_post": {
        "magnitude": "high",
        "reason": "publishes a document to greengale under her name and she has no tool to delete one afterwards.",
    },
    "generate_image": {
        "magnitude": "high",
        "reason": "spends real money per call and uploads a blob to her PDS.",
    },
    "place_chicken_trade": {
        "magnitude": "high",
        "reason": "spends real money on a live market; a filled order cannot be recalled.",
    },
    "manage_feeds": {
        "magnitude": "high",
        "reason": "creates or deletes public feeds other people may be subscribed to; the delete is not recoverable from here.",
    },
}


def risk_of(tool_name: str) -> Risk | None:
    """The declaration for a tool, or None when it has none."""
    return RISK.get(tool_name)


def describe(tool_name: str) -> str:
    """One line for the policy judge, empty when the tool is unknown.

    The judge already receives the proposed action and its provenance; this
    adds what the tool itself can cost, so a borderline call is weighed
    against a real consequence rather than the judge's guess at one.
    """
    risk = RISK.get(tool_name)
    if not risk:
        return ""
    return f"{tool_name} is {risk['magnitude']}-risk: {risk['reason']}"


def at_least(tool_name: str, magnitude: Magnitude) -> bool:
    """Whether a tool's declared risk reaches `magnitude`.

    Nothing enforces on this yet — the gate that will read it is a later
    change. It exists so the ordering is expressed once, here, rather than
    re-derived at each call site.
    """
    risk = RISK.get(tool_name)
    if not risk:
        return False
    return ORDER[risk["magnitude"]] >= ORDER[magnitude]
