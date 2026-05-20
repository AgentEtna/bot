<script lang="ts">
	interface Lens {
		readonly key: string;
		readonly href: string;
		readonly label: string;
	}

	interface Props {
		current: string;
		LENSES: readonly Lens[];
	}

	let { current, LENSES }: Props = $props();
</script>

<div class="cycler">
	<span class="hint chrome faint">lens</span>
	<div class="row">
		{#each LENSES as lens, i (lens.key)}
			<a href={lens.href} class="opt chrome" class:active={current === lens.key}>
				<span class="num">{i + 1}</span>
				<span class="lbl">{lens.label}</span>
			</a>
		{/each}
	</div>
</div>

<style>
	.cycler {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 4px;
	}

	.hint {
		font-size: 9px;
		color: var(--text-dim);
	}

	.row {
		display: flex;
		gap: 1px;
		border: 1px solid var(--line-mid);
		background: var(--bg-panel);
		backdrop-filter: blur(8px);
		-webkit-backdrop-filter: blur(8px);
		clip-path: polygon(
			6px 0,
			100% 0,
			100% calc(100% - 6px),
			calc(100% - 6px) 100%,
			0 100%,
			0 6px
		);
	}

	.opt {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 10px;
		font-size: 11px;
		color: var(--text-mid);
		text-decoration: none;
		transition:
			color 0.12s,
			background 0.12s;
	}

	.opt:hover {
		color: var(--hud-hot);
		background: rgba(184, 107, 58, 0.05);
	}

	.opt.active {
		color: var(--hud-hot);
		background: rgba(184, 107, 58, 0.1);
	}

	.num {
		font-family: var(--font-mono);
		font-size: 9px;
		color: var(--text-dim);
	}

	.opt.active .num {
		color: var(--hud-mid);
	}

	@media (max-width: 640px) {
		.cycler {
			width: 100%;
			align-items: stretch;
		}
		.hint {
			display: none;
		}
		.row {
			width: 100%;
		}
		.opt {
			flex: 1;
			justify-content: center;
			padding: 8px 10px;
			font-size: 12px;
			min-height: 36px;
		}
		.num {
			display: none;
		}
	}
</style>
