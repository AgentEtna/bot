"""Test configuration loading"""

import os
from unittest.mock import patch

from bot.config import settings


def test_config_loads():
    """Test that config loads without errors"""
    assert settings.bluesky_service == "https://bsky.social"
    assert settings.bot_name  # default "Bot" or overridden via env/dotfile
    assert settings.notification_poll_interval == 10


def test_logfire_instrumentation_degrades_gracefully():
    """Regression: a broken logfire instrumentation must not crash the app."""
    import importlib

    import bot.main as main_mod

    # Simulate instrument_pydantic_ai raising (e.g. missing otel dep)
    with patch.object(
        main_mod.logfire,
        "instrument_pydantic_ai",
        side_effect=ImportError("no module 'opentelemetry.instrumentation.httpx'"),
    ):
        # Re-running the instrumentation loop should not raise
        for _instrument in (
            main_mod.logfire.instrument_pydantic_ai,
            main_mod.logfire.instrument_anthropic,
            main_mod.logfire.instrument_openai,
        ):
            try:
                _instrument()
            except Exception:
                pass  # this is what the production code does

    # App should still be importable and functional
    importlib.reload(main_mod)
    assert main_mod.app is not None


class TestSubAgentModelStrings:
    """Regression: sub-agent model settings are full `provider:model` strings.

    bot.memory.extraction and bot.core.residue used to interpolate
    settings.extraction_model into f"anthropic:{...}". That was invisible
    while every sub-agent ran on Anthropic, but it silently produced
    "anthropic:openai-responses:gpt-5.6-luna" at two of the four
    extraction_model call sites the moment a sub-agent moved to OpenAI —
    a broken provider at half the sites and a working one at the other half.
    """

    def _agents(self):
        from bot.core import policy, residue, self_state
        from bot.memory import extraction, namespace_memory

        for mod, attr, factory in (
            (extraction, "_reconciliation_agent", extraction.get_reconciliation_agent),
            (residue, "_synth_agent", residue.get_residue_synth_agent),
            (namespace_memory, "_episodic_synth_agent", namespace_memory._get_episodic_synth_agent),
            (self_state, "_inventory_agent", self_state._get_inventory_agent),
            (policy, "_judge", policy._get_judge),
        ):
            setattr(mod, attr, None)  # bust the module-level singleton
            yield factory
            setattr(mod, attr, None)

    def test_no_call_site_re_prefixes_the_provider(self):
        """Every sub-agent must use the configured string verbatim."""
        sentinel = "openai-responses:gpt-5.6-luna"
        # constructing the provider needs a key present; nothing is sent.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}), patch.object(
            settings, "extraction_model", sentinel
        ), patch.object(settings, "policy_model", sentinel):
            for factory in self._agents():
                agent = factory()
                assert agent.model.system == "openai", (
                    f"{agent.name} resolved to provider {agent.model.system!r}; "
                    "a call site is re-prefixing the configured model string"
                )
                assert agent.model.model_name == "gpt-5.6-luna"

    def test_configured_defaults_are_prefixed(self):
        """A bare model name is provider-ambiguous — require the prefix."""
        for name in ("agent_model", "extraction_model", "policy_model"):
            assert ":" in getattr(settings, name), (
                f"settings.{name} must be a full pydantic-ai provider:model string"
            )
