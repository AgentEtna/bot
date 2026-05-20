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
		getAtlas,
		PHI_HANDLE
	} from '$lib/api';
	import type { GraphNode, DiscoveryEntry, Observation, Goal, Docket, Atlas } from '$lib/types';

	let goals = $state<Goal[]>([]);
	let observations = $state<Observation[]>([]);
	let known = $state<GraphNode[]>([]);
	let candidates = $state<DiscoveryEntry[]>([]);
	let avatars = $state<Record<string, string>>({});
	let docket = $state<Docket | null>(null);
	let atlas = $state<Atlas | null>(null);
	let loaded = $state(false);
	let err = $state<string | null>(null);

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
					if (Object.keys(updates).length > 0) avatars = { ...avatars, ...updates };
				} catch {
					/* best-effort public avatars */
				}
			})
		);
	}

	onMount(() => {
		// Render the MindMap as soon as data trickles in — don't block on the
		// slowest fetch (/api/memory/graph is rate-limited + does PCA backend-side).
		// Each fetch updates its own state slice; the map redraws reactively.
		// Goals + observations are PDS reads, usually fastest; they unblock
		// `loaded` so the user sees the map immediately. Memory graph,
		// discovery, atlas, and docket layer in after.
		const graphP = getMemoryGraph()
			.then((r) => {
				known = r.nodes.filter((n) => n.type === 'user') as GraphNode[];
			})
			.catch(() => {
				known = [];
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
		getAtlas()
			.then((r) => {
				atlas = r;
			})
			.catch(() => {});

		// Unblock render once the fast PDS reads land.
		Promise.allSettled([obsP, goalsP]).then(() => {
			loaded = true;
		});

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
		<MindMap {goals} {observations} {known} {candidates} {avatars} {docket} {atlas} />
	{/if}
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
</style>
