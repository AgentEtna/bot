<script lang="ts">
	import { logbook } from '$lib/state.svelte';
	import { relativeWhen, whenTooltip } from '$lib/time';
	import { PHI_HANDLE, PHI_DID, OWNER_HANDLE, getUserView } from '$lib/api';
	import ViewIn from './ViewIn.svelte';
	import type {
		Goal,
		Observation,
		ActivityItem,
		BlogDoc,
		DocketCandidate,
		Docket,
		DiscoveryEntry,
		GraphNode,
		Atlas,
		UserView
	} from '$lib/types';

	type MemoryCandidate = {
		handle: string;
		point?: Atlas['points'][number];
		weight: number;
		clusterLabel?: string;
	};

	type MemoryPreview = MemoryCandidate & {
		view: UserView | null;
	};

	// Resolve an at-uri or a record reference into the bits ViewIn needs.
	function rkeyFromUri(uri: string): string {
		return uri.split('/').pop() ?? '';
	}
	function collectionFromUri(uri: string): string {
		// at://did/collection/rkey -> "collection"
		const parts = uri.replace(/^at:\/\//, '').split('/');
		return parts[1] ?? '';
	}
	function repoFromUri(uri: string): string {
		const parts = uri.replace(/^at:\/\//, '').split('/');
		return parts[0] ?? PHI_DID;
	}

	function bskyPostUrl(uri: string): string | null {
		const collection = collectionFromUri(uri);
		const repo = repoFromUri(uri);
		const rkey = rkeyFromUri(uri);
		if (collection !== 'app.bsky.feed.post' || !repo || !rkey) return null;
		return `https://bsky.app/profile/${repo}/post/${rkey}`;
	}

	function atlasKindCounts(atlas: Atlas | null | undefined): [string, number][] {
		const counts = new Map<string, number>();
		for (const point of atlas?.points ?? []) {
			const kind = point.kind ?? 'other';
			counts.set(kind, (counts.get(kind) ?? 0) + 1);
		}
		return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
	}

	function atlasPointHandle(point: Atlas['points'][number]): string | null {
		const refs = point.refs as { handle?: string; observation_count?: number } | undefined;
		if (refs?.handle) return refs.handle.replace(/^@/, '');
		if (point.label) return point.label.replace(/^@/, '');
		return null;
	}

	function atlasObservationCount(point: Atlas['points'][number]): number {
		const refs = point.refs as { observation_count?: number } | undefined;
		return refs?.observation_count ?? 0;
	}

	function isPublicPersonHandle(handle: string): boolean {
		const h = handle.replace(/^@/, '').toLowerCase();
		return (
			!h.includes('example') &&
			h !== PHI_HANDLE &&
			h !== OWNER_HANDLE &&
			!h.startsWith('zzstoatzz')
		);
	}

	function clusterLabel(atlas: Atlas | null | undefined, point: Atlas['points'][number]): string | undefined {
		const fine = atlas?.clusters_fine.find((c) => c.id === point.cluster_fine);
		if (fine?.label) return fine.label;
		const coarse = atlas?.clusters_coarse.find((c) => c.id === point.cluster_coarse);
		return coarse?.label;
	}

	function memoryCandidates(store: {
		known?: GraphNode[];
		atlas?: Atlas | null;
	}): MemoryCandidate[] {
		const byHandle = new Map<string, MemoryCandidate>();
		for (const point of store.atlas?.points ?? []) {
			if (point.kind !== 'handle-engaged') continue;
			const handle = atlasPointHandle(point);
			if (!handle) continue;
			if (!isPublicPersonHandle(handle)) continue;
			byHandle.set(handle, {
				handle,
				point,
				weight: atlasObservationCount(point),
				clusterLabel: clusterLabel(store.atlas, point)
			});
		}
		for (const node of store.known ?? []) {
			const handle = node.label.replace(/^@/, '');
			if (!isPublicPersonHandle(handle)) continue;
			if (!byHandle.has(handle)) {
				byHandle.set(handle, { handle, weight: 0 });
			}
		}
		return [...byHandle.values()]
			.sort((a, b) => b.weight - a.weight || a.handle.localeCompare(b.handle));
	}

	const entry = $derived(logbook.value);

	function close() {
		logbook.set(null);
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}

	$effect(() => {
		if (entry) {
			window.addEventListener('keydown', handleKey);
			return () => window.removeEventListener('keydown', handleKey);
		}
	});

	// User-view fetch: when the entry is a 'handle' or 'discovery', go pull
	// /api/users/{handle}. This is the rich state phi carries about a person —
	// histogram, summary, recent observations.
	//
	// `lastFetchedHandle` is a plain `let` (not $state) so the effect doesn't
	// track it — otherwise Svelte detects the read+write of the same piece of
	// state and throws effect_update_depth_exceeded.
	let userView = $state<UserView | null>(null);
	let userViewLoading = $state(false);
	let lastFetchedHandle: string | null = null;
	let memoryPreview = $state<MemoryPreview[]>([]);
	let memoryPreviewLoading = $state(false);
	let lastMemoryPreviewKey: string | null = null;

	$effect(() => {
		if (!entry) {
			userView = null;
			lastFetchedHandle = null;
			return;
		}
		const handle =
			entry.kind === 'handle'
				? entry.handle
				: entry.kind === 'discovery'
					? entry.entry.handle
					: null;
		if (!handle) {
			userView = null;
			lastFetchedHandle = null;
			return;
		}
		if (handle === lastFetchedHandle) return;
		lastFetchedHandle = handle;
		userView = null;
		userViewLoading = true;
		getUserView(handle).then((uv) => {
			if (handle === lastFetchedHandle) {
				userView = uv;
				userViewLoading = false;
			}
		});
	});

	$effect(() => {
		if (!entry || entry.kind !== 'store' || entry.store !== 'memory') {
			memoryPreview = [];
			memoryPreviewLoading = false;
			lastMemoryPreviewKey = null;
			return;
		}
		const store = entry as {
			kind: 'store';
			store: 'memory';
			known?: GraphNode[];
			atlas?: Atlas | null;
		};
		const candidates = memoryCandidates(store).slice(0, 12);
		const key = candidates.map((c) => `${c.handle}:${c.weight}`).join('|');
		if (key === lastMemoryPreviewKey) return;
		lastMemoryPreviewKey = key;
		memoryPreview = candidates.map((c) => ({ ...c, view: null }));
		memoryPreviewLoading = candidates.length > 0;
		Promise.allSettled(candidates.map((c) => getUserView(c.handle))).then((results) => {
			if (key !== lastMemoryPreviewKey) return;
			memoryPreview = candidates.map((candidate, i) => ({
				...candidate,
				view: results[i].status === 'fulfilled' ? results[i].value : null
			}));
			memoryPreviewLoading = false;
		});
	});

	function openPerson(handle: string) {
		logbook.set({ kind: 'handle', handle, engaged: true, payload: { handle } });
	}
</script>

{#if entry}
	<div
		class="overlay"
		onclick={close}
		role="presentation"
		tabindex="-1"
		onkeydown={handleKey}
	></div>
	<aside class="drawer scroll" aria-label="logbook entry">
		<header>
			<div class="kind chrome">
				{#if entry.kind === 'handle'}{entry.engaged ? 'in my memory' : 'on my radar'}{:else if entry.kind === 'observation'}attention{:else if entry.kind === 'goal'}goal{:else if entry.kind === 'docket'}promotion pressure{:else if entry.kind === 'activity'}emission · {entry.item.type}{:else if entry.kind === 'blog'}long form{:else if entry.kind === 'discovery'}on my radar{/if}
				{#if entry.kind === 'docket-list'}public candidates{:else if entry.kind === 'store'}memory store{/if}
			</div>
			<button class="close chrome" onclick={close} aria-label="close">close · esc</button>
		</header>

		{#if entry.kind === 'handle'}
			{@const handleEntry = entry as {
				kind: 'handle';
				handle: string;
				did?: string;
				engaged: boolean;
				payload: unknown;
			}}
			<h1 class="mono">@{handleEntry.handle}</h1>

			{#if userViewLoading}
				<p class="muted">recalling…</p>
			{:else if userView}
				<!-- top-line: phi's own framing of how she knows this person -->
				<p class="muted">
					{#if userView.is_stranger && userView.counts.observation === 0 && userView.counts.interaction === 0}
						i don't carry anything about them yet.
					{:else if userView.is_stranger}
						a thin sketch — not enough yet to feel like i know them.
					{:else}
						they're someone i carry.
					{/if}
				</p>

				<!-- histogram: counts per kind -->
				<div class="hist">
					<div class="hist-cell">
						<div class="hist-num mono">{userView.counts.observation}</div>
						<div class="hist-lbl chrome">observation{userView.counts.observation === 1 ? '' : 's'}</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{userView.counts.interaction}</div>
						<div class="hist-lbl chrome">exchange{userView.counts.interaction === 1 ? '' : 's'}</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{userView.counts.summary}</div>
						<div class="hist-lbl chrome">impression{userView.counts.summary === 1 ? '' : 's'}</div>
					</div>
				</div>

				{#if userView.first_seen}
					<div class="span chrome faint">
						first noted
						<span title={whenTooltip(userView.first_seen)}>{relativeWhen(userView.first_seen)}</span>
						{#if userView.last_seen && userView.last_seen !== userView.first_seen}
							· last touched
							<span title={whenTooltip(userView.last_seen)}>{relativeWhen(userView.last_seen)}</span>
						{/if}
					</div>
				{/if}

				{#if userView.recent_observations.length > 0}
					<div class="block">
						<div class="block-label chrome">recent notes</div>
						<ul class="obs-list">
							{#each userView.recent_observations as obs (obs.created_at ?? obs.content)}
								<li class="obs">
									<div class="obs-text">{obs.content}</div>
									<div class="obs-meta faint">
										{#if obs.tags.length > 0}
											<span class="tags mono">{obs.tags.slice(0, 3).join(' · ')}</span>
										{/if}
										{#if obs.created_at}
											<span class="when" title={whenTooltip(obs.created_at)}
												>{relativeWhen(obs.created_at)}</span
											>
										{/if}
										{#if obs.source_uris.length > 0}
											{@const sourceUrl = bskyPostUrl(obs.source_uris[0])}
											{#if sourceUrl}
												<a class="source-link" href={sourceUrl} target="_blank" rel="noopener">source</a>
											{/if}
										{/if}
									</div>
								</li>
							{/each}
						</ul>
					</div>
				{/if}

				{#if userView.summary}
					<div class="block synthesis">
						<div class="block-label chrome">synthesized impression</div>
						<div class="content">{userView.summary.content}</div>
						<div class="synthesis-note faint">
							Generated from carried notes; useful as orientation, not ground truth.
						</div>
					</div>
				{/if}
			{:else}
				<p class="muted">memory unreachable.</p>
			{/if}

			<div class="actions">
				<ViewIn kind="profile" handle={handleEntry.handle} did={handleEntry.did} />
			</div>
		{:else if entry.kind === 'observation'}
			{@const obs = entry as { kind: 'observation'; observation: Observation }}
			<h1>what i'm watching</h1>
			<p class="content">{obs.observation.content}</p>
			{#if obs.observation.reasoning}
				<div class="block">
					<div class="block-label chrome">why</div>
					<div class="muted">{obs.observation.reasoning}</div>
				</div>
			{/if}
			<div class="meta">
				<span class="faint" title={whenTooltip(obs.observation.created_at)}
					>noted {relativeWhen(obs.observation.created_at)}</span
				>
			</div>
			<div class="actions">
				<ViewIn
					kind="record"
					handle={PHI_HANDLE}
					did={PHI_DID}
					collection="io.zzstoatzz.phi.observation"
					rkey={obs.observation.rkey}
				/>
			</div>
		{:else if entry.kind === 'goal'}
			{@const goalE = entry as { kind: 'goal'; goal: Goal }}
			{@const goalTs = goalE.goal.updated_at || goalE.goal.created_at}
			<h1>{goalE.goal.title}</h1>
			<p class="content">{goalE.goal.description}</p>
			{#if goalE.goal.progress_signal}
				<div class="block">
					<div class="block-label chrome">how i'll know it's working</div>
					<div class="muted">{goalE.goal.progress_signal}</div>
				</div>
			{/if}
			<div class="meta">
				<span class="faint" title={whenTooltip(goalTs)}
					>last touched {relativeWhen(goalTs)}</span
				>
			</div>
			<div class="actions">
				<ViewIn
					kind="record"
					handle={PHI_HANDLE}
					did={PHI_DID}
					collection="io.zzstoatzz.phi.goal"
					rkey={goalE.goal.rkey}
				/>
			</div>
		{:else if entry.kind === 'docket'}
			{@const docket = entry as {
				kind: 'docket';
				candidate: DocketCandidate;
			}}
			<h1>{docket.candidate.title}</h1>
			<p class="content">{docket.candidate.rationale}</p>
			<div class="hist">
				<div class="hist-cell">
					<div class="hist-num mono">{docket.candidate.private_evidence.length}</div>
					<div class="hist-lbl chrome">private</div>
				</div>
				<div class="hist-cell">
					<div class="hist-num mono">{docket.candidate.existing_public_anchors.length}</div>
					<div class="hist-lbl chrome">public</div>
				</div>
				<div class="hist-cell">
					<div class="hist-num mono">{docket.candidate.suggested_shape}</div>
					<div class="hist-lbl chrome">form</div>
				</div>
			</div>
			<p class="muted">
				Form is the suggested way this private pattern might become public work.
			</p>
			{#if docket.candidate.private_evidence.length > 0}
				<div class="block">
					<div class="block-label chrome">private evidence</div>
					<ul class="obs-list">
						{#each docket.candidate.private_evidence as ev (ev.atlas_point_id)}
							<li class="obs">
								<div class="obs-text">{ev.snippet}</div>
								<div class="obs-meta faint mono">{ev.kind} · {ev.atlas_point_id}</div>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if docket.candidate.related_tags.length > 0}
				<div class="block">
					<div class="block-label chrome">tags</div>
					<div class="tags mono">{docket.candidate.related_tags.join(' · ')}</div>
				</div>
			{/if}
		{:else if entry.kind === 'docket-list'}
			{@const docketList = entry as { kind: 'docket-list'; docket: Docket }}
			<h1>public candidates</h1>
			<p class="muted">
				Private atlas evidence that may be ready to become public work. The form is a suggestion
				for how to publish it, such as a thread, note, reply, or longer piece.
			</p>
			<div class="hist">
				<div class="hist-cell">
					<div class="hist-num mono">{docketList.docket.candidates.length}</div>
					<div class="hist-lbl chrome">candidates</div>
				</div>
				<div class="hist-cell">
					<div class="hist-num mono">{docketList.docket.atlas_point_count}</div>
					<div class="hist-lbl chrome">atlas points</div>
				</div>
				<div class="hist-cell">
					<div class="hist-num mono">{docketList.docket.generated_at.slice(5, 10)}</div>
					<div class="hist-lbl chrome">generated</div>
				</div>
			</div>
			<div class="block">
				<div class="block-label chrome">ranked work</div>
				<ul class="obs-list">
					{#each docketList.docket.candidates as candidate (candidate.id)}
						<li class="obs">
							<div class="obs-text">{candidate.title}</div>
							<div class="obs-meta faint">
								<span>{candidate.private_evidence.length} private</span>
								<span>{candidate.existing_public_anchors.length} public</span>
								<span>form: {candidate.suggested_shape}</span>
							</div>
						</li>
					{/each}
				</ul>
			</div>
		{:else if entry.kind === 'store'}
			{@const store = entry as {
				kind: 'store';
				store: 'pds' | 'memory' | 'atlas';
				goals?: Goal[];
				observations?: Observation[];
				known?: GraphNode[];
				atlas?: Atlas | null;
			}}
			{#if store.store === 'pds'}
				<h1>PDS state</h1>
				<p class="muted">
					Small durable records that phi carries into future runs: explicit goals and active
					observations.
				</p>
				<div class="hist">
					<div class="hist-cell">
						<div class="hist-num mono">{store.goals?.length ?? 0}</div>
						<div class="hist-lbl chrome">goals</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{store.observations?.length ?? 0}</div>
						<div class="hist-lbl chrome">attention</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">PDS</div>
						<div class="hist-lbl chrome">source</div>
					</div>
				</div>
				{#if store.goals && store.goals.length > 0}
					<div class="block">
						<div class="block-label chrome">intent</div>
						<ul class="obs-list">
							{#each store.goals as goal (goal.rkey)}
								<li class="obs">
									<div class="obs-text">{goal.title}</div>
									<div class="obs-meta faint">{goal.description}</div>
								</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if store.observations && store.observations.length > 0}
					<div class="block">
						<div class="block-label chrome">attention</div>
						<ul class="obs-list">
							{#each store.observations as obs (obs.rkey)}
								<li class="obs">
									<div class="obs-text">{obs.content}</div>
									<div class="obs-meta faint">{obs.reasoning}</div>
								</li>
							{/each}
						</ul>
					</div>
				{/if}
			{:else if store.store === 'memory'}
				<h1>people memory</h1>
				<p class="muted">
					Relationship memory carried per person: impressions, active observations, and traces of
					exchange.
				</p>
				<div class="hist">
					<div class="hist-cell">
						<div class="hist-num mono">
							{Math.max(store.known?.length ?? 0, memoryCandidates(store).length)}
						</div>
						<div class="hist-lbl chrome">people</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{memoryPreview.reduce((n, p) => n + (p.view?.counts.observation ?? p.weight), 0)}</div>
						<div class="hist-lbl chrome">notes</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{memoryPreview.filter((p) => p.view?.summary).length}</div>
						<div class="hist-lbl chrome">impressions</div>
					</div>
				</div>
				{#if memoryPreviewLoading}
					<p class="muted">recalling relationship index…</p>
				{/if}
				{#if memoryPreview.length > 0}
					<div class="block">
						<div class="block-label chrome">strongest carried people</div>
						<div class="person-grid">
							{#each memoryPreview as person (person.handle)}
								<button class="person-card" onclick={() => openPerson(person.handle)}>
									<div class="person-top">
										<span class="person-handle mono">@{person.handle}</span>
										<span class="person-count mono">
											{person.view?.counts.observation ?? person.weight} obs · {person.view?.counts.interaction ?? 0} exch
										</span>
									</div>
									{#if person.view?.summary}
										<div class="person-summary">{person.view.summary.content}</div>
									{:else if person.view?.recent_observations?.[0]}
										<div class="person-summary">{person.view.recent_observations[0].content}</div>
									{:else if person.clusterLabel}
										<div class="person-summary">near {person.clusterLabel}</div>
									{:else}
										<div class="person-summary faint">carried in the relationship graph</div>
									{/if}
									<div class="person-meta faint">
										{#if person.clusterLabel}
											<span>{person.clusterLabel}</span>
										{/if}
										{#if person.view?.last_seen}
											<span title={whenTooltip(person.view.last_seen)}
												>{relativeWhen(person.view.last_seen)}</span
											>
										{/if}
									</div>
								</button>
							{/each}
						</div>
					</div>
				{:else if !memoryPreviewLoading}
					<p class="muted">No people memory loaded for this pass.</p>
				{/if}
			{:else}
				<h1>atlas</h1>
				<p class="muted">
					The large daily blob: clustered private points used to find patterns and promote
					candidates into public work.
				</p>
				<div class="hist">
					<div class="hist-cell">
						<div class="hist-num mono">{store.atlas?.points.length ?? 0}</div>
						<div class="hist-lbl chrome">points</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{store.atlas?.clusters_coarse.length ?? 0}</div>
						<div class="hist-lbl chrome">coarse</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{store.atlas?.clusters_fine.length ?? 0}</div>
						<div class="hist-lbl chrome">fine</div>
					</div>
				</div>
				{#if store.atlas}
					<div class="block">
						<div class="block-label chrome">point kinds</div>
						<div class="tags mono">
							{atlasKindCounts(store.atlas)
								.map(([kind, count]) => `${kind}:${count}`)
								.join(' · ')}
						</div>
					</div>
				{/if}
				{#if store.atlas?.clusters_fine?.length}
					<div class="block">
						<div class="block-label chrome">largest fine clusters</div>
						<ul class="obs-list">
							{#each [...store.atlas.clusters_fine].sort((a, b) => (b.count ?? 0) - (a.count ?? 0)).slice(0, 12) as cluster (cluster.id)}
								<li class="obs">
									<div class="obs-text">{cluster.label ?? `cluster ${cluster.id}`}</div>
									<div class="obs-meta faint">{cluster.count ?? 0} points</div>
								</li>
							{/each}
						</ul>
					</div>
				{/if}
			{/if}
		{:else if entry.kind === 'activity'}
			{@const act = entry as { kind: 'activity'; item: ActivityItem }}
			{@const kindLabel =
				act.item.type === 'post'
					? 'i posted'
					: act.item.type === 'note'
						? 'i made a note'
						: 'i bookmarked'}
			<h1 class="chrome">{kindLabel}</h1>
			{#if act.item.title}
				<div class="title">{act.item.title}</div>
			{/if}
			<p class="content">{act.item.text}</p>
			<div class="meta">
				<span class="faint" title={whenTooltip(act.item.time)}>{relativeWhen(act.item.time)}</span>
			</div>
			<div class="actions">
				{#if act.item.type === 'post' && act.item.uri.startsWith('at://')}
					<ViewIn
						kind="post"
						handle={PHI_HANDLE}
						did={repoFromUri(act.item.uri)}
						collection={collectionFromUri(act.item.uri)}
						rkey={rkeyFromUri(act.item.uri)}
					/>
				{:else if act.item.uri.startsWith('at://')}
					<ViewIn
						kind="record"
						handle={PHI_HANDLE}
						did={repoFromUri(act.item.uri)}
						collection={collectionFromUri(act.item.uri)}
						rkey={rkeyFromUri(act.item.uri)}
					/>
				{/if}
				{#if act.item.url}
					<a class="extlink" href={act.item.url} target="_blank" rel="noopener"
						>open the link ↗</a
					>
				{/if}
			</div>
		{:else if entry.kind === 'blog'}
			{@const blog = entry as { kind: 'blog'; doc: BlogDoc }}
			<h1>{blog.doc.title}</h1>
			<div class="content prose">{blog.doc.content}</div>
			<div class="meta">
				<span class="faint" title={whenTooltip(blog.doc.publishedAt)}
					>written {relativeWhen(blog.doc.publishedAt)}</span
				>
			</div>
			<div class="actions">
				<ViewIn
					kind="blog"
					handle={PHI_HANDLE}
					did={PHI_DID}
					collection="app.greengale.document"
					rkey={blog.doc.rkey}
				/>
			</div>
		{:else if entry.kind === 'discovery'}
			{@const disc = entry as { kind: 'discovery'; entry: DiscoveryEntry }}
			<h1 class="mono">@{disc.entry.handle}</h1>
			{#if userView && !userView.is_stranger}
				<p class="muted">someone i already carry, also surfacing on my radar:</p>
				<div class="hist">
					<div class="hist-cell">
						<div class="hist-num mono">{userView.counts.observation}</div>
						<div class="hist-lbl chrome">obs</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{userView.counts.interaction}</div>
						<div class="hist-lbl chrome">exch</div>
					</div>
					<div class="hist-cell">
						<div class="hist-num mono">{disc.entry.likes_in_window}</div>
						<div class="hist-lbl chrome">likes</div>
					</div>
				</div>
			{:else}
				<p class="muted">
					not in my memory yet. nate liked {disc.entry.likes_in_window} thing{disc.entry
						.likes_in_window === 1
						? ''
						: 's'} they wrote, most recently {relativeWhen(disc.entry.last_liked_at)}.
				</p>
			{/if}
			{#if disc.entry.sample_posts.length}
				<div class="block">
					<div class="block-label chrome">what nate liked</div>
					{#each disc.entry.sample_posts as post (post.uri)}
						<div class="sample">
							<div class="sample-text">{post.text}</div>
							<div class="sample-meta faint">{relativeWhen(post.liked_at)}</div>
						</div>
					{/each}
				</div>
			{/if}
			<div class="actions">
				<ViewIn kind="profile" handle={disc.entry.handle} did={disc.entry.did} />
			</div>
		{/if}

		<footer class="chrome faint">a window into phi's experience</footer>
	</aside>
{/if}

<style>
	.overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.4);
		z-index: 50;
		animation: fade 180ms ease-out;
	}

	.drawer {
		position: fixed;
		top: 0;
		right: 0;
		bottom: 0;
		width: min(520px, 92vw);
		background: var(--bg-deep);
		border-left: 1px solid var(--line-mid);
		box-shadow: inset 8px 0 24px rgba(184, 107, 58, 0.06);
		z-index: 51;
		padding: 22px 26px 26px;
		animation: slide 220ms cubic-bezier(0.16, 0.84, 0.3, 1);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.drawer::before,
	.drawer::after {
		content: '';
		position: absolute;
		left: -1px;
		width: 12px;
		height: 12px;
		border-color: var(--hud-mid);
		border-style: solid;
		border-width: 0;
		pointer-events: none;
	}

	.drawer::before {
		top: -1px;
		border-top-width: 1px;
		border-left-width: 1px;
	}

	.drawer::after {
		bottom: -1px;
		border-bottom-width: 1px;
		border-left-width: 1px;
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding-bottom: 10px;
		border-bottom: 1px solid var(--line-mid);
	}

	.kind {
		font-size: 10px;
		color: var(--scan-mid);
		letter-spacing: 0.18em;
	}

	.close {
		font-size: 9px;
		padding: 4px 8px;
	}

	h1 {
		font-family: var(--font-chrome);
		font-weight: 400;
		font-size: 22px;
		letter-spacing: 0.04em;
		color: var(--text);
		margin: 0;
	}

	h1.mono {
		font-family: var(--font-mono);
		text-transform: none;
		letter-spacing: 0;
		font-size: 18px;
	}

	h1.chrome {
		font-size: 14px;
		color: var(--hud-hot);
	}

	.title {
		font-size: 14px;
		color: var(--text);
	}

	.content {
		font-size: 13px;
		line-height: 1.6;
		color: var(--text);
		white-space: pre-wrap;
		word-break: break-word;
	}

	.prose {
		max-height: 60vh;
		overflow-y: auto;
		padding-right: 4px;
	}

	.block {
		border-left: 2px solid var(--line-mid);
		padding: 6px 12px;
		margin: 4px 0;
	}

	.block-label {
		font-size: 9px;
		color: var(--text-dim);
		margin-bottom: 4px;
	}

	.meta {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		align-items: baseline;
		font-size: 11px;
		margin-top: 6px;
	}

	.actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
		margin-top: 8px;
	}

	/* user-view histogram */
	.hist {
		display: flex;
		gap: 0;
		margin: 6px 0 4px;
		border: 1px solid var(--line-mid);
		clip-path: polygon(
			6px 0,
			100% 0,
			100% calc(100% - 6px),
			calc(100% - 6px) 100%,
			0 100%,
			0 6px
		);
	}

	.hist-cell {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		padding: 10px 8px 8px;
		gap: 2px;
		background: rgba(184, 107, 58, 0.04);
		border-right: 1px solid var(--line-dim);
	}

	.hist-cell:last-child {
		border-right: none;
	}

	.hist-num {
		font-size: 20px;
		color: var(--scan-hot);
		line-height: 1;
	}

	.hist-lbl {
		font-size: 8px;
		color: var(--text-dim);
		letter-spacing: 0.18em;
	}

	.span {
		font-size: 10px;
		letter-spacing: 0.1em;
		margin: 0 0 2px;
	}

	.obs-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.obs {
		padding: 6px 0;
		border-bottom: 1px solid var(--line-dim);
	}

	.obs:last-child {
		border-bottom: none;
	}

	.obs-text {
		font-size: 12px;
		line-height: 1.5;
		color: var(--text);
		margin-bottom: 4px;
		white-space: pre-wrap;
	}

	.obs-meta {
		display: flex;
		gap: 8px;
		font-size: 10px;
	}

	.tags {
		color: var(--scan-mid);
		font-size: 9px;
	}

	.when {
		color: var(--text-dim);
	}

	.source-link {
		color: var(--scan-mid);
		text-decoration: none;
		border-bottom: 1px solid rgba(126, 192, 212, 0.28);
	}

	.source-link:hover {
		color: var(--scan-hot);
		border-bottom-color: rgba(224, 144, 96, 0.5);
	}

	.synthesis {
		border-left-color: rgba(224, 144, 96, 0.48);
		background: linear-gradient(90deg, rgba(184, 107, 58, 0.06), transparent 58%);
	}

	.synthesis-note {
		margin-top: 8px;
		font-size: 10px;
		line-height: 1.4;
	}

	.extlink {
		font-size: 11px;
		color: var(--scan-mid);
		padding: 6px 10px;
		border: 1px solid var(--line-dim);
	}

	.extlink:hover {
		color: var(--scan-hot);
		border-color: var(--line-mid);
	}

	.sample {
		padding: 8px 0;
		border-bottom: 1px solid var(--line-dim);
	}

	.sample:last-child {
		border-bottom: none;
	}

	.sample-text {
		font-size: 12px;
		color: var(--text);
		margin-bottom: 4px;
		white-space: pre-wrap;
	}

	.sample-meta {
		font-size: 10px;
	}

	.person-grid {
		display: grid;
		grid-template-columns: 1fr;
		gap: 8px;
	}

	.person-card {
		display: flex;
		flex-direction: column;
		gap: 6px;
		text-align: left;
		text-transform: none;
		letter-spacing: 0;
		padding: 10px 12px;
		background:
			linear-gradient(120deg, rgba(74, 139, 154, 0.07), transparent 44%),
			rgba(7, 9, 15, 0.46);
		border: 1px solid var(--line-dim);
		border-radius: 4px;
		color: var(--text);
		cursor: pointer;
	}

	.person-card:hover {
		border-color: var(--scan-mid);
		background:
			linear-gradient(120deg, rgba(74, 139, 154, 0.12), transparent 48%),
			rgba(7, 9, 15, 0.62);
	}

	.person-top,
	.person-meta {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		align-items: baseline;
	}

	.person-handle {
		font-size: 11px;
		color: var(--scan-hot);
		text-transform: none;
	}

	.person-count {
		font-size: 9px;
		color: var(--text-dim);
		white-space: nowrap;
	}

	.person-summary {
		font-size: 12px;
		line-height: 1.45;
		color: var(--text);
		line-clamp: 3;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.person-meta {
		font-size: 9px;
	}

	footer {
		font-size: 9px;
		color: var(--text-dim);
		padding-top: 10px;
		margin-top: auto;
		border-top: 1px solid var(--line-dim);
	}

	@keyframes fade {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}

	@keyframes slide {
		from {
			transform: translateX(20px);
			opacity: 0;
		}
		to {
			transform: translateX(0);
			opacity: 1;
		}
	}
</style>
