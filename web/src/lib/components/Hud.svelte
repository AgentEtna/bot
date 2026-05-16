<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import HudIdentity from './HudIdentity.svelte';
	import HudLensCycler from './HudLensCycler.svelte';
	import HudCounts from './HudCounts.svelte';
	import HudReadout from './HudReadout.svelte';

	const LENSES = [
		{ key: 'mind', href: '/', label: 'mind' },
		{ key: 'docket', href: '/docket', label: 'docket' },
		{ key: 'capabilities', href: '/capabilities', label: 'capabilities' }
	] as const;

	const current = $derived.by(() => {
		const path = page.url.pathname;
		if (path === '/') return 'mind';
		if (path.startsWith('/docket')) return 'docket';
		if (path.startsWith('/capabilities')) return 'capabilities';
		return 'mind';
	});

	function cycle(dir: 1 | -1) {
		const idx = LENSES.findIndex((l) => l.key === current);
		const next = (idx + dir + LENSES.length) % LENSES.length;
		goto(LENSES[next].href);
	}

	function handleKey(e: KeyboardEvent) {
		if (e.target instanceof HTMLInputElement) return;
		if (e.target instanceof HTMLTextAreaElement) return;
		if (e.key === '1') goto('/');
		if (e.key === '2') goto('/docket');
		if (e.key === '3') goto('/capabilities');
		if (e.key === 'Tab' && !e.shiftKey) {
			e.preventDefault();
			cycle(1);
		}
	}

	onMount(() => {
		window.addEventListener('keydown', handleKey);
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') window.removeEventListener('keydown', handleKey);
	});
</script>

<div class="hud hud-tl">
	<HudIdentity />
</div>

<div class="hud hud-tr">
	<HudLensCycler {current} {LENSES} />
</div>

<div class="hud hud-bl">
	<HudCounts />
</div>

<div class="hud hud-br">
	<HudReadout />
</div>
