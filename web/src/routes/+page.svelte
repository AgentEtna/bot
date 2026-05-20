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
		getAtlas
	} from '$lib/api';
	import type { GraphNode, DiscoveryEntry, Observation, Goal, Docket, Atlas } from '$lib/types';

	let goals = $state<Goal[]>([]);
	let observations = $state<Observation[]>([]);
	let known = $state<GraphNode[]>([]);
	let candidates = $state<DiscoveryEntry[]>([]);
	let docket = $state<Docket | null>(null);
	let atlas = $state<Atlas | null>(null);
	let loaded = $state(false);
	let err = $state<string | null>(null);

	onMount(() => {
		// Render the MindMap as soon as data trickles in — don't block on the
		// slowest fetch (/api/memory/graph is rate-limited + does PCA backend-side).
		// Each fetch updates its own state slice; the map redraws reactively.
		// Goals + observations are PDS reads, usually fastest; they unblock
		// `loaded` so the user sees the map immediately. Memory graph,
		// discovery, atlas, and docket layer in after.
		getMemoryGraph()
			.then((r) => {
				known = r.nodes.filter((n) => n.type === 'user') as GraphNode[];
			})
			.catch((e: Error) => {
				err = err ?? e.message;
			});
		getDiscoveryPool()
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
		<MindMap {goals} {observations} {known} {candidates} {docket} {atlas} />
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
