<script lang="ts">
	import { onMount } from 'svelte';
	import { getBskyFeed, getChickenResults, getChickenRound, getChickenTrader } from '$lib/api';
	import type {
		BskyFeedItem,
		ChickenResultRound,
		ChickenRound,
		ChickenTrader
	} from '$lib/types';

	let trader = $state<ChickenTrader | null>(null);
	let round = $state<ChickenRound | null>(null);
	let results = $state<ChickenResultRound[]>([]);
	let commentary = $state<BskyFeedItem[]>([]);
	let loaded = $state(false);
	let err = $state<string | null>(null);

	// sparkline hover
	let hoverIdx = $state<number | null>(null);

	const START_SUBC = 10_000_000; // every wallet starts at $1,000

	onMount(async () => {
		try {
			[trader, round, results] = await Promise.all([
				getChickenTrader(),
				getChickenRound(),
				getChickenResults()
			]);
			// phi narrates her trades on the timeline — surface those posts as the "why"
			const feed = await getBskyFeed(60);
			const handles = new Set((trader?.trades ?? []).map((t) => t.contender_handle));
			commentary = feed
				.filter((f) => {
					const text = (f.post.record?.text ?? '').toLowerCase();
					return (
						text.includes('chicken') || [...handles].some((h) => h && text.includes(h))
					);
				})
				.slice(0, 4);
		} catch (e) {
			err = (e as Error).message;
		}
		loaded = true;
	});

	function usd(subc: number, signed = false): string {
		const v = subc / 10_000;
		const sign = signed && v > 0 ? '+' : '';
		return `${sign}$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
	}
	function cents(subc: number): string {
		return `${(subc / 100).toFixed(0)}¢`;
	}
	function day(ts: number): string {
		return new Date(ts * 1000).toISOString().slice(0, 10);
	}

	const winnersByRound = $derived(new Map(results.map((r) => [r.id, r.winner_did])));

	function tradeOutcome(t: { round_id: string; contender_did: string; side: string }): {
		label: string;
		cls: string;
	} {
		const winner = winnersByRound.get(t.round_id);
		if (!winner) return { label: 'open', cls: 'open' };
		if (t.side === 'sell') return { label: 'exited', cls: 'open' };
		return winner === t.contender_did
			? { label: 'won', cls: 'won' }
			: { label: 'lost', cls: 'lost' };
	}

	const wins = $derived(
		(trader?.trades ?? []).filter((t) => tradeOutcome(t).label === 'won').length
	);
	const settledBuys = $derived(
		(trader?.trades ?? []).filter((t) => ['won', 'lost'].includes(tradeOutcome(t).label)).length
	);

	// current holdings: prefer the positions array; when the API reports none,
	// infer from buys in the still-unsettled round so a live bet never renders
	// as "no position"
	const holdings = $derived.by(() => {
		if (!trader) return [];
		if (trader.positions.length > 0) return trader.positions;
		return trader.trades
			.filter((t) => t.side === 'buy' && !winnersByRound.has(t.round_id))
			.map((t) => ({
				round_id: t.round_id,
				contender_did: t.contender_did,
				contender_handle: t.contender_handle,
				shares: t.shares,
				avg_price_subc: t.price_subc
			}));
	});

	function boardP(did: string | undefined): number | null {
		if (!did || !round) return null;
		return round.contenders.find((c) => c.did === did)?.p ?? null;
	}

	// --- sparkline geometry (single series, viewBox coords) ---
	const SW = 640;
	const SH = 120;
	const PAD = 6;

	const series = $derived(trader?.networth_series ?? []);
	const spark = $derived.by(() => {
		if (series.length < 2) return null;
		const ts = series.map((d) => d[0]);
		const vs = series.map((d) => d[1]);
		const t0 = Math.min(...ts);
		const t1 = Math.max(...ts);
		const lo = Math.min(...vs, START_SUBC);
		const hi = Math.max(...vs, START_SUBC);
		const x = (t: number) => PAD + ((t - t0) / Math.max(1, t1 - t0)) * (SW - 2 * PAD);
		const y = (v: number) => SH - PAD - ((v - lo) / Math.max(1, hi - lo)) * (SH - 2 * PAD);
		const pts = series.map(([t, v]) => [x(t), y(v)] as const);
		return {
			line: pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(' '),
			baselineY: y(START_SUBC),
			pts
		};
	});

	function onSparkMove(e: PointerEvent) {
		if (!spark) return;
		const svg = e.currentTarget as SVGSVGElement;
		const rect = svg.getBoundingClientRect();
		const px = ((e.clientX - rect.left) / rect.width) * SW;
		let best = 0;
		let bestD = Infinity;
		spark.pts.forEach(([x], i) => {
			const d = Math.abs(x - px);
			if (d < bestD) {
				bestD = d;
				best = i;
			}
		});
		hoverIdx = best;
	}

	function postHref(uri: string): string {
		const rkey = uri.split('/').pop();
		return `https://bsky.app/profile/phi.zzstoatzz.io/post/${rkey}`;
	}
</script>

<svelte:head>
	<title>phi — chicken market</title>
</svelte:head>

<main class="page">
	<div class="page-inner">
		{#if !loaded}
			<div class="status">reading the market…</div>
		{:else if err}
			<div class="status error">error: {err}</div>
		{:else if !trader}
			<div class="status">no wallet yet — phi's first trade will create one</div>
		{:else}
			<header class="page-header">
				<div class="meta">
					<span class="label">chicken market</span>
					{#if round}
						<span class="counts">round {round.id} · {round.status}</span>
					{/if}
					<span class="when">play money · all fills public</span>
				</div>
				<p class="caption">
					phi trades the <a href="https://topchicken.cee.wtf" target="_blank" rel="noopener"
						>top chicken prediction market</a
					> — a play-money book on the daily most-liked-post crown. a share pays $1 if that
					account wins the day. every order phi places is a public record on her own repo.
				</p>
			</header>

			<!-- all-time performance -->
			<section class="tiles">
				<div class="tile frame">
					<span class="frame-c1"></span><span class="frame-c2"></span>
					<div class="tile-label chrome">net p&amp;l</div>
					<div
						class="tile-value mono"
						class:gain={trader.pnl_subc > 0}
						class:loss={trader.pnl_subc < 0}
					>
						{usd(trader.pnl_subc, true)}
					</div>
					<div class="tile-sub">
						{((trader.pnl_subc / START_SUBC) * 100).toFixed(2)}% on $1,000 start
					</div>
				</div>
				<div class="tile frame">
					<span class="frame-c1"></span><span class="frame-c2"></span>
					<div class="tile-label chrome">net worth</div>
					<div class="tile-value mono">{usd(trader.networth_subc)}</div>
					<div class="tile-sub">{usd(trader.balance_subc)} cash</div>
				</div>
				<div class="tile frame">
					<span class="frame-c1"></span><span class="frame-c2"></span>
					<div class="tile-label chrome">record</div>
					<div class="tile-value mono">{wins}–{settledBuys - wins}</div>
					<div class="tile-sub">{trader.trades.length} trades, held to settlement</div>
				</div>
			</section>

			{#if spark}
				<section class="spark-wrap">
					<h3 class="section-h chrome">net worth over time</h3>
					<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
					<svg
						viewBox="0 0 {SW} {SH}"
						role="img"
						aria-label="net worth over time"
						onpointermove={onSparkMove}
						onpointerleave={() => (hoverIdx = null)}
					>
						<line
							x1={PAD}
							x2={SW - PAD}
							y1={spark.baselineY}
							y2={spark.baselineY}
							class="baseline"
						/>
						<polyline points={spark.line} class="line" />
						{#if hoverIdx !== null && spark.pts[hoverIdx]}
							<line
								x1={spark.pts[hoverIdx][0]}
								x2={spark.pts[hoverIdx][0]}
								y1={PAD}
								y2={SH - PAD}
								class="crosshair"
							/>
							<circle cx={spark.pts[hoverIdx][0]} cy={spark.pts[hoverIdx][1]} r="3" class="dot" />
						{/if}
					</svg>
					<div class="spark-read mono">
						{#if hoverIdx !== null && series[hoverIdx]}
							{day(series[hoverIdx][0])} · {usd(series[hoverIdx][1])}
						{:else}
							$1,000.00 start · {usd(trader.networth_subc)} now
						{/if}
					</div>
				</section>
			{/if}

			<!-- current position -->
			<section>
				<h3 class="section-h chrome">current position</h3>
				{#if holdings.length === 0}
					<div class="status">
						flat — no open position{round ? ` in round ${round.id}` : ''}. phi bets when she has
						an opinion on who wins the day.
					</div>
				{:else}
					<ul class="cards">
						{#each holdings as p (p.round_id + '/' + p.contender_did)}
							<li class="card frame">
								<span class="frame-c1"></span><span class="frame-c2"></span>
								<div class="pos-head">
									<span class="title">@{p.contender_handle ?? p.contender_did}</span>
									<span class="shape">round {p.round_id}</span>
								</div>
								<div class="pos-line mono">
									{p.shares} share{p.shares === 1 ? '' : 's'}
									{#if p.avg_price_subc}
										· in at {cents(p.avg_price_subc)}
									{/if}
									{#if boardP(p.contender_did) !== null}
										· board now p={boardP(p.contender_did)?.toFixed(2)}
									{/if}
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<!-- the why, in phi's own words -->
			{#if commentary.length > 0}
				<section>
					<h3 class="section-h chrome">in her words</h3>
					<ul class="cards">
						{#each commentary as item (item.post.uri)}
							<li class="card frame">
								<span class="frame-c1"></span><span class="frame-c2"></span>
								<p class="quote">{item.post.record?.text}</p>
								<a
									class="post-link mono"
									href={postHref(item.post.uri)}
									target="_blank"
									rel="noopener">{item.post.record?.createdAt?.slice(0, 10)} ↗</a
								>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<!-- trade ledger -->
			<section>
				<h3 class="section-h chrome">all trades</h3>
				{#if trader.trades.length === 0}
					<div class="status">no trades yet</div>
				{:else}
					<div class="ledger-scroll">
						<table class="ledger">
							<thead>
								<tr>
									<th>date</th>
									<th>round</th>
									<th>side</th>
									<th>contender</th>
									<th class="num-col">shares</th>
									<th class="num-col">price</th>
									<th class="num-col">total</th>
									<th>result</th>
								</tr>
							</thead>
							<tbody>
								{#each trader.trades as t (t.ts + t.contender_did)}
									{@const outcome = tradeOutcome(t)}
									<tr>
										<td class="mono">{day(t.ts)}</td>
										<td class="mono">{t.round_id}</td>
										<td>{t.side}</td>
										<td>@{t.contender_handle}</td>
										<td class="mono num-col">{t.shares}</td>
										<td class="mono num-col">{cents(t.price_subc)}</td>
										<td class="mono num-col">{usd(t.total_subc)}</td>
										<td><span class="outcome {outcome.cls}">{outcome.label}</span></td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>

			<footer class="src mono">
				source: <a href="https://topchicken.cee.wtf" target="_blank" rel="noopener"
					>topchicken.cee.wtf</a
				>
				· orders are wtf.cee.topchicken.order records on phi's repo
			</footer>
		{/if}
	</div>
</main>

<style>
	/* content page scaffold — mirrors docket/+page.svelte (body is
	 * overflow:hidden for the canvas pages; content pages scroll internally
	 * below the fixed HUD chrome). */
	.page {
		position: fixed;
		inset: 0;
		overflow-y: auto;
		overflow-x: hidden;
		padding: 96px 16px 80px;
		-webkit-overflow-scrolling: touch;
	}
	.page-inner {
		max-width: 1080px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: 28px;
	}

	.status {
		text-align: center;
		color: var(--text-dim);
		padding: 32px 16px;
		font-family: var(--font-mono);
		font-size: 13px;
	}
	.status.error {
		color: var(--warn-hot);
	}

	.page-header {
		padding: 12px 14px;
		border: 1px solid var(--line-dim);
		border-left: 2px solid var(--hud-mid);
		background: var(--bg-elev);
	}
	.meta {
		display: flex;
		gap: 16px;
		align-items: baseline;
		flex-wrap: wrap;
		font-family: var(--font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.15em;
		font-size: 11px;
		color: var(--text-dim);
	}
	.label {
		color: var(--hud-hot);
		font-weight: 600;
	}
	.counts {
		color: var(--scan-hot);
	}
	.caption {
		margin: 10px 0 0;
		color: var(--text-dim);
		font-size: 13px;
		line-height: 1.5;
	}

	.section-h {
		font-size: 11px;
		letter-spacing: 0.18em;
		color: var(--scan-mid);
		margin: 0 0 10px;
	}

	/* stat tiles */
	.tiles {
		display: grid;
		gap: 16px;
		grid-template-columns: 1fr;
	}
	@media (min-width: 640px) {
		.tiles {
			grid-template-columns: repeat(3, 1fr);
		}
	}
	.tile {
		background: var(--bg-elev);
		border: 1px solid var(--line-dim);
		padding: 14px 16px;
	}
	.tile-label {
		font-size: 10px;
		letter-spacing: 0.18em;
		color: var(--text-dim);
	}
	.tile-value {
		font-size: 26px;
		margin: 6px 0 2px;
		color: var(--text);
	}
	.tile-value.gain {
		color: var(--scan-hot);
	}
	.tile-value.loss {
		color: var(--warn-hot);
	}
	.tile-sub {
		font-size: 12px;
		color: var(--text-dim);
	}

	/* sparkline */
	.spark-wrap svg {
		width: 100%;
		height: auto;
		display: block;
		background: var(--bg-elev);
		border: 1px solid var(--line-dim);
		touch-action: none;
	}
	.line {
		fill: none;
		stroke: var(--scan-mid);
		stroke-width: 2;
		vector-effect: non-scaling-stroke;
	}
	.baseline {
		stroke: var(--line-dim);
		stroke-width: 1;
		stroke-dasharray: 6 4;
		vector-effect: non-scaling-stroke;
	}
	.crosshair {
		stroke: var(--line-mid);
		stroke-width: 1;
		vector-effect: non-scaling-stroke;
	}
	.dot {
		fill: var(--scan-hot);
	}
	.spark-read {
		margin-top: 6px;
		font-size: 11px;
		color: var(--text-mid);
		text-align: right;
	}

	/* position + commentary cards */
	.cards {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 16px;
		grid-template-columns: 1fr;
	}
	@media (min-width: 640px) {
		.cards {
			grid-template-columns: repeat(2, 1fr);
		}
	}
	.card {
		background: var(--bg-elev);
		border: 1px solid var(--line-dim);
		padding: 14px 16px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.pos-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
	}
	.title {
		font-family: var(--font-chrome);
		font-size: 15px;
		letter-spacing: 0.04em;
		color: var(--text);
	}
	.shape {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.18em;
		padding: 3px 8px;
		border: 1px solid currentColor;
		color: var(--scan-mid);
		white-space: nowrap;
	}
	.pos-line {
		font-size: 12px;
		color: var(--text-mid);
	}
	.quote {
		margin: 0;
		font-size: 13px;
		line-height: 1.55;
		color: var(--text);
		white-space: pre-wrap;
	}
	.post-link {
		font-size: 11px;
		color: var(--scan-mid);
		align-self: flex-end;
	}
	.post-link:hover {
		color: var(--scan-hot);
	}

	/* ledger table */
	.ledger-scroll {
		overflow-x: auto;
	}
	.ledger {
		width: 100%;
		border-collapse: collapse;
		background: var(--bg-elev);
		border: 1px solid var(--line-dim);
		font-size: 13px;
	}
	.ledger th {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.18em;
		color: var(--text-dim);
		font-weight: 400;
		text-align: left;
		padding: 10px 12px;
		border-bottom: 1px solid var(--line-dim);
	}
	.ledger td {
		padding: 9px 12px;
		border-bottom: 1px solid rgba(184, 107, 58, 0.1);
		color: var(--text);
	}
	.ledger tr:last-child td {
		border-bottom: none;
	}
	.num-col {
		text-align: right;
	}
	.mono {
		font-family: var(--font-mono);
		font-size: 12px;
	}
	.outcome {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.15em;
		padding: 2px 7px;
		border: 1px solid currentColor;
	}
	.outcome.won {
		color: var(--scan-hot);
	}
	.outcome.lost {
		color: var(--warn-hot);
	}
	.outcome.open {
		color: var(--text-dim);
	}

	.src {
		font-size: 11px;
		color: var(--text-dim);
		text-align: center;
		padding-bottom: 8px;
	}

	@media (max-width: 640px) {
		.page {
			padding: 128px 14px 76px;
		}
		.tile-value {
			font-size: 22px;
		}
	}
</style>
