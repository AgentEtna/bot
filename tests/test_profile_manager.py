"""Tests for profile status marker handling.

Regression context: phi authors her own bio (up to bsky's 256-grapheme
cap) and the old pause/resume flow appended a 152-char capability
suffix to it, overflowing the cap — the PDS rejected the write and the
bio stayed "offline" while phi was online. Status flips must be
length-neutral.
"""

from unittest.mock import Mock

from bot.core.profile_manager import ProfileManager, _toggle_status_marker

PHI_AUTHORED_BIO = (
    "ai on fly.io. replies, remembers, follows threads. built and operated "
    "by @zzstoatzz.io. interested in small infrastructure, long-form "
    "writing, and connecting existing things. 🟢"
)


def test_offline_flips_green_to_red():
    assert _toggle_status_marker(PHI_AUTHORED_BIO, is_online=False) == (
        PHI_AUTHORED_BIO.replace("🟢", "🔴")
    )


def test_online_flips_red_to_green():
    offline = PHI_AUTHORED_BIO.replace("🟢", "🔴")
    assert _toggle_status_marker(offline, is_online=True) == PHI_AUTHORED_BIO


def test_online_collapses_legacy_offline_wording():
    legacy = (
        f"{PHI_AUTHORED_BIO.removesuffix(' 🟢')}\n\n"
        "source code: https://tangled.sh/zzstoatzz.io/bot\n\n🔴 offline"
    )
    result = _toggle_status_marker(legacy, is_online=True)
    assert "offline" not in result
    assert "🔴" not in result
    assert result.endswith("🟢")


def test_flip_never_grows_a_max_length_bio():
    max_length_bio = ("x" * 254 + " 🟢")[:256]
    for is_online in (True, False):
        flipped = _toggle_status_marker(max_length_bio, is_online)
        assert len(flipped) <= 256


def test_marker_free_bio_is_untouched():
    bio = "no markers here"
    assert _toggle_status_marker(bio, is_online=True) == bio
    assert _toggle_status_marker(bio, is_online=False) == bio


def _manager_with_bio(bio: str) -> tuple[ProfileManager, Mock]:
    client = Mock()
    client.me.did = "did:plc:test"
    record = Mock()
    record.description = bio
    record.display_name = "phi"
    record.avatar = None
    record.banner = None
    record.labels = None
    client.com.atproto.repo.get_record.return_value = Mock(value=record)
    return ProfileManager(client), client


async def test_set_online_status_writes_flipped_bio():
    pm, client = _manager_with_bio(PHI_AUTHORED_BIO)
    await pm.set_online_status(False)
    written = client.com.atproto.repo.put_record.call_args[0][0]["record"]
    assert written["description"] == PHI_AUTHORED_BIO.replace("🟢", "🔴")


async def test_set_online_status_skips_write_without_marker():
    pm, client = _manager_with_bio("no markers here")
    await pm.set_online_status(True)
    client.com.atproto.repo.put_record.assert_not_called()
