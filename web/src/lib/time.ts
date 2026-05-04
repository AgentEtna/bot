// Time helpers for the cockpit.
//
// Phi runs on operator-local time (America/Chicago). The UI mirrors that
// framing: relative ages stay locale-neutral ("3h ago"), absolute times
// render in CT/CDT, and a live operator clock sits in the chrome so the
// viewer reads the same wall-clock phi is reading.

const OPERATOR_TZ = 'America/Chicago';

// Mirror of bot/utils/time.py:relative_when — same granularity slide.
// Renders an ISO timestamp as 'Ns/m/h/d/mo/y ago'.
export function relativeWhen(iso: string | null | undefined): string {
	if (!iso) return '';
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return '';
	const delta = (Date.now() - ts) / 1000;
	if (delta < 0) return '';
	if (delta < 60) return `${Math.floor(delta)}s ago`;
	if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
	if (delta < 86400) {
		const h = delta / 3600;
		return h < 10 ? `${h.toFixed(1)}h ago` : `${Math.floor(h)}h ago`;
	}
	const days = delta / 86400;
	if (days < 30) {
		return days < 10 ? `${days.toFixed(1)}d ago` : `${Math.floor(days)}d ago`;
	}
	if (days < 365) return `${Math.floor(days / 30)}mo ago`;
	return `${Math.floor(days / 365)}y ago`;
}

// Absolute timestamp in operator-local time (e.g. "Apr 28 · 2:13 PM CDT").
// Used as the tooltip on relative-time spans so the viewer can see phi's
// own framing without losing the at-a-glance "3h ago" affordance.
export function absoluteCT(iso: string | null | undefined): string {
	if (!iso) return '';
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return '';
	const date = new Date(ts);
	const datePart = new Intl.DateTimeFormat('en-US', {
		timeZone: OPERATOR_TZ,
		month: 'short',
		day: 'numeric'
	}).format(date);
	const timePart = new Intl.DateTimeFormat('en-US', {
		timeZone: OPERATOR_TZ,
		hour: 'numeric',
		minute: '2-digit',
		timeZoneName: 'short'
	}).format(date);
	return `${datePart} · ${timePart}`;
}

// Combined tooltip string: absolute CT, then UTC on a second line for
// the rare case the viewer wants to cross-reference (logfire, etc).
export function whenTooltip(iso: string | null | undefined): string {
	if (!iso) return '';
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return '';
	const utc = new Date(ts).toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z');
	return `${absoluteCT(iso)}\n(UTC: ${utc})`;
}

// Live wall-clock string for the chrome, formatted as
// "THU · 14:13 CDT" — phi's current operator-local time.
export function operatorClock(now: Date = new Date()): string {
	const wd = new Intl.DateTimeFormat('en-US', {
		timeZone: OPERATOR_TZ,
		weekday: 'short'
	})
		.format(now)
		.toUpperCase();
	const hm = new Intl.DateTimeFormat('en-US', {
		timeZone: OPERATOR_TZ,
		hour: '2-digit',
		minute: '2-digit',
		hour12: false
	}).format(now);
	const tz = new Intl.DateTimeFormat('en-US', {
		timeZone: OPERATOR_TZ,
		timeZoneName: 'short'
	})
		.formatToParts(now)
		.find((p) => p.type === 'timeZoneName')?.value ?? 'CT';
	return `${wd} · ${hm} ${tz}`;
}
