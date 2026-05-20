<script lang="ts">
	import { onMount } from 'svelte';
	import MindMap from '$lib/components/MindMap.svelte';
	import Logbook from '$lib/components/Logbook.svelte';
	import { mindCounts } from '$lib/state.svelte';
	import {
		getMemoryGraph,
		getDiscoveryPool,
		getGoals,
		getDocket,
		getAtlas,
		getActivity,
		PHI_HANDLE
	} from '$lib/api';
	import type { GraphNode, DiscoveryEntry, Goal, Docket, Atlas } from '$lib/types';

	let goals = $state<Goal[]>([]);
	let known = $state<GraphNode[]>([]);
	let candidates = $state<DiscoveryEntry[]>([]);
	let avatars = $state<Record<string, string>>({});
	let docket = $state<Docket | null>(null);
	let atlas = $state<Atlas | null>(null);

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
		// Render the instrument immediately; each source settles into place
		// independently so slow graph/atlas reads never leave a black cockpit.
		let outCount = 0;
		const publishCounts = () => {
			mindCounts.set({
				goals: goals.length,
				out: outCount,
				ppl: known.length,
				cand: docket?.candidates.length ?? candidates.length,
				loaded: true
			});
		};
		publishCounts();
		const graphP = getMemoryGraph()
			.then((r) => {
				known = r.nodes.filter((n) => n.type === 'user') as GraphNode[];
				publishCounts();
				return known;
			})
			.catch(() => {
				known = [];
				publishCounts();
				return [] as GraphNode[];
			});
		const discP = getDiscoveryPool()
			.then((r) => {
				candidates = r;
				publishCounts();
				return r;
			})
			.catch(() => {
				publishCounts();
				return [] as DiscoveryEntry[];
			});
		getActivity()
			.then((r) => {
				outCount = r.length;
				publishCounts();
			})
			.catch(() => {});
		const goalsP = getGoals()
			.then((r) => {
				goals = r;
				publishCounts();
			})
			.catch(() => publishCounts());
		getDocket()
			.then((r) => {
				docket = r;
				publishCounts();
			})
			.catch(() => publishCounts());
		getAtlas()
			.then((r) => {
				atlas = r;
			})
			.catch(() => {});

		void Promise.allSettled([goalsP]);
		Promise.allSettled([graphP, discP]).then(([graphResult, discoveryResult]) => {
			const handles = new Set<string>([PHI_HANDLE]);
			const graphNodes = graphResult.status === 'fulfilled' ? graphResult.value : [];
			const discoveryEntries = discoveryResult.status === 'fulfilled' ? discoveryResult.value : [];
			for (const n of graphNodes) handles.add(n.label.replace(/^@/, ''));
			for (const c of discoveryEntries) handles.add(c.handle);
			fetchAvatars([...handles]);
		});
	});
</script>

<svelte:head>
	<title>phi · mind</title>
</svelte:head>

<div class="lens">
	<MindMap {goals} {known} {candidates} {avatars} {docket} {atlas} />
</div>

<Logbook />

<style>
	.lens {
		position: absolute;
		inset: 0;
	}

</style>
