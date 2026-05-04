// Typed API client surface. Three backends:
//   1. phi's own bot endpoints (relative URLs: /api/activity, /api/memory/graph, /health)
//   2. phi's PDS records (public, no auth) via bsky.social
//   3. external services: bsky public API, hub discovery pool

import type {
	ActivityItem,
	BlogDoc,
	BskyFeedItem,
	Capability,
	DiscoveryEntry,
	Goal,
	GraphData,
	HealthInfo,
	Observation,
	Skill,
	UserView
} from './types';

export const PHI_DID = 'did:plc:65sucjiel52gefhcdcypynsr';
export const PHI_HANDLE = 'phi.zzstoatzz.io';
export const OWNER_HANDLE = 'zzstoatzz.io';

const BSKY_PUBLIC = 'https://public.api.bsky.app';
const PDS_HOST = 'https://bsky.social';

interface PdsListRecordsResponse<V> {
	records: { uri: string; cid: string; value: V }[];
	cursor?: string;
}

async function listPdsRecords<V>(
	repo: string,
	collection: string,
	limit = 50
): Promise<{ uri: string; cid: string; value: V }[]> {
	const url = `${PDS_HOST}/xrpc/com.atproto.repo.listRecords?repo=${encodeURIComponent(repo)}&collection=${encodeURIComponent(collection)}&limit=${limit}`;
	const res = await fetch(url);
	if (!res.ok) throw new Error(`listRecords ${collection}: ${res.status}`);
	const data: PdsListRecordsResponse<V> = await res.json();
	return data.records;
}

function rkey(uri: string): string {
	return uri.split('/').pop() ?? '';
}

// --- phi state from PDS ---

export async function getGoals(): Promise<Goal[]> {
	const records = await listPdsRecords<{
		title: string;
		description: string;
		progress_signal: string;
		created_at: string;
		updated_at: string;
	}>(PHI_DID, 'io.zzstoatzz.phi.goal', 20);
	return records.map((r) => ({ rkey: rkey(r.uri), ...r.value }));
}

export async function getActiveObservations(): Promise<Observation[]> {
	const records = await listPdsRecords<{
		content: string;
		reasoning?: string;
		created_at: string;
	}>(PHI_DID, 'io.zzstoatzz.phi.observation', 50);
	return records
		.map((r) => ({
			rkey: rkey(r.uri),
			content: r.value.content,
			reasoning: r.value.reasoning ?? '',
			created_at: r.value.created_at
		}))
		.sort((a, b) => a.rkey.localeCompare(b.rkey));
}

export async function getBlogDocs(limit = 50): Promise<BlogDoc[]> {
	const records = await listPdsRecords<{
		title: string;
		content: string;
		tags?: string[];
		publishedAt?: string;
		path?: string;
	}>(PHI_DID, 'app.greengale.document', limit);
	return records
		.map((r) => ({
			rkey: rkey(r.uri),
			title: r.value.title,
			content: r.value.content,
			tags: r.value.tags ?? [],
			publishedAt: r.value.publishedAt ?? '',
			url: `https://greengale.app/${PHI_HANDLE}/${rkey(r.uri)}`
		}))
		.sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
}

export async function getMentionConsent(): Promise<string[]> {
	try {
		const records = await listPdsRecords<{ handles?: string[] }>(
			PHI_DID,
			'io.zzstoatzz.phi.mentionConsent',
			10
		);
		const set = new Set<string>();
		for (const r of records) {
			for (const h of r.value.handles ?? []) set.add(h);
		}
		return [...set].sort();
	} catch {
		return [];
	}
}

// --- bot endpoints (relative URLs; same-origin in prod, vite-proxied in dev) ---

export async function getActivity(): Promise<ActivityItem[]> {
	const res = await fetch('/api/activity');
	if (!res.ok) throw new Error(`activity: ${res.status}`);
	return await res.json();
}

export async function getMemoryGraph(): Promise<GraphData> {
	const res = await fetch('/api/memory/graph');
	if (!res.ok) throw new Error(`memory graph: ${res.status}`);
	return await res.json();
}

export async function getHealth(): Promise<HealthInfo> {
	const res = await fetch('/health');
	if (!res.ok) throw new Error(`health: ${res.status}`);
	return await res.json();
}

export async function getCapabilities(): Promise<Capability[]> {
	const res = await fetch('/api/abilities');
	if (!res.ok) throw new Error(`abilities: ${res.status}`);
	return await res.json();
}

export async function getSkills(): Promise<Skill[]> {
	const res = await fetch('/api/skills');
	if (!res.ok) throw new Error(`skills: ${res.status}`);
	return await res.json();
}

export async function getUserView(handle: string): Promise<UserView | null> {
	try {
		const res = await fetch(`/api/users/${encodeURIComponent(handle)}`);
		if (!res.ok) return null;
		return await res.json();
	} catch {
		return null;
	}
}

// --- bsky public API ---

export async function getBskyFeed(limit = 20): Promise<BskyFeedItem[]> {
	const url = `${BSKY_PUBLIC}/xrpc/app.bsky.feed.getAuthorFeed?actor=${PHI_DID}&filter=posts_with_replies&limit=${limit}`;
	const res = await fetch(url);
	if (!res.ok) throw new Error(`getAuthorFeed: ${res.status}`);
	const data: { feed: BskyFeedItem[] } = await res.json();
	return data.feed;
}

// Phi's current profile description — the bio she rewrites at every startup.
// Renders in the HudIdentity area as her own voice.
export async function getPhiBio(): Promise<string | null> {
	try {
		const url = `${BSKY_PUBLIC}/xrpc/app.bsky.actor.getProfile?actor=${PHI_DID}`;
		const res = await fetch(url);
		if (!res.ok) return null;
		const data: { description?: string } = await res.json();
		return data.description ?? null;
	} catch {
		return null;
	}
}

// --- discovery pool ---
//
// frontend calls the bot's /api/discovery (NOT hub directly), so the public
// page reflects the same filtered list phi sees in her prompt — operator
// likes minus handles phi has already exchanged with. single source of
// truth lives in bot/core/discovery_pool.py:get_filtered_pool.

export async function getDiscoveryPool(): Promise<DiscoveryEntry[]> {
	try {
		const res = await fetch('/api/discovery');
		if (!res.ok) return [];
		return await res.json();
	} catch {
		return [];
	}
}
