/**
 * Shared cross-component state for the cockpit chrome.
 *
 * - hudReadout: short text shown in the bottom-right chrome (the "scan" line).
 *   Lenses set this on hover; clearing it on mouseout returns to "idle".
 *
 * - logbook: when set, slides in a drawer with full record details. Clicking
 *   a point sets it; close button clears it.
 *
 * Using rune .svelte.ts module — fields are reactive everywhere imported.
 */

import type { LogbookEntry } from './types';

function cell<T>(initial: T) {
	let v = $state(initial);
	return {
		get value(): T {
			return v;
		},
		set(next: T) {
			v = next;
		}
	};
}

export const hudReadout = cell<string>('');
export const logbook = cell<LogbookEntry | null>(null);
export const mindCounts = cell({
	obs: 0,
	goals: 0,
	out: 0,
	ppl: 0,
	cand: 0,
	loaded: false
});
