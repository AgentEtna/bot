"""Drift enforcement for docs/system-prompt.md.

The doc is a hand-written reference for what reaches the model each run.
Hand-written is the right call (it carries judgment a generator can't),
but it rots silently when a new dynamic block or policy ships without a
doc update. This test makes the rot loud: every `inject_*` system-prompt
function in agent.py and every policy slug must be mentioned in the doc.

If this fails, add a row to docs/system-prompt.md — don't weaken the test.
"""

import re
from pathlib import Path

from bot.core.policy import POLICIES

ROOT = Path(__file__).parent.parent
AGENT_SRC = (ROOT / "src" / "bot" / "agent.py").read_text()
DOC = (ROOT / "docs" / "system-prompt.md").read_text()


def test_every_prompt_block_function_is_documented():
    inject_fns = sorted(set(re.findall(r"def (inject_\w+)", AGENT_SRC)))
    assert inject_fns, "no inject_* functions found — did agent.py move?"
    missing = [fn for fn in inject_fns if fn not in DOC]
    assert not missing, (
        f"dynamic system-prompt blocks missing from docs/system-prompt.md: "
        f"{missing} — add a row to the block table"
    )


def test_every_policy_slug_is_documented():
    missing = [slug for slug in POLICIES if slug not in DOC]
    assert not missing, (
        f"policy slugs missing from docs/system-prompt.md: {missing} — "
        f"the policies render into operational instructions; document them"
    )
