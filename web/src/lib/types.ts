// Shape mirrors phi's PDS record schemas + bot API responses.
// Keep in sync with bot/src/bot/core/goals.py and the json
// returned by /api/* endpoints.

// --- PDS records ---

export interface Goal {
	rkey: string;
	title: string;
	description: string;
	progress_signal: string;
	created_at: string;
	updated_at: string;
}

export interface BlogDoc {
	rkey: string;
	title: string;
	content: string;
	tags: string[];
	publishedAt: string;
	url: string; // greengale.app URL
}

// --- /api/activity (existing endpoint, mixed feed) ---

export type ActivityType = 'post' | 'note' | 'url';

export interface ActivityItem {
	type: ActivityType;
	text: string;
	title?: string | null;
	time: string;
	uri: string;
	url?: string | null;
}

// --- /api/memory/graph ---

export interface GraphNode {
	id: string;
	label: string;
	type: 'phi' | 'user';
	x: number | null;
	y: number | null;
}

export interface GraphEdge {
	source: string;
	target: string;
}

export interface GraphData {
	nodes: GraphNode[];
	edges: GraphEdge[];
}

// --- discovery pool (hub /api/agents/discovery-pool) ---

export interface DiscoveryPost {
	uri: string;
	text: string;
	liked_at: string;
}

export interface DiscoveryEntry {
	handle: string;
	did: string;
	likes_in_window: number;
	last_liked_at: string;
	sample_posts: DiscoveryPost[];
}

// --- /health ---

export interface HealthInfo {
	status: string;
	polling_active: boolean;
	paused: boolean;
}

// --- /api/abilities ---

export interface Capability {
	name: string;
	description: string;
	operator_only: boolean;
}

// --- /api/skills ---

export interface Skill {
	name: string;
	description: string;
	resources: string[];
}

// --- /api/users/{handle} ---

export interface UserViewObservation {
	content: string;
	tags: string[];
	created_at: string | null;
	source_uris: string[];
}

export interface UserViewSummary {
	content: string;
	created_at: string | null;
}

export interface UserView {
	handle: string;
	did: string | null;
	is_stranger: boolean;
	counts: {
		observation: number;
		interaction: number;
		summary: number;
	};
	first_seen: string | null;
	last_seen: string | null;
	summary: UserViewSummary | null;
	recent_observations: UserViewObservation[];
}

// --- bsky public API minimal types (used by feed/blog) ---

export interface BskyAuthor {
	did: string;
	handle: string;
	displayName?: string;
	avatar?: string;
}

export interface BskyPostRecord {
	text: string;
	createdAt: string;
	reply?: { parent: { uri: string; cid: string }; root: { uri: string; cid: string } };
	facets?: unknown[];
	embed?: unknown;
}

export interface BskyPost {
	uri: string;
	cid: string;
	author: BskyAuthor;
	record: BskyPostRecord;
	indexedAt: string;
	likeCount?: number;
	replyCount?: number;
	repostCount?: number;
}

export interface BskyFeedItem {
	post: BskyPost;
	reply?: { parent?: { author?: BskyAuthor; record?: BskyPostRecord; uri?: string } };
}

// --- /api/docket (daily promotion object) ---

export interface DocketEvidenceRef {
	atlas_point_id: string;
	kind: string;
	snippet: string;
}

export interface DocketAnchorRef {
	at_uri: string;
	kind: string;
	snippet: string;
}

export interface DocketCandidate {
	id: string;
	title: string;
	rationale: string;
	private_evidence: DocketEvidenceRef[];
	existing_public_anchors: DocketAnchorRef[];
	related_tags: string[];
	// knownValues-style — not a closed enum; the bot may emit new strings as
	// the rubric evolves. Renderer falls back to a neutral badge.
	suggested_shape: string;
	atlas_cluster_fine: number;
	atlas_cluster_coarse: number;
}

export interface Docket {
	generated_at: string;
	atlas_record_cid: string;
	atlas_point_count: number;
	candidates: DocketCandidate[];
}

// --- /api/atlas ---

export interface AtlasCluster {
	id: number;
	label?: string;
	count?: number;
	x?: number;
	y?: number;
	kind_counts?: Record<string, number>;
	parent_coarse?: number | null;
	[key: string]: unknown;
}

export interface Atlas {
	generated_at: string;
	points: {
		id?: string;
		kind?: string;
		label?: string;
		x?: number;
		y?: number;
		layer?: string;
		promotion_status?: string;
		cluster_coarse?: number;
		cluster_fine?: number;
		tags?: string[];
		[key: string]: unknown;
	}[];
	clusters_coarse: AtlasCluster[];
	clusters_fine: AtlasCluster[];
}

// --- cockpit / hud ---

/**
 * AtlasPoint is the unifying primitive across the mind lens. Every "object of
 * phi's attention" — concept-shaped (goal) and people-shaped (engaged,
 * candidate) — becomes a point with a kind, a position, and a payload that
 * becomes the logbook entry on click.
 */
export type AtlasKind =
	| 'phi'
	| 'handle-engaged'
	| 'handle-candidate'
	| 'goal';

export interface AtlasPoint {
	id: string;
	kind: AtlasKind;
	label: string; // 1-line for hover readout
	x: number; // normalized -1..1 (canvas scales)
	y: number; // normalized -1..1
	avatar?: string | null;
	payload: unknown; // pulled from the underlying record; logbook renders it
}

/**
 * Logbook entries are what slide in from the right when you click a thing.
 * The kind drives the renderer; the payload is the matching record shape.
 */
export type LogbookEntry =
	| { kind: 'handle'; handle: string; did?: string; engaged: boolean; payload: unknown }
	| { kind: 'goal'; goal: Goal }
	| { kind: 'docket'; candidate: DocketCandidate }
	| { kind: 'docket-list'; docket: Docket }
	| {
			kind: 'store';
			store: 'pds' | 'memory' | 'atlas';
			goals?: Goal[];
			known?: GraphNode[];
			atlas?: Atlas | null;
	  }
	| { kind: 'activity'; item: ActivityItem }
	| { kind: 'blog'; doc: BlogDoc }
	| { kind: 'discovery'; entry: DiscoveryEntry };
