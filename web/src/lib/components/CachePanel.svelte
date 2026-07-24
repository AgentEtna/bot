<script lang="ts">
	// prompt-cache readout. each run is one stacked bar of its input tokens:
	// read from cache (free-ish) / written to cache (premium) / uncached
	// (full price). the shape of the stack IS the diagnosis — a healthy run
	// is mostly cyan with a thin orange head; an all-grey bar means the
	// cacheable prefix moved and phi paid for her whole context again.
	import { onMount } from 'svelte';
	import { getCacheStability } from '$lib/api';
	import type { CacheRun, CacheStability } from '$lib/types';
	import { relativeWhen, whenTooltip } from '$lib/time';

	let data = $state<CacheStability | null>(null);
	let loaded = $state(false);
	let expanded = $state<string | null>(null);

	onMount(async () => {
		data = await getCacheStability();
		loaded = true;
	});

	function pct(n: number): string {
		return `${Math.round(n * 100)}%`;
	}

	function tokens(n: number): string {
		if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`;
		return String(n);
	}

	function total(run: CacheRun): number {
		return run.cache_read + run.cache_write + run.uncached;
	}

	function share(part: number, run: CacheRun): number {
		const t = total(run);
		return t ? (part / t) * 100 : 0;
	}

	const runKey = (run: CacheRun) => `${run.started_at}:${run.label}`;
</script>

<section class="cache">
	<h2>prompt cache</h2>
	<p class="explainer">
		phi caches her tool definitions and static instructions for an hour, and her message history
		for five minutes. this is the provider's own verdict on whether that held — read back from
		cache, written to cache at a premium, or paid for in full.
	</p>

	{#if !loaded}
		<div class="status">loading…</div>
	{:else if !data || !data.runs.length}
		<div class="status">no runs recorded yet</div>
	{:else}
		<div class="headline">
			<div class="stat">
				<span class="stat-value">{pct(data.hit_rate)}</span>
				<span class="stat-label">read from cache</span>
			</div>
			<div class="stat">
				<span class="stat-value">{data.carried_in}<span class="of">/{data.window_runs}</span></span>
				<span class="stat-label">runs carried in</span>
			</div>
			<div class="stat {data.collapses ? 'stat-bad' : ''}">
				<span class="stat-value">{data.collapses}</span>
				<span class="stat-label">collapses</span>
			</div>
		</div>

		<div class="legend">
			<span><i class="sw sw-read"></i>read</span>
			<span><i class="sw sw-write"></i>written</span>
			<span><i class="sw sw-cold"></i>uncached</span>
		</div>

		<ul class="runs">
			{#each data.runs as run (runKey(run))}
				<li class="run">
					<button
						class="run-head"
						onclick={() => (expanded = expanded === runKey(run) ? null : runKey(run))}
					>
						<span class="carry" class:carried={run.carried_in} title={run.carried_in
							? 'first request read back a prefix from an earlier run — the 1h cache bridged them'
							: 'cold start: no prefix carried in from an earlier run'}>
							{run.carried_in ? '⇥' : '·'}
						</span>
						<span class="label">{run.label}</span>
						<span class="when" title={whenTooltip(run.started_at)}>{relativeWhen(run.started_at)}</span>
						<span class="reqs">{run.requests} req</span>
						<span class="rate" class:bad={run.collapses > 0}>{pct(run.hit_rate)}</span>
					</button>

					<div class="bar" title="{tokens(total(run))} input tokens">
						<span class="seg seg-read" style="width:{share(run.cache_read, run)}%"></span>
						<span class="seg seg-write" style="width:{share(run.cache_write, run)}%"></span>
						<span class="seg seg-cold" style="width:{share(run.uncached, run)}%"></span>
					</div>

					{#if run.collapses}
						<div class="collapse-note">
							{run.collapses} collapse{run.collapses > 1 ? 's' : ''} — the cacheable prefix moved
							mid-run, or the provider cache expired under it
						</div>
					{/if}

					{#if expanded === runKey(run)}
						<table class="samples">
							<thead>
								<tr>
									<th>#</th><th>read</th><th>written</th><th>uncached</th><th>gap</th><th></th>
								</tr>
							</thead>
							<tbody>
								{#each run.samples as s, i (s.at + i)}
									<tr class:collapsed={s.collapsed}>
										<td>{i + 1}</td>
										<td>{tokens(s.cache_read)}</td>
										<td>{tokens(s.cache_write)}</td>
										<td>{tokens(s.input_tokens)}</td>
										<td>{s.gap_seconds === null ? '—' : `${Math.round(s.gap_seconds)}s`}</td>
										<td class="verdict">
											{#if s.collapsed}{s.maybe_expiry ? 'collapse (maybe expiry)' : 'collapse'}{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.cache {
		margin-top: 3rem;
		border-top: 1px solid var(--line-dim);
		padding-top: 1.5rem;
	}
	h2 {
		font-family: var(--font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-weight: 500;
		font-size: 1.1rem;
		margin: 0 0 0.5rem;
		color: var(--hud-hot);
	}
	.explainer {
		color: var(--text-dim);
		max-width: 60ch;
		line-height: 1.5;
	}
	.status {
		margin-top: 1rem;
		color: var(--text-dim);
		font-family: var(--font-mono);
	}

	.headline {
		display: flex;
		gap: 2.5rem;
		margin: 1.25rem 0 1rem;
	}
	.stat {
		display: flex;
		flex-direction: column;
	}
	.stat-value {
		font-family: var(--font-chrome);
		font-size: 1.9rem;
		line-height: 1;
		color: var(--scan-hot);
	}
	.stat-value .of {
		color: var(--text-dim);
		font-size: 0.6em;
	}
	.stat-bad .stat-value {
		color: var(--warn-hot);
	}
	.stat-label {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--text-dim);
		margin-top: 0.3rem;
	}

	.legend {
		display: flex;
		gap: 1rem;
		font-size: 0.75rem;
		color: var(--text-dim);
		margin-bottom: 0.75rem;
	}
	.legend span {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}
	.sw {
		width: 10px;
		height: 10px;
		display: inline-block;
	}
	.sw-read,
	.seg-read {
		background: var(--scan-mid);
	}
	.sw-write,
	.seg-write {
		background: var(--hud-mid);
	}
	.sw-cold,
	.seg-cold {
		background: var(--text-dim);
	}

	.runs {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.run {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--grid);
	}
	.run-head {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		width: 100%;
		background: none;
		border: none;
		padding: 0 0 0.35rem;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.run-head:hover .label {
		color: var(--hud-hot);
	}
	.carry {
		font-family: var(--font-mono);
		color: var(--text-dim);
		width: 1ch;
	}
	.carry.carried {
		color: var(--scan-hot);
	}
	.label {
		flex: 1;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.when,
	.reqs {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--text-dim);
	}
	.rate {
		font-family: var(--font-mono);
		font-size: 0.8rem;
		color: var(--scan-hot);
		min-width: 4ch;
		text-align: right;
	}
	.rate.bad {
		color: var(--warn-hot);
	}

	.bar {
		display: flex;
		height: 8px;
		background: var(--bg-elev);
		overflow: hidden;
	}
	.seg {
		height: 100%;
	}

	.collapse-note {
		font-size: 0.75rem;
		color: var(--warn);
		margin-top: 0.35rem;
	}

	.samples {
		width: 100%;
		margin-top: 0.6rem;
		border-collapse: collapse;
		font-family: var(--font-mono);
		font-size: 0.72rem;
		color: var(--text-mid);
	}
	.samples th {
		text-align: right;
		font-weight: 400;
		color: var(--text-dim);
		border-bottom: 1px solid var(--grid);
		padding: 0.15rem 0.4rem;
	}
	.samples td {
		text-align: right;
		padding: 0.15rem 0.4rem;
	}
	.samples tr.collapsed td {
		color: var(--warn);
	}
	.verdict {
		text-align: left;
		white-space: nowrap;
	}

	@media (max-width: 520px) {
		.headline {
			gap: 1.5rem;
		}
		.when {
			display: none;
		}
	}
</style>
