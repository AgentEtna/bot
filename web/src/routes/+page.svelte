<script lang="ts">
	import { onMount } from 'svelte';
	import MindMap from '$lib/components/MindMap.svelte';
	import Logbook from '$lib/components/Logbook.svelte';
	import {
		getMemoryGraph,
		getDiscoveryPool,
		getActiveObservations,
		getGoals,
		getDocket,
		PHI_HANDLE
	} from '$lib/api';
	import type { GraphNode, DiscoveryEntry, Observation, Goal, Docket } from '$lib/types';

	let goals = $state<Goal[]>([]);
	let observations = $state<Observation[]>([]);
	let known = $state<GraphNode[]>([]);
	let candidates = $state<DiscoveryEntry[]>([]);
	let avatars = $state<Record<string, string>>({});
	let docket = $state<Docket | null>(null);
	let loaded = $state(false);
	let err = $state<string | null>(null);

	// Parallelize avatar batches and update progressively so the MindMap can
	// layer avatars in as they arrive — render is never blocked on the full set.
	async function fetchAvatars(handles: string[]): Promise<void> {
		const filtered = handles.filter((h) => h && !h.includes('example'));
		const chunks: string[][] = [];
		for (let i = 0; i < filtered.length; i += 25) {
			chunks.push(filtered.slice(i, i + 25));
		}
		await Promise.allSettled(
			chunks.map(async (chunk) => {
				const params = chunk.map((h) => `actors=${encodeURIComponent(h)}`).join('&');
				try {
					const res = await fetch(
						`https://typeahead.waow.tech/xrpc/app.bsky.actor.getProfiles?${params}`
					);
					if (!res.ok) return;
					const data: { profiles: { handle: string; avatar?: string }[] } = await res.json();
					const updates: Record<string, string> = {};
					for (const p of data.profiles) if (p.avatar) updates[p.handle] = p.avatar;
					if (Object.keys(updates).length > 0) {
						avatars = { ...avatars, ...updates };
					}
				} catch {
					/* skip */
				}
			})
		);
	}

	onMount(() => {
		// Render the MindMap as soon as data trickles in — don't block on the
		// slowest fetch (/api/memory/graph is rate-limited + does PCA backend-side).
		// Each fetch updates its own state slice; the map redraws reactively.
		// Goals + observations are PDS reads, usually fastest; they unblock
		// `loaded` so the user sees the map immediately. Memory graph +
		// discovery + avatars layer in after.
		const graphP = getMemoryGraph()
			.then((r) => {
				known = r.nodes.filter((n) => n.type === 'user') as GraphNode[];
			})
			.catch((e: Error) => {
				err = err ?? e.message;
			});
		const discP = getDiscoveryPool()
			.then((r) => {
				candidates = r;
			})
			.catch(() => {});
		const obsP = getActiveObservations()
			.then((r) => {
				observations = r;
			})
			.catch(() => {});
		const goalsP = getGoals()
			.then((r) => {
				goals = r;
			})
			.catch(() => {});
		getDocket()
			.then((r) => {
				docket = r;
			})
			.catch(() => {});

		// Unblock render once the fast PDS reads land.
		Promise.allSettled([obsP, goalsP]).then(() => {
			loaded = true;
		});

		// Once handle-producing fetches are done, kick avatars off non-blockingly.
		Promise.allSettled([graphP, discP]).then(() => {
			const handles = new Set<string>([PHI_HANDLE]);
			for (const n of known) handles.add(n.label.replace(/^@/, ''));
			for (const c of candidates) handles.add(c.handle);
			fetchAvatars([...handles]);
		});
	});
</script>

<svelte:head>
	<title>phi · mind</title>
</svelte:head>

<div class="lens">
	{#if !loaded}
		<div class="overlay chrome muted">acquiring map…</div>
	{:else if err}
		<div class="overlay chrome muted">connection lost · {err}</div>
	{:else}
		<MindMap {goals} {observations} {known} {candidates} {avatars} />
	{/if}

	{#if docket && docket.candidates.length > 0}
		<a class="docket-pointer chrome" href="/docket" aria-label="open docket">
			<span class="dp-label">docket</span>
			<span class="dp-count">{docket.candidates.length}</span>
			<span class="dp-caption">candidates today →</span>
		</a>
	{/if}

	<!-- Bottom-of-map orientation key -->
	<div class="key chrome">
		<span class="kii"><span class="hex" style="color: var(--hud-hot)"></span>self</span>
		<span class="sep"></span>
		<span class="kii"><span class="hex" style="color: var(--warn)"></span>anchor</span>
		<span class="kii"><span class="hex" style="color: var(--scan-mid)"></span>attention</span>
		<span class="kii"><span class="dot solid"></span>known</span>
		<span class="kii"><span class="dot dashed"></span>horizon</span>
	</div>
</div>

<Logbook />

<style>
	.lens {
		position: absolute;
		inset: 0;
	}

	.overlay {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 11px;
		color: var(--text-mid);
		letter-spacing: 0.18em;
	}

	.key {
		position: absolute;
		bottom: 60px;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		gap: 14px;
		font-size: 10px;
		color: var(--text-dim);
		background: var(--bg-panel);
		border: 1px solid var(--line-mid);
		backdrop-filter: blur(8px);
		-webkit-backdrop-filter: blur(8px);
		padding: 7px 14px;
		pointer-events: none;
		clip-path: polygon(
			6px 0,
			100% 0,
			100% calc(100% - 6px),
			calc(100% - 6px) 100%,
			0 100%,
			0 6px
		);
	}

	.kii {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.sep {
		width: 1px;
		height: 10px;
		background: var(--line-mid);
	}

	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		display: inline-block;
	}

	.dot.solid {
		background: var(--text);
	}

	.dot.dashed {
		background: transparent;
		border: 1px dashed var(--text-dim);
	}

	/* Floating docket pointer — bottom-center-ish, well below the HUD chrome.
	 * Visible from the mind page so the docket is discoverable without
	 * needing the lens cycler. Tap-friendly target on mobile. */
	.docket-pointer {
		position: absolute;
		bottom: 110px;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 16px;
		font-family: var(--font-chrome);
		font-size: 11px;
		letter-spacing: 0.18em;
		text-transform: uppercase;
		color: var(--hud-hot);
		text-decoration: none;
		background: var(--bg-panel);
		border: 1px solid var(--hud-mid);
		backdrop-filter: blur(8px);
		-webkit-backdrop-filter: blur(8px);
		clip-path: polygon(
			8px 0,
			100% 0,
			100% calc(100% - 8px),
			calc(100% - 8px) 100%,
			0 100%,
			0 8px
		);
		transition: color 0.12s, border-color 0.12s, background 0.12s;
	}
	.docket-pointer:hover {
		color: var(--hud-hot);
		border-color: var(--hud-hot);
		background: rgba(184, 107, 58, 0.12);
	}
	.dp-label {
		color: var(--scan-hot);
	}
	.dp-count {
		font-family: var(--font-mono);
		font-size: 14px;
		color: var(--hud-hot);
		letter-spacing: 0;
	}
	.dp-caption {
		color: var(--text-dim);
		font-size: 10px;
	}

	@media (max-width: 640px) {
		.key {
			bottom: 44px;
			gap: 8px;
			font-size: 9px;
			padding: 5px 10px;
			max-width: calc(100vw - 16px);
			flex-wrap: wrap;
			justify-content: center;
		}
		.docket-pointer {
			bottom: 80px;
			padding: 8px 12px;
			font-size: 10px;
			min-height: 36px;
		}
		.dp-caption {
			display: none;
		}
	}
</style>
