"""Manage bot profile status updates."""

import logging
from typing import Any

from atproto import Client

logger = logging.getLogger("bot.profile_manager")


def _read_profile(client: Client) -> Any:
    """Read the current profile record, returning the raw value."""
    assert client.me is not None
    response = client.com.atproto.repo.get_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.actor.profile",
            "rkey": "self",
        }
    )
    return response.value


def _build_profile_data(current) -> dict:
    """Build a profile_data dict from the current profile, preserving all fields."""
    profile_data: dict = {"$type": "app.bsky.actor.profile"}

    if current.description:
        profile_data["description"] = current.description
    if current.display_name:
        profile_data["displayName"] = current.display_name
    if current.avatar:
        profile_data["avatar"] = {
            "$type": "blob",
            "ref": {"$link": current.avatar.ref.link},
            "mimeType": current.avatar.mime_type,
            "size": current.avatar.size,
        }
    if current.banner:
        profile_data["banner"] = {
            "$type": "blob",
            "ref": {"$link": current.banner.ref.link},
            "mimeType": current.banner.mime_type,
            "size": current.banner.size,
        }

    # Preserve existing self-labels
    if current.labels:
        try:
            values = [{"val": lbl.val} for lbl in current.labels.values]
            if values:
                profile_data["labels"] = {
                    "$type": "com.atproto.label.defs#selfLabels",
                    "values": values,
                }
        except (AttributeError, TypeError):
            pass  # no parseable labels on profile

    return profile_data


def _toggle_status_marker(bio: str, is_online: bool) -> str:
    """Swap the 🟢/🔴 status marker in the bio text, length-safely.

    phi authors her own bio (via write_bio) and includes a 🟢 marker;
    pause/resume just flips the emoji rather than appending suffixes —
    appending can overflow bsky's 256-grapheme cap on a full-length
    phi-authored bio. "🔴 offline" is the legacy offline wording;
    collapse it to a bare marker when coming online.
    """
    if is_online:
        return bio.replace("🔴 offline", "🟢").replace("🔴", "🟢")
    return bio.replace("🟢", "🔴")


def _write_profile(client: Client, profile_data: dict) -> None:
    """Write the profile record."""
    assert client.me is not None
    client.com.atproto.repo.put_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.actor.profile",
            "rkey": "self",
            "record": profile_data,
        }
    )


def get_self_labels(client: Client) -> list[str]:
    """Return the current list of self-label values on the profile."""
    current = _read_profile(client)
    if not current.labels:
        return []
    try:
        return [lbl.val for lbl in current.labels.values]
    except (AttributeError, TypeError):
        return []


def add_self_label(client: Client, label: str) -> list[str]:
    """Add a self-label to the profile. Returns the updated label list."""
    current = _read_profile(client)
    profile_data = _build_profile_data(current)

    # Get existing label values or start fresh
    existing = set()
    if "labels" in profile_data:
        existing = {v["val"] for v in profile_data["labels"]["values"]}

    existing.add(label)
    profile_data["labels"] = {
        "$type": "com.atproto.label.defs#selfLabels",
        "values": [{"val": v} for v in sorted(existing)],
    }

    _write_profile(client, profile_data)
    return sorted(existing)


def remove_self_label(client: Client, label: str) -> list[str]:
    """Remove a self-label from the profile. Returns the updated label list."""
    current = _read_profile(client)
    profile_data = _build_profile_data(current)

    existing = set()
    if "labels" in profile_data:
        existing = {v["val"] for v in profile_data["labels"]["values"]}

    existing.discard(label)
    if existing:
        profile_data["labels"] = {
            "$type": "com.atproto.label.defs#selfLabels",
            "values": [{"val": v} for v in sorted(existing)],
        }
    else:
        profile_data.pop("labels", None)

    _write_profile(client, profile_data)
    return sorted(existing)


class ProfileManager:
    """Manages bot profile updates."""

    def __init__(self, client: Client):
        self.client = client

    async def initialize(self):
        """Ensure the bot self-label is present on the profile."""
        try:
            labels = get_self_labels(self.client)
            if "bot" not in labels:
                labels = add_self_label(self.client, "bot")
                logger.info(f"set bot label, labels now: {labels}")
        except Exception as e:
            logger.error(f"failed to check profile labels: {e}")

    async def set_description(self, text: str):
        """Write the bio description directly.

        Used by `PhiAgent.process_bio` at startup — phi has just authored
        a fresh bio and we want exactly that text on the profile.
        """
        try:
            current = _read_profile(self.client)
            profile_data = _build_profile_data(current)
            profile_data["description"] = text
            _write_profile(self.client, profile_data)
            logger.info(f"updated profile bio (phi-authored): {text}")
        except Exception as e:
            logger.error(f"failed to set bio: {e}")

    async def set_online_status(self, is_online: bool):
        """Flip the 🟢/🔴 marker in the current bio to reflect status."""
        try:
            current = _read_profile(self.client)
            bio = current.description or ""
            new_bio = _toggle_status_marker(bio, is_online)
            if new_bio == bio:
                logger.info("bio has no status marker to flip; leaving it as-is")
                return

            profile_data = _build_profile_data(current)
            profile_data["description"] = new_bio
            _write_profile(self.client, profile_data)
            logger.info(f"updated profile bio: {new_bio}")

        except Exception as e:
            logger.error(f"failed to update profile status: {e}")
            # Don't fail the whole app if profile update fails
