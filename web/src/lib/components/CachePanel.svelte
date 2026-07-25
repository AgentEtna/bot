<script lang="ts">
	// prompt cache instrument.
	//
	// the question this answers is "is the caching strategy in agent.py worth
	// it", so the headline is money, not tokens: what the input bill would
	// have been with caching off, against what phi was actually billed.
	// every label here names a thing that happened — no jargon that needs a
	// glossary, and no facts restated in prose (the TTLs and prices come
	// from the API, which reads the same CACHE_TTLS dict the agent
	// configures from).
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

	const pct = (n: number) => `${Math.round(n * 100)}%`;
	const num = (n: number) => n.toLocaleString('en-US');

	function tokens(n: number): string {
		if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`;
		return String(n);
	}

	const total = (r: CacheRun) => r.cache_read + r.cache_write + r.uncached;
	const share = (part: number, r: CacheRun) => (total(r) ? (part / total(r)) * 100 : 0);

	// each segment says what it is, how much of the run it was, and what it
	// cost — the thing you actually want when you hover a colored bar
	function segTitle(kind: 'reused' | 'stored' | 'full', part: number, r: CacheRun): string {
		if (!data) return '';
		const rate =
			kind === 'reused' ? data.prices.read : kind === 'stored' ? data.prices.write : 1;
		const what =
			kind === 'reused'
				? 'read back from cache'
				: kind === 'stored'
					? 'written into the cache'
					: 'sent uncached';
		return `${num(part)} tokens ${what} — ${Math.round(share(part, r))}% of this run, billed at ${rate}× (${num(Math.round(part * rate))} tokens' worth)`;
	}

	const runKey = (r: CacheRun) => `${r.started_at}:${r.label}`;
</script>

<section class="cache">
	<h2>prompt cache</h2>

	{#if !loaded}
		<div class="status">loading…</div>
	{:else if !data || !data.runs.length}
		<div class="status">no runs recorded yet</div>
	{:else}
		<p class="strategy">
			caching what phi re-sends every request:
			{#each Object.entries(data.strategy) as [what, ttl], i (what)}<span class="ttl"
					>{what.replace('_', ' ')} <b>{ttl}</b></span
				>{i < Object.entries(data.strategy).length - 1 ? ' · ' : ''}{/each}
		</p>

		<div class="headline">
			<div class="verdict">
				<span class="big">{pct(data.saved)}</span>
				<span class="big-label">off the input bill</span>
				<span class="sub">
					{tokens(data.uncached_cost_tokens)} tokens of context, billed as
					{tokens(data.billed_tokens)} — across {data.window_runs} run{data.window_runs === 1
						? ''
						: 's'}
				</span>
			</div>
		</div>

		<div class="facts">
			<div class="fact">
				<span class="fact-n">{data.warm_starts}<span class="of">/{data.window_runs}</span></span>
				<span class="fact-t"
					>runs began with a cache already warm — they reused the tool definitions and
					instructions a previous run left behind, instead of paying to store them again</span
				>
			</div>
			<div class="fact {data.collapses ? 'fact-bad' : ''}">
				<span class="fact-n">{data.collapses}</span>
				<span class="fact-t"
					>requests lost the cache mid-run — something changed the start of the prompt, so the
					provider had to re-read the whole thing. zero is the healthy number</span
				>
			</div>
		</div>

		<div class="legend">
			<span title="billed at {data.prices.read}× the base input rate"
				><i class="sw sw-read"></i>reused · {data.prices.read}×</span
			>
			<span title="billed at {data.prices.write}× the base input rate"
				><i class="sw sw-write"></i>stored · {data.prices.write}×</span
			>
			<span title="billed at the full base input rate"
				><i class="sw sw-cold"></i>full price · 1×</span
			>
		</div>

		<ul class="runs">
			{#each data.runs as run (runKey(run))}
				<li class="run">
					<div class="run-head">
						<button
							class="opener"
							onclick={() => (expanded = expanded === runKey(run) ? null : runKey(run))}
							title="show each model request in this run"
						>
							<span class="start" class:warm={run.warm_start}>
								{run.warm_start ? 'warm' : 'cold'}
							</span>
							<span class="label">{run.label}</span>
						</button>
						<span class="when" title={whenTooltip(run.started_at)}
							>{relativeWhen(run.started_at)}</span
						>
						<span class="reqs">{run.requests} req · {tokens(total(run))}</span>
						<span class="saved" class:bad={run.saved < 0.2}>{pct(run.saved)} off</span>
						{#if run.trace_url}
							<a
								class="trace"
								href={run.trace_url}
								target="_blank"
								rel="noopener"
								title="open this run's trace in logfire — every tool call it made">trace&nbsp;↗</a
							>
						{/if}
					</div>

					<div class="bar">
						<span
							class="seg seg-read"
							style="width:{share(run.cache_read, run)}%"
							title={segTitle('reused', run.cache_read, run)}
						></span>
						<span
							class="seg seg-write"
							style="width:{share(run.cache_write, run)}%"
							title={segTitle('stored', run.cache_write, run)}
						></span>
						<span
							class="seg seg-cold"
							style="width:{share(run.uncached, run)}%"
							title={segTitle('full', run.uncached, run)}
						></span>
					</div>

					{#if run.collapses}
						<div class="collapse-note">
							lost the cache {run.collapses}
							{run.collapses > 1 ? 'times' : 'time'} mid-run — the start of the prompt changed, or
							the provider's copy expired underneath it
						</div>
					{/if}

					{#if expanded === runKey(run)}
						<table class="samples">
							<thead>
								<tr>
									<th>request</th>
									<th>reused</th>
									<th>stored</th>
									<th>full price</th>
									<th>since last</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each run.samples as s, i (s.at + i)}
									<tr class:collapsed={s.collapsed}>
										<td>{i + 1}</td>
										<td>{tokens(s.cache_read)}</td>
										<td>{tokens(s.cache_write)}</td>
										<td>{tokens(s.input_tokens)}</td>
										<td>{s.gap_seconds === null ? 'first' : `${Math.round(s.gap_seconds)}s`}</td>
										<td class="verdict-cell">
											{#if s.collapsed}
												{s.maybe_expiry ? 'lost the cache (probably expired)' : 'lost the cache'}
											{/if}
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
		margin: 0 0 0.75rem;
		color: var(--hud-hot);
	}
	.status {
		color: var(--text-dim);
		font-family: var(--font-mono);
	}

	.strategy {
		color: var(--text-dim);
		font-size: 0.85rem;
		margin: 0 0 1.25rem;
	}
	.ttl b {
		font-family: var(--font-mono);
		color: var(--scan-hot);
		font-weight: 400;
	}

	.headline {
		margin-bottom: 1.25rem;
	}
	.big {
		font-family: var(--font-chrome);
		font-size: 2.6rem;
		line-height: 1;
		color: var(--scan-hot);
	}
	.big-label {
		font-family: var(--font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--text);
		margin-left: 0.5rem;
	}
	.sub {
		display: block;
		color: var(--text-dim);
		font-size: 0.8rem;
		margin-top: 0.4rem;
	}

	.facts {
		display: flex;
		gap: 1.5rem;
		margin-bottom: 1.25rem;
	}
	.fact {
		display: flex;
		gap: 0.6rem;
		align-items: baseline;
		flex: 1;
		max-width: 34ch;
	}
	.fact-n {
		font-family: var(--font-chrome);
		font-size: 1.5rem;
		line-height: 1;
		color: var(--scan-hot);
		white-space: nowrap;
	}
	.fact-n .of {
		color: var(--text-dim);
		font-size: 0.6em;
	}
	.fact-bad .fact-n {
		color: var(--warn-hot);
	}
	.fact-t {
		font-size: 0.75rem;
		color: var(--text-dim);
		line-height: 1.35;
	}

	.legend {
		display: flex;
		gap: 1rem;
		font-size: 0.75rem;
		color: var(--text-dim);
		margin-bottom: 0.75rem;
		font-family: var(--font-mono);
	}
	.legend span {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		cursor: help;
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
		gap: 0.5rem;
		padding: 0 2px 0.35rem 0;
	}
	/* the label is the only elastic part — everything to its right is a
	 * fixed readout and must never be pushed off the edge */
	.when,
	.reqs,
	.saved,
	.trace {
		flex-shrink: 0;
	}
	.opener {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		flex: 1;
		min-width: 0;
		background: none;
		border: none;
		padding: 0;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.opener:hover .label {
		color: var(--hud-hot);
	}
	.start {
		font-family: var(--font-mono);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
		border: 1px solid var(--line-dim);
		padding: 0 0.3rem;
	}
	.start.warm {
		color: var(--scan-hot);
		border-color: var(--line-scan);
	}
	.label {
		/* min-width:0 is what lets a flex item shrink below its content
		 * width — without it the row overflows and the trace link clips */
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.when,
	.reqs {
		font-family: var(--font-mono);
		font-size: 0.72rem;
		color: var(--text-dim);
		white-space: nowrap;
	}
	.saved {
		font-family: var(--font-mono);
		font-size: 0.78rem;
		color: var(--scan-hot);
		white-space: nowrap;
	}
	.saved.bad {
		color: var(--warn-hot);
	}
	.trace {
		font-family: var(--font-mono);
		font-size: 0.7rem;
		color: var(--text-dim);
		text-decoration: none;
		white-space: nowrap;
	}
	.trace:hover {
		color: var(--hud-hot);
	}

	.bar {
		display: flex;
		height: 8px;
		background: var(--bg-elev);
		overflow: hidden;
	}
	.seg {
		height: 100%;
		cursor: help;
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
	.verdict-cell {
		text-align: left;
		white-space: nowrap;
	}

	@media (max-width: 640px) {
		.facts {
			flex-direction: column;
			gap: 0.75rem;
		}
		.when {
			display: none;
		}
	}
</style>
