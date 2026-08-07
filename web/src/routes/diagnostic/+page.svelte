<script lang="ts">
	import { onMount } from 'svelte';
	import { getContextPreview } from '$lib/api';
	import type { ContextPreview } from '$lib/types';

	let preview = $state<ContextPreview | null>(null);
	let loading = $state(true);
	let err = $state<string | null>(null);
	let selectedIdx = $state(0);

	// blocks that rendered nothing are real information (the path renders
	// them empty), but they shouldn't crowd the list — group them at the end
	const rendered = $derived(preview?.blocks.filter((b) => b.chars > 0 || b.error) ?? []);
	const silent = $derived(preview?.blocks.filter((b) => b.chars === 0 && !b.error) ?? []);
	const rows = $derived([...rendered, ...silent]);
	const selected = $derived(rows[selectedIdx] ?? null);

	async function load() {
		loading = true;
		err = null;
		const p = await getContextPreview();
		if (p) {
			preview = p;
			selectedIdx = 0;
		} else {
			err = 'preview unavailable';
		}
		loading = false;
	}

	onMount(load);

	function pad(i: number): string {
		return String(i).padStart(2, '0');
	}

	function kb(chars: number): string {
		return chars >= 1000 ? `${(chars / 1000).toFixed(1)}k` : String(chars);
	}
</script>

<svelte:head>
	<title>phi · diagnostic</title>
</svelte:head>

<div class="lens">
	<div class="frame-wrap">
		<header class="head">
			<div class="head-rule">
				<span class="head-tag chrome">phi · diagnostic</span>
				<span class="head-meta chrome faint">
					{#if preview}
						next-run context · {preview.path} · {kb(preview.total_chars)} chars ·
						rendered {new Date(preview.generated_at).toLocaleTimeString()}
					{:else}
						if phi woke up right now, what would she read?
					{/if}
				</span>
				<button class="refresh chrome" onclick={load} disabled={loading}>
					{loading ? 'rendering…' : 'refresh'}
				</button>
			</div>
		</header>

		<div class="panes">
			<aside class="list-pane">
				{#if loading}
					<div class="empty chrome muted">composing the prompt…</div>
				{:else if err}
					<div class="empty chrome muted">unreachable · {err}</div>
				{:else}
					<ul class="list scroll" role="listbox" aria-label="context blocks">
						{#each rows as b, i (b.name)}
							{#if i === rendered.length && silent.length > 0}
								<li class="section">
									<span class="section-tag chrome">silent on this path</span>
								</li>
							{/if}
							<li>
								<button
									class="row"
									class:active={i === selectedIdx}
									role="option"
									aria-selected={i === selectedIdx}
									onclick={() => (selectedIdx = i)}
								>
									<span class="idx mono">{pad(i)}</span>
									<span class="name mono">{b.name}</span>
									{#if b.error}
										<span class="err-dot" title={b.error}></span>
									{/if}
									<span class="size mono faint">{b.chars > 0 ? kb(b.chars) : '·'}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</aside>

			<section class="detail-pane">
				<div class="pane-rule chrome">as phi would read it</div>
				{#if selected}
					<div class="detail scroll">
						<div class="d-head">
							<div class="d-name mono">{selected.name}</div>
							<div class="d-meta chrome">
								<span class="num mono">{selected.chars}</span>
								<span class="dim">chars</span>
								<span class="seg"></span>
								<span class="num mono">{selected.ms}</span>
								<span class="dim">ms</span>
							</div>
						</div>
						<div class="d-rule"></div>
						{#if selected.error}
							<div class="block-error mono">{selected.error}</div>
						{:else if selected.chars === 0}
							<div class="d-body muted">
								renders nothing on this path — batch-seeded blocks (notifications,
								per-author memory, episodic, prior coverage) need material to react to.
							</div>
						{:else}
							<pre class="block-text mono">{selected.text}</pre>
						{/if}
					</div>
				{:else if !loading}
					<div class="empty chrome muted">no block selected</div>
				{/if}
			</section>
		</div>
	</div>
</div>

<style>
	.lens {
		position: absolute;
		inset: 0;
		display: flex;
		justify-content: center;
		padding: 76px 28px 56px;
		overflow: hidden;
	}

	.frame-wrap {
		position: relative;
		width: 100%;
		max-width: 1100px;
		height: 100%;
		display: grid;
		grid-template-rows: auto 1fr;
		gap: 14px;
	}

	.head-rule {
		display: flex;
		align-items: baseline;
		gap: 12px;
		border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.25));
		padding-bottom: 8px;
	}

	.head-meta {
		flex: 1;
		font-size: 0.72rem;
	}

	.refresh {
		background: none;
		border: 1px solid var(--line, rgba(128, 128, 128, 0.35));
		color: inherit;
		font-size: 0.7rem;
		padding: 2px 10px;
		cursor: pointer;
	}
	.refresh:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.panes {
		display: grid;
		grid-template-columns: 280px 1fr;
		gap: 16px;
		min-height: 0;
	}

	.list-pane,
	.detail-pane {
		display: flex;
		flex-direction: column;
		min-height: 0;
	}

	.pane-rule {
		font-size: 0.68rem;
		padding-bottom: 6px;
	}

	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-y: auto;
	}

	.section {
		padding: 10px 4px 4px;
	}
	.section-tag {
		font-size: 0.65rem;
		opacity: 0.6;
	}

	.row {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		background: none;
		border: none;
		color: inherit;
		padding: 5px 6px;
		cursor: pointer;
		text-align: left;
		font-size: 0.78rem;
	}
	.row:hover {
		background: rgba(128, 128, 128, 0.08);
	}
	.row.active {
		background: rgba(128, 128, 128, 0.14);
	}

	.idx {
		opacity: 0.45;
		font-size: 0.68rem;
	}
	.name {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.size {
		font-size: 0.68rem;
	}

	.err-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: #d3574e;
		flex-shrink: 0;
	}

	.detail {
		overflow-y: auto;
		min-height: 0;
	}

	.d-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
	}
	.d-name {
		font-size: 0.85rem;
	}
	.d-meta {
		font-size: 0.7rem;
	}
	.d-meta .seg {
		display: inline-block;
		width: 10px;
	}
	.d-rule {
		border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.25));
		margin: 8px 0 12px;
	}

	.block-text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: 0.76rem;
		line-height: 1.55;
		margin: 0;
	}

	.block-error {
		color: #d3574e;
		font-size: 0.78rem;
	}

	.empty {
		padding: 24px 8px;
		font-size: 0.75rem;
	}
</style>
