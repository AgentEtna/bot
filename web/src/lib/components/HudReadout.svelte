<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { hudReadout } from '$lib/state.svelte';
	import { operatorClock } from '$lib/time';

	const readout = $derived(hudReadout.value || '');

	// Live operator clock — ticks every 30s. When hudReadout has a value
	// (something on the canvas is hovered), the readout takes over the slot;
	// otherwise the clock is the resting state.
	let clock = $state(operatorClock());
	let timer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		clock = operatorClock();
		timer = setInterval(() => {
			clock = operatorClock();
		}, 30_000);
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
	});
</script>

<div class="readout chrome" class:has={!!readout}>
	<span class="hex" aria-hidden="true"></span>
	<span class="t mono" title={readout ? '' : "phi's local time (operator timezone)"}>
		{readout || clock}
	</span>
</div>

<style>
	.readout {
		display: flex;
		gap: 8px;
		align-items: center;
		font-size: 10px;
		color: var(--text-dim);
	}

	.hex {
		display: inline-block;
		width: 6px;
		height: 7px;
		background: var(--text-dim);
		clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%);
		transition: background 0.12s;
		flex-shrink: 0;
	}

	.has .hex {
		background: var(--hud-hot);
	}

	.t {
		font-size: 10px;
		letter-spacing: 0.1em;
		text-transform: none;
	}

	.has .t {
		color: var(--hud-hot);
		font-family: var(--font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.18em;
	}

	@media (max-width: 640px) {
		.readout {
			justify-content: flex-start;
			min-height: 20px;
			padding: 3px 0;
			overflow: hidden;
		}

		.t {
			display: block;
			min-width: 0;
			max-width: 100%;
			overflow: hidden;
			text-overflow: ellipsis;
			white-space: nowrap;
			font-size: 9px;
		}
	}
</style>
