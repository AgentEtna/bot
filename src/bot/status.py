"""Bot status tracking with persistence."""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("bot.status")

STATUS_FILE = Path("/data/status.json")


@dataclass
class BotStatus:
    """Tracks bot status and activity, persisted to disk."""

    start_time: datetime = field(default_factory=datetime.now)
    mentions_received: int = 0
    responses_sent: int = 0
    errors: int = 0
    last_mention_time: datetime | None = None
    last_response_time: datetime | None = None
    ai_enabled: bool = False
    polling_active: bool = False
    paused: bool = False
    # Most recent pause/resume timestamps (UTC). Surfaced to phi so she
    # knows when she was offline — informs how to handle a catchup batch.
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    workflow_failure_monitor_seeded: bool = False
    workflow_failure_run_ids: list[str] = field(default_factory=list)
    workflow_incidents: dict = field(default_factory=dict)
    # incidents phi has seen but not yet said anything about. they render in
    # her context until a post clears them, so silence stays visible.
    pending_incidents: dict = field(default_factory=dict)
    # when phi last rewrote her bio. the rewrite fires from the lifespan, so
    # every deploy triggers one — 19 deploys on 2026-07-25 earned her a
    # bluesky "changes profile often" label. restarts are not new days.
    last_bio_at: datetime | None = None

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def uptime_str(self) -> str:
        seconds = int(self.uptime_seconds)
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{secs}s")

        return " ".join(parts)

    def record_mention(self):
        self.mentions_received += 1
        self.last_mention_time = datetime.now()
        self._save()

    def record_response(self):
        self.responses_sent += 1
        self.last_response_time = datetime.now()
        self._save()

    def record_error(self):
        self.errors += 1
        self._save()

    def record_paused(self):
        self.paused = True
        self.paused_at = datetime.now(UTC)
        self._save()

    def record_resumed(self):
        self.paused = False
        self.resumed_at = datetime.now(UTC)
        self._save()

    def record_bio_write(self) -> None:
        self.last_bio_at = datetime.now(UTC)
        self._save()

    def bio_written_within(self, hours: float) -> bool:
        """Whether a bio rewrite already happened recently enough to skip."""
        if self.last_bio_at is None:
            return False
        age = datetime.now(UTC) - self.last_bio_at
        return age.total_seconds() < hours * 3600

    def clear_pending_incidents(self, run_ids: list[str]) -> None:
        """Mark incidents as addressed once phi has actually posted."""
        if not run_ids:
            return
        for run_id in run_ids:
            self.pending_incidents.pop(run_id, None)
        self._save()

    def record_workflow_failures(self, run_ids: list[str]):
        """Persist delivered Prefect failure IDs so alerts survive restarts."""
        self.workflow_failure_monitor_seeded = True
        known = set(self.workflow_failure_run_ids)
        for run_id in run_ids:
            if run_id not in known:
                self.workflow_failure_run_ids.append(run_id)
                known.add(run_id)
        self.workflow_failure_run_ids = self.workflow_failure_run_ids[-200:]
        self._save()

    def _save(self):
        """Persist counters to disk."""
        if not STATUS_FILE.parent.exists():
            return
        try:
            data = {
                "mentions_received": self.mentions_received,
                "responses_sent": self.responses_sent,
                "errors": self.errors,
                "last_mention_time": self.last_mention_time.isoformat()
                if self.last_mention_time
                else None,
                "last_response_time": self.last_response_time.isoformat()
                if self.last_response_time
                else None,
                # `paused` is persisted so a deploy / machine restart doesn't
                # silently resume a bot the operator paused for a reason. the
                # timestamps below let phi see the most recent cycle in her
                # context block; the bool is what gates the poller.
                "paused": self.paused,
                "paused_at": self.paused_at.isoformat() if self.paused_at else None,
                "resumed_at": self.resumed_at.isoformat() if self.resumed_at else None,
                "workflow_failure_run_ids": self.workflow_failure_run_ids,
                "workflow_incidents": self.workflow_incidents,
                "pending_incidents": self.pending_incidents,
                "last_bio_at": self.last_bio_at.isoformat()
                if self.last_bio_at
                else None,
                "workflow_failure_monitor_seeded": self.workflow_failure_monitor_seeded,
            }
            STATUS_FILE.write_text(json.dumps(data))
        except Exception as e:
            logger.warning(f"failed to save status: {e}")

    def _load(self):
        """Restore counters from disk."""
        if not STATUS_FILE.exists():
            return
        try:
            data = json.loads(STATUS_FILE.read_text())
            self.mentions_received = data.get("mentions_received", 0)
            self.responses_sent = data.get("responses_sent", 0)
            self.errors = data.get("errors", 0)
            self.paused = bool(data.get("paused", False))
            if data.get("last_mention_time"):
                self.last_mention_time = datetime.fromisoformat(
                    data["last_mention_time"]
                )
            if data.get("last_response_time"):
                self.last_response_time = datetime.fromisoformat(
                    data["last_response_time"]
                )
            if data.get("paused_at"):
                self.paused_at = datetime.fromisoformat(data["paused_at"])
            if data.get("resumed_at"):
                self.resumed_at = datetime.fromisoformat(data["resumed_at"])
            self.workflow_failure_run_ids = list(
                data.get("workflow_failure_run_ids") or []
            )[-200:]
            self.workflow_incidents = dict(data.get("workflow_incidents") or {})
            self.pending_incidents = dict(data.get("pending_incidents") or {})
            if data.get("last_bio_at"):
                self.last_bio_at = datetime.fromisoformat(data["last_bio_at"])
            self.workflow_failure_monitor_seeded = bool(
                data.get("workflow_failure_monitor_seeded", False)
            )
            logger.info(
                f"restored status: {self.mentions_received} mentions, {self.responses_sent} responses"
            )
        except Exception as e:
            logger.warning(f"failed to load status: {e}")


# Global status instance
bot_status = BotStatus()
bot_status._load()
