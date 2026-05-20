<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { PHI_HANDLE } from '$lib/api';
	import { hudReadout, logbook } from '$lib/state.svelte';
	import AtlasOverlay from './AtlasOverlay.svelte';
	import type {
		Atlas,
		AtlasPoint,
		DiscoveryEntry,
		Docket,
		Goal,
		GraphNode,
		LogbookEntry,
		Observation
	} from '$lib/types';

	interface Props {
		goals: Goal[];
		observations: Observation[];
		known: GraphNode[];
		candidates: DiscoveryEntry[];
		avatars: Record<string, string>;
		docket: Docket | null;
		atlas: Atlas | null;
	}

	let { goals, observations, known, candidates, avatars, docket, atlas }: Props = $props();

	let canvas: HTMLCanvasElement;
	let W = $state(0);
	let H = $state(0);
	let dpr = 1;
	let frameRequested = false;
	let hovered = $state<Hotspot | null>(null);
	let hotspots = $state<Hotspot[]>([]);
	let points = $state<AtlasPoint[]>([]);
	let atlasExpanded = $state(false);

	const imageCache = new Map<string, HTMLImageElement>();
	const imageLoading = new Set<string>();
	const imageFailed = new Set<string>();

	type Rect = { x: number; y: number; w: number; h: number };
	type Ring = 'self' | 'goals' | 'attention' | 'people' | 'horizon';
	type AtlasKind = 'observation' | 'interaction' | 'summary' | 'episodic' | 'post' | 'note' | 'url' | 'handle-engaged' | 'other';
	type Hotspot = Rect & {
		label: string;
		readout: string;
		entry?: LogbookEntry;
		action?: 'atlas';
		point?: AtlasPoint;
	};
	type PreviewPoint = { id: string; sx: number; sy: number; color: string; promotion_status?: string };
	type PreviewCluster = { id: string | number; label: string; sx: number; sy: number; count: number };

	const rings: { key: Ring; r: number; label: string; metric: () => number }[] = [
		{ key: 'goals', r: 0.18, label: 'intent', metric: () => goals.length },
		{ key: 'attention', r: 0.32, label: 'attention', metric: () => observations.length },
		{ key: 'people', r: 0.55, label: 'people carried', metric: () => known.length },
		{ key: 'horizon', r: 0.82, label: 'horizon', metric: () => candidates.length }
	];

	const knownPeople = $derived.by(() =>
		known
			.filter((n) => n.type === 'user')
			.map((n) => n.label.replace(/^@/, ''))
			.filter(Boolean)
			.slice(0, 10)
	);
	const attentionPreview = $derived([...observations].slice(0, 3));
	const goalPreview = $derived([...goals].slice(0, 2));
	const docketPreview = $derived(docket?.candidates.slice(0, 3) ?? []);
	const atlasPreview = $derived.by(() => buildAtlasPreview());
	const atlasClusterPreview = $derived.by(() => buildAtlasClusterPreview());

	function resolve(name: string): string {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
	}

	function rgba(hex: string, alpha: number): string {
		const h = hex.replace('#', '');
		const r = parseInt(h.slice(0, 2), 16);
		const g = parseInt(h.slice(2, 4), 16);
		const b = parseInt(h.slice(4, 6), 16);
		return `rgba(${r}, ${g}, ${b}, ${alpha})`;
	}

	function atlasColor(kind: string | undefined): { core: string; mid: string; edge: string } {
		const key = (kind ?? 'other') as AtlasKind;
		const palette: Record<AtlasKind, { core: string; mid: string; edge: string }> = {
			observation: { core: '#7ec0d4', mid: '#4a8b9a', edge: '#1d3b46' },
			interaction: { core: '#e09060', mid: '#b86b3a', edge: '#4d2c14' },
			summary: { core: '#7bd3b1', mid: '#3c9f7c', edge: '#174638' },
			episodic: { core: '#b9a6ff', mid: '#7762c8', edge: '#30285c' },
			post: { core: '#e0bb6a', mid: '#c08a35', edge: '#5b3b12' },
			note: { core: '#f0d27d', mid: '#c9a05a', edge: '#5c4720' },
			url: { core: '#90d087', mid: '#5d9a52', edge: '#274821' },
			'handle-engaged': { core: '#d6d2c9', mid: '#8c8579', edge: '#3d3932' },
			other: { core: '#9aa3ad', mid: '#626c76', edge: '#252c34' }
		};
		return palette[key] ?? palette.other;
	}

	function dominantKind(kindCounts: Record<string, number> | undefined): string {
		let best = 'other';
		let bestCount = -1;
		for (const [kind, count] of Object.entries(kindCounts ?? {})) {
			if (count > bestCount) {
				best = kind;
				bestCount = count;
			}
		}
		return best;
	}

	function loadImage(url: string) {
		if (imageCache.has(url) || imageLoading.has(url) || imageFailed.has(url)) return;
		imageLoading.add(url);
		const img = new Image();
		img.onload = () => {
			imageCache.set(url, img);
			imageLoading.delete(url);
			scheduleFrame();
		};
		img.onerror = () => {
			imageFailed.add(url);
			imageLoading.delete(url);
		};
		img.src = url;
	}

	function hashAngle(s: string): number {
		let h = 2166136261;
		for (let i = 0; i < s.length; i++) {
			h ^= s.charCodeAt(i);
			h = Math.imul(h, 16777619);
		}
		return (((h >>> 0) % 10000) / 10000) * Math.PI * 2;
	}

	function place(): AtlasPoint[] {
		const out: AtlasPoint[] = [
			{
				id: 'phi',
				kind: 'phi',
				label: 'phi',
				x: 0,
				y: 0,
				avatar: avatars[PHI_HANDLE] ?? null,
				payload: {}
			}
		];

		const sortedGoals = [...goals].sort((a, b) => a.created_at.localeCompare(b.created_at));
		for (let i = 0; i < sortedGoals.length; i++) {
			const goal = sortedGoals[i];
			const angle = -Math.PI / 2 + (i / Math.max(sortedGoals.length, 1)) * Math.PI * 2;
			out.push({
				id: `goal-${goal.rkey}`,
				kind: 'goal',
				label: goal.title,
				x: Math.cos(angle) * 0.18,
				y: Math.sin(angle) * 0.18,
				payload: goal
			});
		}

		const sortedObs = [...observations].sort((a, b) => a.rkey.localeCompare(b.rkey));
		for (let i = 0; i < sortedObs.length; i++) {
			const obs = sortedObs[i];
			const angle = -Math.PI / 2 + (i / Math.max(sortedObs.length, 1)) * Math.PI * 2;
			out.push({
				id: `obs-${obs.rkey}`,
				kind: 'observation',
				label: obs.content,
				x: Math.cos(angle) * 0.32,
				y: Math.sin(angle) * 0.32,
				payload: obs
			});
		}

		const knownEntries = known.filter((n) => n.type === 'user');
		for (const node of knownEntries) {
			const handle = node.label.replace(/^@/, '');
			const angle =
				node.x != null && node.y != null && (node.x !== 0 || node.y !== 0)
					? Math.atan2(node.y, node.x)
					: hashAngle(handle);
			out.push({
				id: node.id,
				kind: 'handle-engaged',
				label: node.label,
				x: Math.cos(angle) * 0.55,
				y: Math.sin(angle) * 0.55,
				avatar: avatars[handle] ?? null,
				payload: { handle }
			});
		}

		const knownHandles = new Set(knownEntries.map((n) => n.label.replace(/^@/, '')));
		const fresh = [...candidates]
			.filter((c) => !knownHandles.has(c.handle))
			.sort((a, b) => b.last_liked_at.localeCompare(a.last_liked_at));
		for (let i = 0; i < fresh.length; i++) {
			const candidate = fresh[i];
			const angle = -Math.PI / 2 + (i / Math.max(fresh.length, 1)) * Math.PI * 2;
			out.push({
				id: `cand-${candidate.did}`,
				kind: 'handle-candidate',
				label: `@${candidate.handle}`,
				x: Math.cos(angle) * 0.82,
				y: Math.sin(angle) * 0.82,
				avatar: avatars[candidate.handle] ?? null,
				payload: { handle: candidate.handle, did: candidate.did, entry: candidate }
			});
		}

		return out;
	}

	function scheduleFrame() {
		if (!frameRequested) {
			frameRequested = true;
			requestAnimationFrame(draw);
		}
	}

	$effect(() => {
		void goals;
		void observations;
		void known;
		void candidates;
		void avatars;
		void docket;
		void atlas;
		const next = place();
		for (const p of next) if (p.avatar) loadImage(p.avatar);
		points = next;
		scheduleFrame();
	});

	function mobile(): boolean {
		return W < 760;
	}

	function field(): Rect {
		const mx = mobile() ? 18 : 62;
		const top = mobile() ? 136 : 112;
		const bottom = mobile() ? 244 : 68;
		return { x: mx, y: top, w: W - mx * 2, h: H - top - bottom };
	}

	function center(): { x: number; y: number } {
		const f = field();
		return {
			x: mobile() ? f.x + f.w / 2 : f.x + f.w * 0.44,
			y: f.y + f.h * (mobile() ? 0.5 : 0.46)
		};
	}

	function unit(): number {
		const f = field();
		return Math.min(f.w, f.h) * (mobile() ? 0.37 : 0.43);
	}

	function worldToScreen(x: number, y: number): [number, number] {
		const c = center();
		const u = unit();
		return [c.x + x * u, c.y + y * u];
	}

	function sidePanel(): Rect {
		const f = field();
		if (mobile()) {
			const y = Math.min(H - 292, f.y + f.h + 22);
			return { x: 14, y, w: W - 28, h: 220 };
		}
		return { x: f.x + f.w * 0.72, y: f.y + 26, w: Math.min(360, f.w * 0.24), h: f.h - 64 };
	}

	function chrome(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, size = 10) {
		ctx.font = `${size}px "Saira Condensed", sans-serif`;
		ctx.fillStyle = resolve('--text-dim');
		ctx.fillText(text.toUpperCase(), x, y);
	}

	function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, maxW: number, color = '--text-mid', size = 12) {
		ctx.font = `${size}px "Inter", system-ui, sans-serif`;
		ctx.fillStyle = resolve(color);
		let out = text;
		while (ctx.measureText(out).width > maxW && out.length > 5) out = out.slice(0, -2);
		if (out !== text) out = `${out.slice(0, -1)}…`;
		ctx.fillText(out, x, y);
	}

	function rounded(ctx: CanvasRenderingContext2D, r: Rect, radius = 7) {
		const rr = Math.min(radius, r.w / 2, r.h / 2);
		ctx.beginPath();
		ctx.moveTo(r.x + rr, r.y);
		ctx.lineTo(r.x + r.w - rr, r.y);
		ctx.quadraticCurveTo(r.x + r.w, r.y, r.x + r.w, r.y + rr);
		ctx.lineTo(r.x + r.w, r.y + r.h - rr);
		ctx.quadraticCurveTo(r.x + r.w, r.y + r.h, r.x + r.w - rr, r.y + r.h);
		ctx.lineTo(r.x + rr, r.y + r.h);
		ctx.quadraticCurveTo(r.x, r.y + r.h, r.x, r.y + r.h - rr);
		ctx.lineTo(r.x, r.y + rr);
		ctx.quadraticCurveTo(r.x, r.y, r.x + rr, r.y);
		ctx.closePath();
	}

	function drawBackdrop(ctx: CanvasRenderingContext2D) {
		const c = center();
		const u = unit();
		ctx.save();
		ctx.strokeStyle = resolve('--grid');
		ctx.lineWidth = 1;
		for (const ring of rings) {
			ctx.beginPath();
			ctx.arc(c.x, c.y, ring.r * u, 0, Math.PI * 2);
			ctx.stroke();
		}
		ctx.globalAlpha = 0.45;
		ctx.beginPath();
		ctx.moveTo(c.x - u * 0.9, c.y);
		ctx.lineTo(c.x + u * 0.9, c.y);
		ctx.moveTo(c.x, c.y - u * 0.9);
		ctx.lineTo(c.x, c.y + u * 0.9);
		ctx.stroke();
		ctx.restore();
	}

	function drawHeader(ctx: CanvasRenderingContext2D) {
		const f = field();
		chrome(ctx, mobile() ? 'memory field' : 'living memory field', f.x, f.y - 24, 12);
		const stats = [
			mobile() ? `${goals.length} intent` : `${goals.length} intent`,
			mobile() ? `${observations.length} attn` : `${observations.length} attention`,
			`${known.length} people`,
			mobile() ? `${docket?.candidates.length ?? 0} cand` : `${candidates.length} horizon`,
			...(mobile()
				? []
				: [`${docket?.candidates.length ?? 0} public candidates`, atlas ? `${atlas.points.length} atlas points` : 'atlas pending'])
		];
		label(ctx, stats.join(' · '), f.x, f.y - 5, f.w, '--scan-mid', mobile() ? 11 : 12);
	}

	function drawRingLabels(ctx: CanvasRenderingContext2D) {
		if (mobile()) return;
		const c = center();
		const u = unit();
		ctx.textAlign = 'left';
		ctx.textBaseline = 'middle';
		for (const ring of rings) {
			const y = c.y - ring.r * u;
			ctx.strokeStyle = resolve('--hud-mid');
			ctx.beginPath();
			ctx.moveTo(c.x - 4, y);
			ctx.lineTo(c.x + 4, y);
			ctx.stroke();
			chrome(ctx, `${ring.label} ${ring.metric()}`, c.x + 10, y + 1, 9);
		}
		ctx.textBaseline = 'alphabetic';
	}

	function radiusFor(p: AtlasPoint): number {
		if (p.kind === 'phi') return mobile() ? 20 : 29;
		if (p.kind === 'handle-engaged') return mobile() ? 6.5 : 10;
		if (p.kind === 'handle-candidate') return mobile() ? 4 : 6;
		if (p.kind === 'goal') return mobile() ? 6 : 8;
		return mobile() ? 4.5 : 6;
	}

	function drawHexPath(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number) {
		ctx.beginPath();
		for (let i = 0; i < 6; i++) {
			const a = -Math.PI / 2 + (i * Math.PI) / 3;
			const x = cx + Math.cos(a) * r;
			const y = cy + Math.sin(a) * r;
			if (i === 0) ctx.moveTo(x, y);
			else ctx.lineTo(x, y);
		}
		ctx.closePath();
	}

	function drawPhi(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number, p: AtlasPoint) {
		const glow = ctx.createRadialGradient(cx, cy, r * 0.4, cx, cy, r * 2.3);
		glow.addColorStop(0, 'rgba(224, 144, 96, 0.35)');
		glow.addColorStop(1, 'rgba(224, 144, 96, 0)');
		ctx.fillStyle = glow;
		ctx.beginPath();
		ctx.arc(cx, cy, r * 2.3, 0, Math.PI * 2);
		ctx.fill();

		const img = p.avatar ? imageCache.get(p.avatar) : null;
		if (img) {
			ctx.save();
			drawHexPath(ctx, cx, cy, r);
			ctx.clip();
			ctx.drawImage(img, cx - r, cy - r, r * 2, r * 2);
			ctx.restore();
		} else {
			ctx.fillStyle = resolve('--hud-hot');
			drawHexPath(ctx, cx, cy, r);
			ctx.fill();
		}
		ctx.strokeStyle = resolve('--hud-hot');
		ctx.lineWidth = 1.5;
		drawHexPath(ctx, cx, cy, r);
		ctx.stroke();
		chrome(ctx, 'phi', cx - 8, cy + r + 18, 11);
	}

	function drawPoint(ctx: CanvasRenderingContext2D, p: AtlasPoint) {
		const [cx, cy] = worldToScreen(p.x, p.y);
		const r = radiusFor(p);
		if (p.kind === 'phi') {
			drawPhi(ctx, cx, cy, r, p);
		} else if (p.kind === 'handle-engaged') {
			const img = p.avatar ? imageCache.get(p.avatar) : null;
			ctx.save();
			ctx.beginPath();
			ctx.arc(cx, cy, r, 0, Math.PI * 2);
			if (img) {
				ctx.clip();
				ctx.drawImage(img, cx - r, cy - r, r * 2, r * 2);
			} else {
				ctx.fillStyle = resolve('--text-mid');
				ctx.fill();
			}
			ctx.restore();
			ctx.strokeStyle = img ? resolve('--text') : resolve('--text-mid');
			ctx.lineWidth = 1.1;
			ctx.beginPath();
			ctx.arc(cx, cy, r, 0, Math.PI * 2);
			ctx.stroke();
		} else if (p.kind === 'handle-candidate') {
			ctx.strokeStyle = resolve('--text-dim');
			ctx.setLineDash([2, 2]);
			ctx.beginPath();
			ctx.arc(cx, cy, r, 0, Math.PI * 2);
			ctx.stroke();
			ctx.setLineDash([]);
		} else {
			ctx.fillStyle = resolve(p.kind === 'goal' ? '--warn' : '--scan-mid');
			drawHexPath(ctx, cx, cy, r);
			ctx.fill();
		}
		hotspots.push({
			x: cx - r - 7,
			y: cy - r - 7,
			w: (r + 7) * 2,
			h: (r + 7) * 2,
			label: p.label,
			readout: readoutFor(p),
			entry: entryFor(p) ?? undefined,
			point: p
		});
	}

	function drawSpokes(ctx: CanvasRenderingContext2D) {
		const c = center();
		ctx.strokeStyle = resolve('--line-dim');
		ctx.lineWidth = 1;
		for (const p of points) {
			if (p.kind !== 'goal' && p.kind !== 'observation') continue;
			const [x, y] = worldToScreen(p.x, p.y);
			ctx.beginPath();
			ctx.moveTo(c.x, c.y);
			ctx.lineTo(x, y);
			ctx.stroke();
		}
	}

	function drawCycle(ctx: CanvasRenderingContext2D) {
		if (mobile()) return;
		const c = center();
		const u = unit();
		const y = c.y + u * 0.72;
		const steps = mobile()
			? [
					{ x: c.x - u * 0.42, label: 'signals' },
					{ x: c.x, label: 'pass' },
					{ x: c.x + u * 0.42, label: 'memory' }
				]
			: [
					{ x: c.x - u * 0.72, label: 'signals' },
					{ x: c.x - u * 0.24, label: 'attend' },
					{ x: c.x + u * 0.24, label: 'remember' },
					{ x: c.x + u * 0.72, label: 'publish' }
				];
		ctx.save();
		ctx.strokeStyle = 'rgba(224, 144, 96, 0.24)';
		ctx.fillStyle = 'rgba(7, 9, 15, 0.66)';
		for (let i = 0; i < steps.length; i++) {
			if (i > 0) {
				ctx.beginPath();
				ctx.moveTo(steps[i - 1].x + 28, y);
				ctx.lineTo(steps[i].x - 28, y);
				ctx.stroke();
			}
			const r = { x: steps[i].x - 30, y: y - 15, w: 60, h: 30 };
			rounded(ctx, r, 4);
			ctx.fillStyle = 'rgba(7, 9, 15, 0.66)';
			ctx.fill();
			ctx.strokeStyle = 'rgba(224, 144, 96, 0.24)';
			ctx.stroke();
			chrome(ctx, steps[i].label, r.x + 10, r.y + 19, 9);
		}
		ctx.restore();
	}

	function atlasBounds() {
		const pts = atlas?.points.filter((p) => typeof p.x === 'number' && typeof p.y === 'number') ?? [];
		if (pts.length === 0) return null;
		let minX = Infinity;
		let maxX = -Infinity;
		let minY = Infinity;
		let maxY = -Infinity;
		for (const p of pts) {
			const x = p.x as number;
			const y = p.y as number;
			if (x < minX) minX = x;
			if (x > maxX) maxX = x;
			if (y < minY) minY = y;
			if (y > maxY) maxY = y;
		}
		const dx = Math.max(0.001, maxX - minX);
		const dy = Math.max(0.001, maxY - minY);
		return {
			minX: minX - dx * 0.08,
			maxX: maxX + dx * 0.08,
			minY: minY - dy * 0.08,
			maxY: maxY + dy * 0.08
		};
	}

	function atlasProject(x: number, y: number, r: Rect, b: NonNullable<ReturnType<typeof atlasBounds>>): [number, number] {
		const nx = (x - b.minX) / (b.maxX - b.minX);
		const ny = (y - b.minY) / (b.maxY - b.minY);
		return [r.x + nx * r.w, r.y + (1 - ny) * r.h];
	}

	function atlasUnit(x: number, y: number, b: NonNullable<ReturnType<typeof atlasBounds>>): [number, number] {
		const sx = ((x - b.minX) / (b.maxX - b.minX)) * 100;
		const sy = (1 - (y - b.minY) / (b.maxY - b.minY)) * 100;
		return [sx, sy];
	}

	function buildAtlasPreview(): PreviewPoint[] {
		const b = atlasBounds();
		if (!atlas || !b) return [];
		const numeric = atlas.points.filter((p) => typeof p.x === 'number' && typeof p.y === 'number');
		const limit = 560;
		const step = Math.max(1, Math.ceil(numeric.length / limit));
		const out: PreviewPoint[] = [];
		for (let i = 0; i < numeric.length; i += step) {
			const p = numeric[i];
			const [sx, sy] = atlasUnit(p.x as number, p.y as number, b);
			out.push({
				id: p.id ?? `atlas-${i}`,
				sx,
				sy,
				color: atlasColor(p.kind).core,
				promotion_status: p.promotion_status
			});
		}
		return out;
	}

	function buildAtlasClusterPreview(): PreviewCluster[] {
		const b = atlasBounds();
		if (!atlas || !b) return [];
		return [...atlas.clusters_coarse]
			.filter((cl) => cl.label && typeof cl.x === 'number' && typeof cl.y === 'number')
			.sort((a, b2) => (b2.count ?? 0) - (a.count ?? 0))
			.slice(0, 3)
			.map((cl) => {
				const [sx, sy] = atlasUnit(cl.x as number, cl.y as number, b);
				return {
					id: cl.id,
					label: cl.label ?? 'region',
					sx,
					sy,
					count: cl.count ?? 0
				};
			});
	}

	function avatarFor(handle: string): string | null {
		return avatars[handle] ?? null;
	}

	function compactHandle(handle: string): string {
		return handle.length > 22 ? `${handle.slice(0, 21)}…` : handle;
	}

	function atlasMiniRect(): Rect {
		const p = sidePanel();
		return {
			x: p.x + 14,
			y: p.y + (mobile() ? 36 : 64),
			w: p.w - 28,
			h: mobile() ? 78 : Math.min(218, p.h * 0.44)
		};
	}

	function storeRowRects(): { rect: Rect; entry?: LogbookEntry; title: string; value: string }[] {
		const p = sidePanel();
		const atlasRect = atlasMiniRect();
		const docketCount = docket?.candidates.length ?? 0;
		const rows = [
			{
				title: 'PDS state',
				value: `${goals.length} goals · ${observations.length} observations`,
				entry: { kind: 'store', store: 'pds', goals, observations } as LogbookEntry
			},
			{
				title: 'people memory',
				value: `${known.length} profiles with carried context`,
				entry: { kind: 'store', store: 'memory', known, atlas } as LogbookEntry
			},
			{
				title: 'public candidates',
				value: `${docketCount} candidates from private evidence`,
				entry: docket ? ({ kind: 'docket-list', docket } as LogbookEntry) : undefined
			}
		];
		const rowH = mobile() ? 28 : 46;
		const gap = mobile() ? 8 : 10;
		let y = atlasRect.y + atlasRect.h + (mobile() ? 10 : 12);
		return rows.map((row) => {
			const rect = { x: p.x + 14, y, w: p.w - 28, h: rowH };
			y += rowH + gap;
			return { ...row, rect };
		});
	}

	function drawAtlasMini(ctx: CanvasRenderingContext2D, r: Rect) {
		rounded(ctx, r, 6);
		ctx.save();
		ctx.clip();
		const bg = ctx.createLinearGradient(r.x, r.y, r.x + r.w, r.y + r.h);
		bg.addColorStop(0, 'rgba(4, 8, 14, 0.92)');
		bg.addColorStop(0.52, 'rgba(8, 18, 24, 0.82)');
		bg.addColorStop(1, 'rgba(20, 12, 18, 0.88)');
		ctx.fillStyle = bg;
		ctx.fillRect(r.x, r.y, r.w, r.h);

		ctx.strokeStyle = 'rgba(126, 192, 212, 0.055)';
		ctx.lineWidth = 1;
		const pitch = 18;
		for (let x = r.x - r.h; x < r.x + r.w + r.h; x += pitch) {
			ctx.beginPath();
			ctx.moveTo(x, r.y + r.h);
			ctx.lineTo(x + r.h, r.y);
			ctx.stroke();
		}
		for (let y = r.y; y < r.y + r.h; y += 4) {
			ctx.strokeStyle = y % 16 === 0 ? 'rgba(224, 144, 96, 0.055)' : 'rgba(255,255,255,0.018)';
			ctx.beginPath();
			ctx.moveTo(r.x, y);
			ctx.lineTo(r.x + r.w, y);
			ctx.stroke();
		}

		const b = atlasBounds();
		if (!atlas || !b) {
			label(ctx, 'atlas pending', r.x + 12, r.y + r.h / 2, r.w - 24, '--text-dim', 11);
			ctx.restore();
			return;
		}

		const coarse = [...atlas.clusters_coarse].sort((a, b2) => (b2.count ?? 0) - (a.count ?? 0));
		for (const cl of coarse) {
			if (typeof cl.x !== 'number' || typeof cl.y !== 'number') continue;
			const [x, y] = atlasProject(cl.x, cl.y, r, b);
			const color = atlasColor(dominantKind(cl.kind_counts));
			const radius = Math.max(14, Math.min(58, Math.sqrt(cl.count ?? 1) * 3.2));
			const grad = ctx.createRadialGradient(x, y, 0, x, y, radius);
			grad.addColorStop(0, rgba(color.core, 0.24));
			grad.addColorStop(0.45, rgba(color.mid, 0.1));
			grad.addColorStop(1, rgba(color.edge, 0));
			ctx.fillStyle = grad;
			ctx.globalAlpha = 0.9;
			ctx.beginPath();
			ctx.arc(x, y, radius, 0, Math.PI * 2);
			ctx.fill();
		}

		for (const p of atlas.points) {
			if (typeof p.x !== 'number' || typeof p.y !== 'number') continue;
			const [x, y] = atlasProject(p.x, p.y, r, b);
			const color = atlasColor(p.kind);
			const promoted = p.promotion_status === 'promoted';
			ctx.globalAlpha = promoted ? 0.96 : 0.52;
			ctx.fillStyle = promoted ? color.core : color.mid;
			ctx.fillRect(x, y, promoted ? 1.8 : 1.2, promoted ? 1.8 : 1.2);
		}

		ctx.globalAlpha = 1;
		ctx.font = '9px "Saira Condensed", sans-serif';
		ctx.textAlign = 'center';
		ctx.textBaseline = 'middle';
		const placed: { x: number; y: number; w: number; h: number }[] = [];
		const labelLimit = mobile() ? 2 : 4;
		let labelCount = 0;
		for (const cl of coarse) {
			if (labelCount >= labelLimit) break;
			if (!cl.label || typeof cl.x !== 'number' || typeof cl.y !== 'number') continue;
			const [x, y] = atlasProject(cl.x, cl.y, r, b);
			const text = cl.label.toUpperCase();
			const tw = ctx.measureText(text).width;
			const box = { x: x - tw / 2, y: y - 7, w: tw, h: 14 };
			let collides = false;
			for (const prev of placed) {
				if (
					box.x < prev.x + prev.w + 8 &&
					box.x + box.w + 8 > prev.x &&
					box.y < prev.y + prev.h + 6 &&
					box.y + box.h + 6 > prev.y
				) {
					collides = true;
					break;
				}
			}
			if (collides) continue;
			placed.push(box);
			ctx.strokeStyle = 'rgba(0,0,0,0.72)';
			ctx.lineWidth = 3;
			ctx.strokeText(text, x, y);
			ctx.fillStyle = 'rgba(214, 210, 201, 0.76)';
			ctx.fillText(text, x, y);
			labelCount++;
		}
		ctx.textAlign = 'left';
		ctx.textBaseline = 'alphabetic';

		const promoted = atlas.points.filter((p) => p.promotion_status === 'promoted').length;
		const pointText =
			atlas.points.length >= 1000 ? `${(atlas.points.length / 1000).toFixed(1)}k` : String(atlas.points.length);
		const read = `${pointText} pts · ${atlas.clusters_coarse.length} rg · ${atlas.clusters_fine.length} cl`;
		ctx.fillStyle = 'rgba(4, 7, 12, 0.72)';
		ctx.fillRect(r.x, r.y + r.h - 22, r.w, 22);
		chrome(ctx, 'semantic atlas', r.x + 10, r.y + r.h - 8, 9);
		label(ctx, read, r.x + 100, r.y + r.h - 8, r.w - 110, '--scan-hot', 10);
		ctx.restore();

		ctx.strokeStyle = 'rgba(126, 192, 212, 0.28)';
		rounded(ctx, r, 6);
		ctx.stroke();
		hotspots.push({
			...r,
			label: 'semantic atlas',
			readout: `semantic atlas · ${atlas.points.length} points · ${promoted} promoted · click to expand map`,
			action: 'atlas'
		});
	}

	function drawStores(ctx: CanvasRenderingContext2D) {
		const p = sidePanel();
		ctx.save();
		rounded(ctx, p, 8);
		ctx.fillStyle = 'rgba(9, 13, 20, 0.66)';
		ctx.fill();
		ctx.strokeStyle = 'rgba(74, 139, 154, 0.32)';
		ctx.stroke();
		chrome(ctx, mobile() ? 'substrate' : 'under the field', p.x + 14, p.y + 24, 11);
		if (!mobile()) {
			label(ctx, 'private state, memory, and publication pressure', p.x + 14, p.y + 45, p.w - 28, '--text-dim', 11);
		}

		const atlasRect = atlasMiniRect();
		drawAtlasMini(ctx, atlasRect);

		for (const row of storeRowRects()) {
			const r = row.rect;
			rounded(ctx, r, 5);
			ctx.fillStyle = 'rgba(4, 7, 12, 0.42)';
			ctx.fill();
			ctx.strokeStyle = row.title === 'public candidates' ? 'rgba(224, 144, 96, 0.42)' : 'rgba(74, 139, 154, 0.22)';
			ctx.stroke();
			chrome(ctx, row.title, r.x + 10, r.y + (mobile() ? 18 : 18), 9);
			if (!mobile()) label(ctx, row.value, r.x + 10, r.y + 38, r.w - 20, '--text-mid', 11);
			hotspots.push({
				...r,
				label: row.title,
				readout: `${row.title} · ${row.value}`,
				entry: row.entry
			});
		}
		ctx.restore();
	}

	function drawReticle(ctx: CanvasRenderingContext2D, h: Hotspot) {
		ctx.save();
		if (h.point) {
			const [cx, cy] = worldToScreen(h.point.x, h.point.y);
			const r = radiusFor(h.point) + 6;
			const arm = 7;
			ctx.strokeStyle = resolve('--hud-hot');
			ctx.lineWidth = 1.2;
			for (const [sx, sy] of [
				[-1, -1],
				[1, -1],
				[-1, 1],
				[1, 1]
			]) {
				const x = cx + sx * r;
				const y = cy + sy * r;
				ctx.beginPath();
				ctx.moveTo(x, y - sy * arm);
				ctx.lineTo(x, y);
				ctx.lineTo(x - sx * arm, y);
				ctx.stroke();
			}
		} else {
			rounded(ctx, h, 6);
			ctx.strokeStyle = resolve('--hud-hot');
			ctx.lineWidth = 1.4;
			ctx.stroke();
		}
		ctx.restore();
	}

	function readoutFor(p: AtlasPoint): string {
		const labels: Record<string, string> = {
			phi: 'self',
			'handle-engaged': 'person in memory',
			'handle-candidate': 'person on horizon',
			goal: 'intent',
			observation: 'active attention'
		};
		return `${labels[p.kind] ?? p.kind} · ${p.label}`;
	}

	function entryFor(p: AtlasPoint): LogbookEntry | null {
		if (p.kind === 'phi') return null;
		if (p.kind === 'handle-engaged') {
			const payload = p.payload as { handle: string };
			return { kind: 'handle', handle: payload.handle, engaged: true, payload };
		}
		if (p.kind === 'handle-candidate') {
			const payload = p.payload as { entry: DiscoveryEntry };
			return { kind: 'discovery', entry: payload.entry };
		}
		if (p.kind === 'goal') return { kind: 'goal', goal: p.payload as Goal };
		if (p.kind === 'observation') return { kind: 'observation', observation: p.payload as Observation };
		return null;
	}

	function draw() {
		frameRequested = false;
		if (!canvas) return;
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		hotspots = [];
		ctx.save();
		ctx.scale(dpr, dpr);
		ctx.clearRect(0, 0, W, H);
		drawBackdrop(ctx);
		drawHeader(ctx);
		drawSpokes(ctx);
		for (const p of points) drawPoint(ctx, p);
		drawRingLabels(ctx);
		drawCycle(ctx);
		drawStores(ctx);
		if (hovered) drawReticle(ctx, hovered);
		ctx.restore();
	}

	function hit(mx: number, my: number): Hotspot | null {
		for (let i = hotspots.length - 1; i >= 0; i--) {
			const h = hotspots[i];
			if (mx >= h.x && mx <= h.x + h.w && my >= h.y && my <= h.y + h.h) return h;
		}
		return null;
	}

	function onPointerMove(e: PointerEvent) {
		const rect = canvas.getBoundingClientRect();
		const h = hit(e.clientX - rect.left, e.clientY - rect.top);
		if (h !== hovered) {
			hovered = h;
			hudReadout.set(h ? h.readout : '');
			canvas.style.cursor = h?.entry || h?.action ? 'pointer' : 'default';
			scheduleFrame();
		}
	}

	function onClick(e: MouseEvent) {
		const rect = canvas.getBoundingClientRect();
		const h = hit(e.clientX - rect.left, e.clientY - rect.top);
		if (h?.action === 'atlas') {
			atlasExpanded = true;
			return;
		}
		if (h?.entry) logbook.set(h.entry);
	}

	let ro: ResizeObserver | null = null;
	function resize() {
		const host = canvas?.closest('.host') as HTMLElement | null;
		if (!canvas || !host) return;
		const rect = host.getBoundingClientRect();
		W = rect.width;
		H = rect.height;
		dpr = window.devicePixelRatio || 1;
		canvas.width = W * dpr;
		canvas.height = H * dpr;
		canvas.style.width = `${W}px`;
		canvas.style.height = `${H}px`;
		scheduleFrame();
	}

	onMount(() => {
		resize();
		ro = new ResizeObserver(resize);
		const host = canvas?.closest('.host') as HTMLElement | null;
		if (host) ro.observe(host);
	});

	onDestroy(() => {
		ro?.disconnect();
		hudReadout.set('');
	});
</script>

<div class="host">
	<div class="desktop-field" aria-hidden="true">
		<canvas
			bind:this={canvas}
			onpointermove={onPointerMove}
			onpointerleave={() => {
				hovered = null;
				hudReadout.set('');
				canvas.style.cursor = 'default';
				scheduleFrame();
			}}
			onclick={onClick}
		></canvas>
	</div>

	<div class="mobile-mind">
		<section class="mobile-panel overview">
			<div class="section-label chrome">mind state</div>
			<div class="overview-copy">
				<div class="overview-title chrome">living memory</div>
				<div class="overview-sub">A compact read of phi's current context surfaces.</div>
			</div>
			<div class="metric-grid" aria-label="memory counts">
				<button
					class="metric"
					onclick={() => logbook.set({ kind: 'store', store: 'pds', goals, observations })}
				>
					<span class="metric-value mono">{goals.length}</span>
					<span class="metric-label chrome">intent</span>
				</button>
				<button
					class="metric"
					onclick={() => {
						const first = observations[0];
						if (first) logbook.set({ kind: 'observation', observation: first });
					}}
				>
					<span class="metric-value mono">{observations.length}</span>
					<span class="metric-label chrome">attention</span>
				</button>
				<button
					class="metric"
					onclick={() => logbook.set({ kind: 'store', store: 'memory', known, atlas })}
				>
					<span class="metric-value mono">{known.length}</span>
					<span class="metric-label chrome">people</span>
				</button>
				<button
					class="metric metric-warm"
					onclick={() => {
						if (docket) logbook.set({ kind: 'docket-list', docket });
					}}
				>
					<span class="metric-value mono">{docket?.candidates.length ?? 0}</span>
					<span class="metric-label chrome">public queue</span>
				</button>
			</div>
		</section>

		<button class="atlas-card" onclick={() => (atlasExpanded = true)} aria-label="open semantic atlas">
			<div class="card-top">
				<div>
					<div class="section-label chrome">semantic atlas</div>
					<div class="card-title">
						{atlas ? `${atlas.points.length.toLocaleString()} points` : 'atlas pending'}
					</div>
				</div>
				<div class="card-meta mono">
					{atlas ? `${atlas.clusters_coarse.length} regions` : 'syncing'}
				</div>
			</div>
			<div class="atlas-phone-map" aria-hidden="true">
				{#if atlasPreview.length > 0}
					{#each atlasPreview as p (p.id)}
						<span
							class="atlas-pixel"
							class:promoted={p.promotion_status === 'promoted'}
							style={`left:${p.sx}%;top:${p.sy}%;--dot:${p.color};`}
						></span>
					{/each}
					{#each atlasClusterPreview as cl (cl.id)}
						<span class="cluster-label chrome" style={`left:${cl.sx}%;top:${cl.sy}%;`}>
							{cl.label}
						</span>
					{/each}
				{:else}
					<div class="atlas-empty chrome">waiting for atlas</div>
				{/if}
			</div>
		</button>

		<section class="mobile-panel">
			<div class="card-top">
				<div>
					<div class="section-label chrome">active attention</div>
					<div class="panel-summary">{observations.length} observations currently carried</div>
				</div>
			</div>
			<div class="stack-list">
				{#each attentionPreview as obs (obs.rkey)}
					<button class="list-row" onclick={() => logbook.set({ kind: 'observation', observation: obs })}>
						<span class="row-rule"></span>
						<span class="row-body">{obs.content}</span>
					</button>
				{:else}
					<div class="empty-row chrome">no active observations</div>
				{/each}
			</div>
		</section>

		<section class="mobile-panel people-panel">
			<div class="card-top">
				<div>
					<div class="section-label chrome">people memory</div>
					<div class="panel-summary">{known.length} profiles with carried context</div>
				</div>
				<button class="mini-action chrome" onclick={() => logbook.set({ kind: 'store', store: 'memory', known, atlas })}>
					all
				</button>
			</div>
			<div class="people-strip" aria-label="people in memory">
				{#each knownPeople as handle (handle)}
					<button class="person-chip" onclick={() => logbook.set({ kind: 'handle', handle, engaged: true, payload: { handle } })}>
						{#if avatarFor(handle)}
							<img src={avatarFor(handle) ?? ''} alt="" />
						{:else}
							<span class="avatar-fallback"></span>
						{/if}
						<span>{compactHandle(handle)}</span>
					</button>
				{:else}
					<div class="empty-row chrome">memory graph pending</div>
				{/each}
			</div>
		</section>

		<section class="mobile-panel split-panel">
			<div>
				<div class="section-label chrome">intent</div>
				<div class="stack-list compact">
					{#each goalPreview as goal (goal.rkey)}
						<button class="list-row" onclick={() => logbook.set({ kind: 'goal', goal })}>
							<span class="row-body">{goal.title}</span>
						</button>
					{:else}
						<div class="empty-row chrome">no goals loaded</div>
					{/each}
				</div>
			</div>
			<div>
				<div class="section-label chrome">publication pressure</div>
				<div class="stack-list compact">
					{#each docketPreview as candidate (candidate.id)}
						<button
							class="list-row warm"
							onclick={() => {
								if (docket) logbook.set({ kind: 'docket-list', docket });
							}}
						>
							<span class="row-body">{candidate.title}</span>
						</button>
					{:else}
						<div class="empty-row chrome">docket pending</div>
					{/each}
				</div>
			</div>
		</section>
	</div>

	{#if W > 0 && H > 0}
		{@const atlasHit = atlasMiniRect()}
		<button
			class="canvas-hit"
			style={`left:${atlasHit.x}px;top:${atlasHit.y}px;width:${atlasHit.w}px;height:${atlasHit.h}px;`}
			aria-label="open semantic atlas"
			onclick={() => {
				if (atlas) atlasExpanded = true;
			}}
		></button>
		{#each storeRowRects() as row (row.title)}
			<button
				class="canvas-hit"
				style={`left:${row.rect.x}px;top:${row.rect.y}px;width:${row.rect.w}px;height:${row.rect.h}px;`}
				aria-label={row.title}
				onclick={() => {
					if (row.entry) logbook.set(row.entry);
				}}
			></button>
		{/each}
	{/if}
	{#if atlasExpanded && atlas}
		<AtlasOverlay {atlas} onClose={() => (atlasExpanded = false)} />
	{/if}
</div>

<style>
	.host {
		position: absolute;
		inset: 0;
	}

	.desktop-field {
		position: absolute;
		inset: 0;
	}

	canvas {
		display: block;
		touch-action: manipulation;
	}

	.canvas-hit {
		position: absolute;
		display: block;
		margin: 0;
		padding: 0;
		border: 0;
		background: transparent;
		color: transparent;
		cursor: pointer;
		touch-action: manipulation;
	}

	.canvas-hit:focus-visible {
		outline: 1px solid var(--hud-hot);
		outline-offset: 2px;
	}

	.mobile-mind {
		display: none;
	}

	@media (max-width: 760px) {
		.desktop-field,
		.canvas-hit {
			display: none;
		}

		.mobile-mind {
			position: absolute;
			inset: 0;
			display: flex;
			flex-direction: column;
			gap: 12px;
			padding: 126px 14px calc(74px + env(safe-area-inset-bottom));
			overflow-y: auto;
			-webkit-overflow-scrolling: touch;
			scrollbar-width: none;
		}

		.mobile-mind::-webkit-scrollbar {
			display: none;
		}

		.mobile-panel,
		.atlas-card {
			width: 100%;
			border: 1px solid rgba(74, 139, 154, 0.28);
			background:
				linear-gradient(180deg, rgba(18, 24, 34, 0.84), rgba(5, 8, 14, 0.9)),
				var(--bg-void);
			box-shadow:
				inset 0 1px 0 rgba(126, 192, 212, 0.06),
				0 18px 42px rgba(0, 0, 0, 0.18);
			clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px);
		}

		.mobile-panel {
			padding: 14px;
		}

		.overview {
			border-color: rgba(224, 144, 96, 0.28);
			background:
				radial-gradient(circle at 80% 0%, rgba(224, 144, 96, 0.14), transparent 42%),
				linear-gradient(180deg, rgba(18, 24, 34, 0.88), rgba(5, 8, 14, 0.92));
		}

		.section-label {
			color: var(--text-dim);
			font-size: 10px;
			letter-spacing: 0.18em;
		}

		.overview-copy {
			margin-top: 8px;
		}

		.overview-title {
			color: var(--hud-hot);
			font-size: 22px;
			line-height: 1;
			letter-spacing: 0.12em;
		}

		.overview-sub,
		.panel-summary {
			margin-top: 5px;
			color: var(--text-mid);
			font-size: 13px;
			line-height: 1.35;
		}

		.metric-grid {
			display: grid;
			grid-template-columns: repeat(4, minmax(0, 1fr));
			gap: 8px;
			margin-top: 14px;
		}

		.metric {
			min-width: 0;
			min-height: 64px;
			padding: 10px 6px;
			border: 1px solid rgba(74, 139, 154, 0.26);
			background: rgba(4, 7, 12, 0.52);
			color: var(--text);
			text-align: left;
		}

		.metric-warm {
			border-color: rgba(224, 144, 96, 0.34);
		}

		.metric-value {
			display: block;
			color: var(--scan-hot);
			font-size: 18px;
			line-height: 1;
		}

		.metric-label {
			display: block;
			margin-top: 8px;
			color: var(--text-dim);
			font-size: 9px;
			line-height: 1.05;
		}

		.atlas-card {
			display: flex;
			flex-direction: column;
			min-height: 300px;
			padding: 0;
			color: inherit;
			text-align: left;
			cursor: pointer;
			overflow: hidden;
		}

		.card-top {
			display: flex;
			align-items: flex-start;
			justify-content: space-between;
			gap: 12px;
			padding: 14px 14px 10px;
		}

		.card-title {
			margin-top: 4px;
			color: var(--text);
			font-size: 18px;
			line-height: 1.15;
		}

		.card-meta {
			color: var(--scan-hot);
			font-size: 11px;
			white-space: nowrap;
		}

		.atlas-phone-map {
			position: relative;
			height: 214px;
			margin: 0 10px 10px;
			overflow: hidden;
			border: 1px solid rgba(74, 139, 154, 0.26);
			background:
				linear-gradient(135deg, rgba(126, 192, 212, 0.055) 0 1px, transparent 1px 18px),
				radial-gradient(circle at 68% 48%, rgba(224, 144, 96, 0.16), transparent 36%),
				#050912;
		}

		.atlas-pixel {
			position: absolute;
			width: 2px;
			height: 2px;
			transform: translate(-50%, -50%);
			background: var(--dot);
			border-radius: 50%;
			opacity: 0.58;
			box-shadow: 0 0 5px color-mix(in srgb, var(--dot) 55%, transparent);
		}

		.atlas-pixel.promoted {
			width: 3px;
			height: 3px;
			opacity: 0.95;
		}

		.cluster-label {
			position: absolute;
			max-width: 132px;
			transform: translate(-50%, -50%);
			color: rgba(214, 210, 201, 0.78);
			font-size: 9px;
			line-height: 1;
			text-align: center;
			text-shadow: 0 1px 5px #000;
		}

		.atlas-empty {
			position: absolute;
			inset: 0;
			display: grid;
			place-items: center;
			color: var(--text-dim);
			font-size: 10px;
		}

		.stack-list {
			display: flex;
			flex-direction: column;
			gap: 8px;
			margin-top: 12px;
		}

		.stack-list.compact {
			margin-top: 8px;
		}

		.list-row,
		.person-chip,
		.mini-action {
			border: 1px solid rgba(74, 139, 154, 0.22);
			background: rgba(4, 7, 12, 0.42);
			color: inherit;
			font: inherit;
			text-align: left;
			cursor: pointer;
		}

		.list-row {
			display: grid;
			grid-template-columns: 3px 1fr;
			gap: 10px;
			min-height: 48px;
			padding: 10px 11px;
		}

		.list-row.warm {
			border-color: rgba(224, 144, 96, 0.28);
		}

		.row-rule {
			width: 3px;
			min-height: 100%;
			background: var(--scan-mid);
			box-shadow: 0 0 8px rgba(126, 192, 212, 0.28);
		}

		.row-body {
			min-width: 0;
			color: var(--text);
			font-size: 13px;
			line-height: 1.35;
			display: -webkit-box;
			line-clamp: 2;
			-webkit-line-clamp: 2;
			-webkit-box-orient: vertical;
			overflow: hidden;
		}

		.people-panel {
			padding-bottom: 10px;
		}

		.people-strip {
			display: flex;
			gap: 9px;
			margin: 12px -14px 0;
			padding: 0 14px 4px;
			overflow-x: auto;
			scrollbar-width: none;
			-webkit-overflow-scrolling: touch;
		}

		.people-strip::-webkit-scrollbar {
			display: none;
		}

		.person-chip {
			display: flex;
			align-items: center;
			gap: 8px;
			min-width: 142px;
			max-width: 168px;
			min-height: 44px;
			padding: 7px 9px;
			color: var(--text-mid);
			font-size: 12px;
		}

		.person-chip img,
		.avatar-fallback {
			width: 28px;
			height: 28px;
			flex: 0 0 28px;
			border-radius: 50%;
			border: 1px solid rgba(214, 210, 201, 0.55);
			background: var(--text-dim);
		}

		.person-chip span:last-child {
			min-width: 0;
			overflow: hidden;
			text-overflow: ellipsis;
			white-space: nowrap;
		}

		.mini-action {
			min-width: 44px;
			min-height: 32px;
			padding: 6px 10px;
			color: var(--scan-hot);
			font-size: 10px;
			text-align: center;
		}

		.split-panel {
			display: grid;
			grid-template-columns: 1fr;
			gap: 14px;
		}

		.empty-row {
			min-height: 44px;
			display: grid;
			place-items: center start;
			padding: 0 11px;
			border: 1px dashed rgba(74, 139, 154, 0.2);
			color: var(--text-dim);
			font-size: 10px;
		}
	}
</style>
