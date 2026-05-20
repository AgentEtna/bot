<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { PHI_HANDLE } from '$lib/api';
	import { hudReadout, logbook } from '$lib/state.svelte';
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
	let W = 0;
	let H = 0;
	let dpr = 1;
	let frameRequested = false;
	let hovered = $state<Hotspot | null>(null);
	let hotspots = $state<Hotspot[]>([]);
	let points = $state<AtlasPoint[]>([]);

	const imageCache = new Map<string, HTMLImageElement>();
	const imageLoading = new Set<string>();
	const imageFailed = new Set<string>();

	type Rect = { x: number; y: number; w: number; h: number };
	type Ring = 'self' | 'goals' | 'attention' | 'people' | 'horizon';
	type Hotspot = Rect & {
		label: string;
		readout: string;
		entry?: LogbookEntry;
		point?: AtlasPoint;
	};

	const rings: { key: Ring; r: number; label: string; metric: () => number }[] = [
		{ key: 'goals', r: 0.18, label: 'intent', metric: () => goals.length },
		{ key: 'attention', r: 0.32, label: 'attention', metric: () => observations.length },
		{ key: 'people', r: 0.55, label: 'people carried', metric: () => known.length },
		{ key: 'horizon', r: 0.82, label: 'horizon', metric: () => candidates.length }
	];

	function resolve(name: string): string {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
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
		const mx = mobile() ? 24 : 62;
		const top = mobile() ? 126 : 112;
		const bottom = mobile() ? 92 : 68;
		return { x: mx, y: top, w: W - mx * 2, h: H - top - bottom };
	}

	function center(): { x: number; y: number } {
		const f = field();
		return {
			x: mobile() ? f.x + f.w / 2 : f.x + f.w * 0.44,
			y: f.y + f.h * (mobile() ? 0.4 : 0.46)
		};
	}

	function unit(): number {
		const f = field();
		return Math.min(f.w, f.h) * (mobile() ? 0.41 : 0.43);
	}

	function worldToScreen(x: number, y: number): [number, number] {
		const c = center();
		const u = unit();
		return [c.x + x * u, c.y + y * u];
	}

	function sidePanel(): Rect {
		const f = field();
		if (mobile()) return { x: f.x, y: f.y + f.h - 160, w: f.w, h: 138 };
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
		chrome(ctx, 'living memory field', f.x, f.y - 24, 12);
		const stats = [
			`${goals.length} intent`,
			`${observations.length} attention`,
			`${known.length} people`,
			`${candidates.length} horizon`,
			`${docket?.candidates.length ?? 0} public candidates`,
			atlas ? `${atlas.points.length} atlas points` : 'atlas pending'
		];
		label(ctx, stats.join(' · '), f.x, f.y - 5, f.w, '--scan-mid', 12);
	}

	function drawRingLabels(ctx: CanvasRenderingContext2D) {
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
		if (p.kind === 'phi') return mobile() ? 23 : 29;
		if (p.kind === 'handle-engaged') return mobile() ? 8 : 10;
		if (p.kind === 'handle-candidate') return mobile() ? 5 : 6;
		if (p.kind === 'goal') return mobile() ? 7 : 8;
		return mobile() ? 5 : 6;
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

	function drawStores(ctx: CanvasRenderingContext2D) {
		const p = sidePanel();
		ctx.save();
		rounded(ctx, p, 8);
		ctx.fillStyle = 'rgba(9, 13, 20, 0.66)';
		ctx.fill();
		ctx.strokeStyle = 'rgba(74, 139, 154, 0.32)';
		ctx.stroke();
		chrome(ctx, 'under the field', p.x + 14, p.y + 24, 11);
		label(ctx, 'click a store to inspect what it carries', p.x + 14, p.y + 45, p.w - 28, '--text-dim', 11);

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
				entry: { kind: 'store', store: 'memory', known } as LogbookEntry
			},
			{
				title: 'atlas',
				value: atlas ? `${atlas.points.length} points · ${atlas.clusters_fine.length} fine clusters` : 'pending',
				entry: { kind: 'store', store: 'atlas', atlas } as LogbookEntry
			},
			{
				title: 'public candidates',
				value: `${docketCount} candidates from private evidence`,
				entry: docket ? ({ kind: 'docket-list', docket } as LogbookEntry) : undefined
			}
		];
		const rowH = mobile() ? 22 : 52;
		const gap = mobile() ? 7 : 10;
		let y = p.y + (mobile() ? 58 : 66);
		for (const row of rows) {
			const r = { x: p.x + 14, y, w: p.w - 28, h: rowH };
			rounded(ctx, r, 5);
			ctx.fillStyle = 'rgba(4, 7, 12, 0.42)';
			ctx.fill();
			ctx.strokeStyle = row.title === 'public candidates' ? 'rgba(224, 144, 96, 0.42)' : 'rgba(74, 139, 154, 0.22)';
			ctx.stroke();
			chrome(ctx, row.title, r.x + 10, r.y + (mobile() ? 15 : 18), 9);
			if (!mobile()) label(ctx, row.value, r.x + 10, r.y + 38, r.w - 20, '--text-mid', 11);
			hotspots.push({
				...r,
				label: row.title,
				readout: `${row.title} · ${row.value}`,
				entry: row.entry
			});
			y += rowH + gap;
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
			canvas.style.cursor = h?.entry ? 'pointer' : 'default';
			scheduleFrame();
		}
	}

	function onClick(e: MouseEvent) {
		const rect = canvas.getBoundingClientRect();
		const h = hit(e.clientX - rect.left, e.clientY - rect.top);
		if (h?.entry) logbook.set(h.entry);
	}

	let ro: ResizeObserver | null = null;
	function resize() {
		if (!canvas?.parentElement) return;
		const rect = canvas.parentElement.getBoundingClientRect();
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
		if (canvas?.parentElement) ro.observe(canvas.parentElement);
	});

	onDestroy(() => {
		ro?.disconnect();
		hudReadout.set('');
	});
</script>

<div class="host">
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

<style>
	.host {
		position: absolute;
		inset: 0;
	}

	canvas {
		display: block;
		touch-action: manipulation;
	}
</style>
