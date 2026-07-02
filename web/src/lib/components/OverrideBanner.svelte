<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchOverride, type Override } from '$lib/operator/override';

	let override = $state<Override | null>(null);

	onMount(async () => {
		override = await fetchOverride();
		// keep it roughly as fresh as the bot's own 60s TTL
		setInterval(async () => {
			override = await fetchOverride();
		}, 60_000);
	});
</script>

{#if override?.active}
	<a class="override-banner" href="/operator" title="operator override — click to manage">
		<span class="dot"></span>
		operator override active — phi's outward actions are held. {override.message}
	</a>
{/if}

<style>
	.override-banner {
		display: block;
		padding: 0.5rem 1rem;
		background: color-mix(in srgb, #e0a458 18%, transparent);
		border-bottom: 1px solid #e0a458;
		color: inherit;
		text-decoration: none;
		font-size: 0.9em;
		line-height: 1.4;
	}
	.dot {
		display: inline-block;
		width: 0.55em;
		height: 0.55em;
		border-radius: 50%;
		background: #e0a458;
		margin-right: 0.5em;
		animation: pulse 2s ease-in-out infinite;
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
</style>
