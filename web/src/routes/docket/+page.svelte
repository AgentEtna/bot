<script lang="ts">
	import { onMount } from 'svelte';
	import { getDocket } from '$lib/api';
	import type { Docket, DocketCandidate } from '$lib/types';

	let docket = $state<Docket | null>(null);
	let loaded = $state(false);
	let err = $state<string | null>(null);
	let expanded = $state<Set<string>>(new Set());

	onMount(async () => {
		try {
			docket = await getDocket();
		} catch (e) {
			err = (e as Error).message;
		}
		loaded = true;
	});

	function toggle(id: string) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	function generatedAgo(iso: string): string {
		if (!iso) return '';
		try {
			const then = new Date(iso).getTime();
			const now = Date.now();
			const mins = Math.floor((now - then) / 60_000);
			if (mins < 60) return `${mins}m ago`;
			const hrs = Math.floor(mins / 60);
			if (hrs < 24) return `${hrs}h ago`;
			return `${Math.floor(hrs / 24)}d ago`;
		} catch {
			return '';
		}
	}

	// rkey extraction for opening a Bluesky/Greengale anchor in a new tab
	function anchorHref(atUri: string): string | null {
		// at://did:plc:.../collection/rkey
		const m = atUri.match(/^at:\/\/([^/]+)\/([^/]+)\/(.+)$/);
		if (!m) return null;
		const [, did, collection, rkey] = m;
		if (collection === 'app.bsky.feed.post') return `https://bsky.app/profile/${did}/post/${rkey}`;
		if (collection === 'app.greengale.document') return `https://greengale.app/phi.zzstoatzz.io/${rkey}`;
		return null;
	}
</script>

<svelte:head>
	<title>phi — docket</title>
</svelte:head>

<main class="page">
	<div class="page-inner">
	{#if !loaded}
		<div class="status">loading docket…</div>
	{:else if err}
		<div class="status error">error: {err}</div>
	{:else if !docket}
		<div class="status">no docket yet — the next phi-atlas cycle will produce one</div>
	{:else}
		<header class="page-header">
			<div class="meta">
				<span class="label">docket</span>
				<span class="counts">{docket.candidates.length} candidates</span>
				<span class="when">generated {generatedAgo(docket.generated_at)}</span>
			</div>
			<p class="caption">
				promotion-pressure candidates from today's atlas. raw private clusters with no
				public anchor — phi's pending decisions about what wants to come out.
			</p>
		</header>

		{#if docket.candidates.length === 0}
			<div class="status">no candidates today — atlas had no qualifying pressure clusters</div>
		{:else}
			<ul class="cards">
				{#each docket.candidates as cand (cand.id)}
					{@const isOpen = expanded.has(cand.id)}
					<li class="card frame">
						<button
							class="card-header"
							onclick={() => toggle(cand.id)}
							aria-expanded={isOpen}
						>
							<span class="title">{cand.title}</span>
							<span class="shape shape-{cand.suggested_shape}">{cand.suggested_shape}</span>
						</button>
						<p class="rationale">{cand.rationale}</p>
						{#if isOpen}
							<div class="detail">
								{#if cand.private_evidence.length > 0}
									<section class="evidence">
										<h4>private evidence</h4>
										<ul>
											{#each cand.private_evidence as e}
												<li>
													<code class="kind">[{e.kind}]</code>
													<code class="id">{e.atlas_point_id}</code>
													{#if e.snippet}
														<div class="snippet">{e.snippet}</div>
													{/if}
												</li>
											{/each}
										</ul>
									</section>
								{/if}
								{#if cand.existing_public_anchors.length > 0}
									<section class="anchors">
										<h4>existing public anchors</h4>
										<ul>
											{#each cand.existing_public_anchors as a}
												<li>
													<code class="kind">[{a.kind}]</code>
													{#if anchorHref(a.at_uri)}
														<a href={anchorHref(a.at_uri)} target="_blank" rel="noopener">
															open
														</a>
													{/if}
													<code class="uri">{a.at_uri}</code>
													{#if a.snippet}
														<div class="snippet">{a.snippet}</div>
													{/if}
												</li>
											{/each}
										</ul>
									</section>
								{/if}
								{#if cand.related_tags.length > 0}
									<section class="tags">
										{#each cand.related_tags as t}
											<span class="tag">{t}</span>
										{/each}
									</section>
								{/if}
								<section class="cluster-ref">
									<code>cluster: fine={cand.atlas_cluster_fine} coarse={cand.atlas_cluster_coarse}</code>
								</section>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
	</div>
</main>

<style>
	/* body sets overflow:hidden + height:100vh for the canvas pages (MindMap,
	 * Constellation) — content pages like docket need their own scroll
	 * container that fills the viewport, sits below the fixed HUD chrome,
	 * and scrolls internally. */
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
		margin-bottom: 24px;
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

	/* card grid: 1 col mobile, 2 cols ≥640px */
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
		gap: 10px;
	}

	.card-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
		background: transparent;
		border: none;
		padding: 0;
		text-align: left;
		cursor: pointer;
		min-height: 44px; /* touch target */
		color: inherit;
		font: inherit;
	}
	.card-header:hover .title {
		color: var(--hud-hot);
	}

	.title {
		font-family: var(--font-chrome);
		font-size: 15px;
		letter-spacing: 0.04em;
		color: var(--text);
		flex: 1;
		line-height: 1.3;
	}

	.shape {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.18em;
		padding: 3px 8px;
		border: 1px solid currentColor;
		white-space: nowrap;
		flex-shrink: 0;
	}
	/* loose color hints per knownValues string. unknown shapes fall back to neutral. */
	.shape-card,
	.shape-url,
	.shape-note { color: var(--warn-hot); }
	.shape-post,
	.shape-thread { color: var(--hud-hot); }
	.shape-doc { color: var(--scan-hot); }
	.shape-connection { color: var(--text); }
	.shape-no-action { color: var(--text-dim); opacity: 0.7; }

	.rationale {
		margin: 0;
		font-size: 14px;
		line-height: 1.5;
		color: var(--text);
	}

	.detail {
		display: flex;
		flex-direction: column;
		gap: 14px;
		border-top: 1px dashed var(--line-dim);
		padding-top: 12px;
		margin-top: 4px;
	}

	.detail h4 {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.18em;
		color: var(--scan-mid);
		margin: 0 0 6px;
	}

	.detail ul {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.detail li {
		font-size: 13px;
		line-height: 1.4;
	}

	.kind {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--scan-mid);
		margin-right: 6px;
	}
	.id, .uri {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--text-dim);
		word-break: break-all;
	}
	.detail a {
		font-family: var(--font-chrome);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.15em;
		color: var(--hud-hot);
		margin-right: 6px;
	}

	.snippet {
		margin-top: 4px;
		padding-left: 12px;
		color: var(--text-dim);
		font-size: 12px;
		line-height: 1.5;
	}

	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.tag {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--text-dim);
		border: 1px solid var(--line-dim);
		padding: 2px 8px;
	}

	.cluster-ref code {
		font-family: var(--font-mono);
		font-size: 10px;
		color: var(--text-dim);
	}

	@media (max-width: 640px) {
		.page {
			padding: 128px 14px 76px;
		}

		.page-header {
			margin-bottom: 14px;
			padding: 12px;
		}

		.meta {
			gap: 8px 12px;
			font-size: 10px;
		}

		.caption {
			font-size: 12px;
			line-height: 1.45;
		}

		.cards {
			gap: 12px;
		}

		.card {
			padding: 12px;
		}

		.card-header {
			align-items: center;
			min-height: 48px;
		}

		.title {
			font-size: 15px;
			line-height: 1.1;
		}

		.rationale {
			font-size: 13px;
		}
	}
</style>
