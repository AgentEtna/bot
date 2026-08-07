<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { getHealth, getPhiBio, PHI_HANDLE } from '$lib/api';
	import type { HealthInfo } from '$lib/types';

	let health = $state<HealthInfo | null>(null);
	let bio = $state<string | null>(null);
	let healthTimer: ReturnType<typeof setInterval> | null = null;
	let bioTimer: ReturnType<typeof setInterval> | null = null;

	async function pollHealth() {
		try {
			health = await getHealth();
		} catch {
			health = null;
		}
	}

	async function pollBio() {
		bio = await getPhiBio();
	}

	onMount(() => {
		pollHealth();
		pollBio();
		healthTimer = setInterval(pollHealth, 15_000);
		// Bio changes only at phi's startup (rare). 5 min refresh is enough.
		bioTimer = setInterval(pollBio, 5 * 60_000);
	});

	onDestroy(() => {
		if (healthTimer) clearInterval(healthTimer);
		if (bioTimer) clearInterval(bioTimer);
	});

	const status = $derived.by(() => {
		if (!health) return { color: 'var(--danger)', label: 'offline', pulse: false };
		if (health.paused) return { color: 'var(--warn)', label: 'paused', pulse: true };
		if (health.polling_active) return { color: 'var(--hud-hot)', label: 'online', pulse: true };
		return { color: 'var(--text-dim)', label: 'idle', pulse: false };
	});

</script>

<div class="ident">
	<div class="glyph-wrap" style="color: {status.color}" class:pulse={status.pulse}>
		<svg
			class="logo"
			viewBox="0 0 32 32"
			aria-hidden="true"
			xmlns="http://www.w3.org/2000/svg"
		>
			<!-- outer hex frame -->
			<polygon
				points="16,3 27,9 27,23 16,29 5,23 5,9"
				fill="none"
				stroke="currentColor"
				stroke-width="1.6"
				stroke-linejoin="round"
			/>
			<!-- phi sigil — vertical stem + circle, classic lowercase φ -->
			<line
				x1="16"
				y1="7.5"
				x2="16"
				y2="24.5"
				stroke="currentColor"
				stroke-width="1.6"
				stroke-linecap="round"
			/>
			<ellipse
				cx="16"
				cy="16"
				rx="4.6"
				ry="5.6"
				fill="none"
				stroke="currentColor"
				stroke-width="1.6"
			/>
		</svg>
	</div>
	<div class="meta">
		<div class="name chrome">phi</div>
		<div class="line">
			<span class="hex" style="color: {status.color}" class:pulse={status.pulse}></span>
			<span class="state chrome muted">{status.label}</span>
			<span class="sep">·</span>
			<a href="https://bsky.app/profile/{PHI_HANDLE}" target="_blank" rel="noopener" class="handle"
				>@{PHI_HANDLE}</a
			>
		</div>
		{#if bio}
			<div class="bio" title={bio}>{bio}</div>
		{/if}
	</div>
</div>

<style>
	.ident {
		display: flex;
		gap: 12px;
		align-items: center;
	}

	.glyph-wrap {
		width: 32px;
		height: 32px;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}

	.logo {
		width: 100%;
		height: 100%;
		display: block;
		filter: drop-shadow(0 0 0 currentColor);
		transition: filter 0.4s ease-out;
	}

	/* opacity-only: animating `filter` kept the compositor repainting this
	   layer every frame for as long as phi was online — i.e. always. */
	.glyph-wrap.pulse .logo {
		filter: drop-shadow(0 0 3px currentColor);
		animation: logo-pulse 2.4s ease-in-out infinite;
	}

	@keyframes logo-pulse {
		0%,
		100% {
			opacity: 0.85;
		}
		50% {
			opacity: 1;
		}
	}

	@media (max-width: 760px) {
		.ident {
			gap: 10px;
			align-items: flex-start;
		}
		.glyph-wrap {
			width: 28px;
			height: 28px;
			margin-top: 2px;
		}
		.name {
			font-size: 16px;
		}
		.handle {
			display: none;
		}
		.sep {
			display: none;
		}
	}

	.meta {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.name {
		font-size: 14px;
		color: var(--hud-hot);
		letter-spacing: 0.18em;
	}

	.line {
		display: flex;
		gap: 6px;
		align-items: center;
		font-size: 10px;
	}


	.state {
		font-size: 9px;
	}

	.sep {
		color: var(--text-dim);
	}

	.handle {
		font-family: var(--font-mono);
		font-size: 10px;
		color: var(--scan-mid);
	}

	.bio {
		font-size: 11px;
		color: var(--text-mid);
		font-style: italic;
		line-height: 1.35;
		max-width: 280px;
		margin-top: 2px;
		cursor: help;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	@media (max-width: 760px) {
		.bio {
			max-width: calc(100vw - 70px);
			font-size: 11px;
			line-height: 1.25;
		}
	}
</style>
