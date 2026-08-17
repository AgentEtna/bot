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
    # logfire alert incidents (core/alert_watch.py) and the per-alert
    # last_run cursor that keeps a re-observed firing from counting twice.
    alert_incidents: dict = field(default_factory=dict)
    alert_watch_cursor: dict = field(default_factory=dict)

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

    def record_operator_mention(self, alert_keys: list[str]) -> None:
        """Stamp alert incidents phi just @-mentioned the operator about.

        Structural, so she is never asked to self-report having done it —
        the [ALERT WATCH] render flips those incidents to 'operator
        notified' and withdraws the escalation flag.
        """
        if not alert_keys:
            return
        from bot.core.alert_watch import mark_mentioned

        self.alert_incidents = mark_mentioned(
            self.alert_incidents, alert_keys, datetime.now(UTC).timestamp()
        )
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
                "alert_incidents": self.alert_incidents,
                "alert_watch_cursor": self.alert_watch_cursor,
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
            self.alert_incidents = dict(data.get("alert_incidents") or {})
            self.alert_watch_cursor = dict(data.get("alert_watch_cursor") or {})
            logger.info(
                f"restored status: {self.mentions_received} mentions, {self.responses_sent} responses"
            )
        except Exception as e:
            logger.warning(f"failed to load status: {e}")


# Global status instance
bot_status = BotStatus()
bot_status._load()
