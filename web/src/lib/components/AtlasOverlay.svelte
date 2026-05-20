<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { Atlas } from '$lib/types';

	interface Props {
		atlas: Atlas;
		onClose: () => void;
	}

	let { atlas, onClose }: Props = $props();

	type Point = Atlas['points'][number];
	type Cluster = Atlas['clusters_coarse'][number];
	type Bounds = { minX: number; maxX: number; minY: number; maxY: number; cx: number; cy: number };
	type Palette = { core: string; mid: string; edge: string };
	type PointLink = { href: string; label: string; note: string };

	let canvas: HTMLCanvasElement;
	let W = 0;
	let H = 0;
	let dpr = 1;
	let frameRequested = false;
	let bounds: Bounds | null = null;
	let scale = 1;
	let view = $state({ zoom: 1, panX: 0, panY: 0 });
	let hover = $state<Point | null>(null);
	let selected = $state<Point | null>(null);
	let dragging = false;
	let dragStart = { x: 0, y: 0, panX: 0, panY: 0 };
	let pinchStart: {
		distance: number;
		zoom: number;
		focalX: number;
		focalY: number;
	} | null = null;
	let moved = false;
	let ro: ResizeObserver | null = null;
	const activePointers = new Map<number, { x: number; y: number }>();

	const minZoom = 0.72;
	const maxZoom = 18;

	const palette: Record<string, Palette> = {
		'active-observation': { core: '#b6f4ff', mid: '#5aa9bc', edge: '#183b48' },
		observation: { core: '#8ed2e0', mid: '#4a8b9a', edge: '#163944' },
		interaction: { core: '#ff9c62', mid: '#b86b3a', edge: '#512912' },
		summary: { core: '#7bd3b1', mid: '#3c9f7c', edge: '#123f33' },
		episodic: { core: '#b9a6ff', mid: '#7762c8', edge: '#282252' },
		post: { core: '#f2c772', mid: '#c08a35', edge: '#5a3910' },
		blog: { core: '#ffd58b', mid: '#b98b42', edge: '#553714' },
		goal: { core: '#f6b85c', mid: '#c37d32', edge: '#5a3214' },
		note: { core: '#f0d27d', mid: '#c9a05a', edge: '#5c4720' },
		url: { core: '#90d087', mid: '#5d9a52', edge: '#274821' },
		'handle-engaged': { core: '#d6d2c9', mid: '#8c8579', edge: '#3d3932' },
		other: { core: '#9aa3ad', mid: '#626c76', edge: '#252c34' }
	};

	const atlasById = $derived.by(() => new Map(atlas.points.map((p) => [p.id, p])));

	function color(kind: string | undefined): Palette {
		return palette[kind ?? 'other'] ?? palette.other;
	}

	function pointKind(kind: string | undefined): string {
		return (kind ?? 'point').replaceAll('-', ' ');
	}

	function refs(p: Point): Record<string, unknown> {
		return typeof p.refs === 'object' && p.refs !== null ? (p.refs as Record<string, unknown>) : {};
	}

	function parseAtUri(uri: string): { authority: string; collection?: string; rkey?: string } | null {
		const match = uri.match(/^at:\/\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/);
		if (!match) return null;
		return {
			authority: match[1],
			collection: match[2],
			rkey: match[3]
		};
	}

	function recordViewer(uri: string): string {
		const parsed = parseAtUri(uri);
		if (!parsed?.collection || !parsed.rkey) return `https://pdsls.dev/${encodeURIComponent(uri)}`;
		return `https://pdsls.dev/at/${parsed.authority}/${parsed.collection}/${parsed.rkey}`;
	}

	function urlInLabel(p: Point): string | null {
		const match = String(p.label ?? '').match(/https?:\/\/[^\s)]+/);
		return match?.[0] ?? null;
	}

	function pointLinks(p: Point): PointLink[] {
		const r = refs(p);
		const links: PointLink[] = [];
		const handle = typeof r.handle === 'string' ? r.handle : null;
		const atUri = typeof r.at_uri === 'string' ? r.at_uri : null;
		const parsed = atUri ? parseAtUri(atUri) : null;
		if (parsed?.collection === 'app.bsky.feed.post' && parsed.rkey) {
			links.push({
				href: `https://bsky.app/profile/${parsed.authority}/post/${parsed.rkey}`,
				label: 'open post',
				note: 'public Bluesky record'
			});
		}
		if (handle) {
			links.push({
				href: `https://bsky.app/profile/${handle}`,
				label: 'open profile',
				note: p.kind === 'handle-engaged' ? 'this person' : 'source profile'
			});
		}
		const embedded = urlInLabel(p);
		if (embedded) {
			links.push({ href: embedded, label: 'open url', note: 'URL carried by card' });
		}
		if (atUri) {
			links.push({
				href: recordViewer(atUri),
				label: parsed?.collection === 'app.bsky.feed.post' ? 'raw record' : 'open record',
				note: parsed?.collection ?? 'AT Protocol record'
			});
		}
		return links.filter((link, index, arr) => arr.findIndex((other) => other.href === link.href) === index);
	}

	function pointFingerprint(p: Point): string {
		const r = refs(p);
		if (typeof r.tpuf_namespace === 'string' && typeof r.tpuf_id === 'string') return `${r.tpuf_namespace} · ${r.tpuf_id}`;
		if (typeof r.collection === 'string') return r.collection;
		if (typeof r.observation_count === 'number') return `${r.observation_count} observations`;
		return p.id ?? 'atlas point';
	}

	function dominantKind(kindCounts: Record<string, number> | undefined): string {
		let best = 'other';
		let count = -1;
		for (const [kind, n] of Object.entries(kindCounts ?? {})) {
			if (n > count) {
				best = kind;
				count = n;
			}
		}
		return best;
	}

	function rgba(hex: string, alpha: number): string {
		const h = hex.replace('#', '');
		const r = parseInt(h.slice(0, 2), 16);
		const g = parseInt(h.slice(2, 4), 16);
		const b = parseInt(h.slice(4, 6), 16);
		return `rgba(${r}, ${g}, ${b}, ${alpha})`;
	}

	function numericPoints(): Point[] {
		return atlas.points.filter((p) => typeof p.x === 'number' && typeof p.y === 'number');
	}

	function computeBounds(): Bounds | null {
		const pts = numericPoints();
		if (pts.length === 0) return null;
		let minX = Infinity;
		let maxX = -Infinity;
		let minY = Infinity;
		let maxY = -Infinity;
		for (const p of pts) {
			const x = p.x as number;
			const y = p.y as number;
			minX = Math.min(minX, x);
			maxX = Math.max(maxX, x);
			minY = Math.min(minY, y);
			maxY = Math.max(maxY, y);
		}
		const dx = Math.max(0.001, maxX - minX);
		const dy = Math.max(0.001, maxY - minY);
		minX -= dx * 0.08;
		maxX += dx * 0.08;
		minY -= dy * 0.08;
		maxY += dy * 0.08;
		return { minX, maxX, minY, maxY, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2 };
	}

	function updateScale() {
		if (!bounds) return;
		const sidePad = W < 760 ? 18 : 76;
		const topPad = W < 760 ? 78 : 74;
		const bottomPad = W < 760 ? 112 : 92;
		scale = Math.min(
			(W - sidePad * 2) / (bounds.maxX - bounds.minX),
			(H - topPad - bottomPad) / (bounds.maxY - bounds.minY)
		);
	}

	function dataToScreen(x: number, y: number): [number, number] {
		if (!bounds) return [0, 0];
		return [
			W / 2 + (x - bounds.cx + view.panX) * scale * view.zoom,
			H / 2 - (y - bounds.cy + view.panY) * scale * view.zoom
		];
	}

	function screenToData(x: number, y: number): [number, number] {
		if (!bounds) return [0, 0];
		return [
			(x - W / 2) / (scale * view.zoom) + bounds.cx - view.panX,
			-(y - H / 2) / (scale * view.zoom) + bounds.cy - view.panY
		];
	}

	function scheduleFrame() {
		if (!frameRequested) {
			frameRequested = true;
			requestAnimationFrame(draw);
		}
	}

	function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, max = 44) {
		const out = text.length > max ? `${text.slice(0, max - 1)}…` : text;
		ctx.lineWidth = 3;
		ctx.strokeStyle = 'rgba(2, 4, 8, 0.84)';
		ctx.strokeText(out, x, y);
		ctx.fillText(out, x, y);
	}

	function drawHalos(ctx: CanvasRenderingContext2D, clusters: Cluster[], alpha: number, radiusScale: number) {
		for (const cl of clusters) {
			if (typeof cl.x !== 'number' || typeof cl.y !== 'number') continue;
			const [x, y] = dataToScreen(cl.x, cl.y);
			const radius = Math.max(18, Math.min(140, Math.sqrt(cl.count ?? 1) * radiusScale * view.zoom));
			if (x + radius < 0 || x - radius > W || y + radius < 0 || y - radius > H) continue;
			const c = color(dominantKind(cl.kind_counts));
			const grad = ctx.createRadialGradient(x, y, 0, x, y, radius);
			grad.addColorStop(0, rgba(c.core, alpha * 1.7));
			grad.addColorStop(0.42, rgba(c.mid, alpha));
			grad.addColorStop(1, rgba(c.edge, 0));
			ctx.fillStyle = grad;
			ctx.beginPath();
			ctx.arc(x, y, radius, 0, Math.PI * 2);
			ctx.fill();
			if (view.zoom < 2.4 && (cl.count ?? 0) > 20) {
				ctx.strokeStyle = rgba(c.core, alpha * 1.45);
				ctx.lineWidth = 0.8;
				for (let i = 0.42; i <= 0.86; i += 0.22) {
					ctx.beginPath();
					ctx.ellipse(x, y, radius * i * 1.38, radius * i * 0.56, -0.18, 0, Math.PI * 2);
					ctx.stroke();
				}
			}
		}
	}

	function visibleBounds() {
		const [x1, y1] = screenToData(-40, H + 40);
		const [x2, y2] = screenToData(W + 40, -40);
		return {
			minX: Math.min(x1, x2),
			maxX: Math.max(x1, x2),
			minY: Math.min(y1, y2),
			maxY: Math.max(y1, y2)
		};
	}

	function drawConnections(ctx: CanvasRenderingContext2D) {
		if (view.zoom < 2.4) return;
		const pts = numericPoints();
		const vb = visibleBounds();
		const byCluster = new Map<number, Point[]>();
		for (const p of pts) {
			if ((p.x as number) < vb.minX || (p.x as number) > vb.maxX || (p.y as number) < vb.minY || (p.y as number) > vb.maxY) continue;
			const cluster = p.cluster_fine ?? -1;
			const arr = byCluster.get(cluster) ?? [];
			if (arr.length < 22) arr.push(p);
			byCluster.set(cluster, arr);
		}
		ctx.lineWidth = 0.5;
		ctx.globalAlpha = Math.min(0.24, (view.zoom - 2.4) / 8);
		for (const pts of byCluster.values()) {
			for (let i = 1; i < pts.length; i++) {
				const a = pts[i - 1];
				const b = pts[i];
				const [x1, y1] = dataToScreen(a.x as number, a.y as number);
				const [x2, y2] = dataToScreen(b.x as number, b.y as number);
				ctx.strokeStyle = rgba(color(a.kind).mid, 0.65);
				ctx.beginPath();
				ctx.moveTo(x1, y1);
				ctx.lineTo(x2, y2);
				ctx.stroke();
			}
		}
		ctx.globalAlpha = 1;
	}

	function pointRadius(p: Point): number {
		const promoted = p.promotion_status === 'promoted';
		const base = view.zoom < 2 ? 1.05 : Math.min(4.6, 1.15 + view.zoom * 0.3);
		if (p.kind === 'handle-engaged') return base * (promoted ? 2.05 : 1.72);
		if (p.kind === 'goal' || p.kind === 'active-observation') return base * 2.1;
		if (p.kind === 'summary' || p.kind === 'blog') return base * 1.55;
		return promoted ? base * 1.4 : base;
	}

	function polygon(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, sides: number, rotation = 0) {
		ctx.beginPath();
		for (let i = 0; i < sides; i++) {
			const a = rotation + (Math.PI * 2 * i) / sides;
			const px = x + Math.cos(a) * r;
			const py = y + Math.sin(a) * r;
			if (i === 0) ctx.moveTo(px, py);
			else ctx.lineTo(px, py);
		}
		ctx.closePath();
	}

	function drawGlyph(ctx: CanvasRenderingContext2D, p: Point, x: number, y: number) {
		const c = color(p.kind);
		const promoted = p.promotion_status === 'promoted';
		const r = pointRadius(p);
		const hot = promoted || p.kind === 'active-observation' || p.kind === 'goal';
		ctx.globalAlpha = hot ? 0.95 : 0.58;
		ctx.fillStyle = hot ? c.core : c.mid;
		ctx.strokeStyle = hot ? rgba(c.core, 0.95) : rgba(c.mid, 0.72);
		ctx.lineWidth = Math.max(0.75, Math.min(1.4, r * 0.24));
		if (view.zoom < 1.35 && p.kind !== 'handle-engaged' && p.kind !== 'goal') {
			ctx.fillRect(x - r, y - r, r * 2, r * 2);
			return;
		}
		switch (p.kind) {
			case 'observation':
			case 'active-observation':
				polygon(ctx, x, y, r * 1.15, 4, Math.PI / 4);
				ctx.fill();
				if (p.kind === 'active-observation') ctx.stroke();
				break;
			case 'interaction':
				ctx.beginPath();
				ctx.arc(x, y, r * 1.2, 0, Math.PI * 2);
				ctx.stroke();
				ctx.beginPath();
				ctx.arc(x, y, r * 0.42, 0, Math.PI * 2);
				ctx.fill();
				break;
			case 'summary':
				polygon(ctx, x, y, r * 1.18, 6, Math.PI / 6);
				ctx.fill();
				break;
			case 'episodic':
				polygon(ctx, x, y + r * 0.08, r * 1.25, 3, -Math.PI / 2);
				ctx.fill();
				break;
			case 'post':
				ctx.beginPath();
				ctx.moveTo(x - r * 1.35, y + r * 0.35);
				ctx.lineTo(x + r * 1.15, y - r * 0.55);
				ctx.lineTo(x + r * 0.45, y + r * 0.72);
				ctx.closePath();
				ctx.fill();
				break;
			case 'blog':
			case 'note':
				ctx.fillRect(x - r * 1.05, y - r * 0.75, r * 2.1, r * 1.5);
				if (p.kind === 'blog') ctx.strokeRect(x - r * 1.05, y - r * 0.75, r * 2.1, r * 1.5);
				break;
			case 'url':
				ctx.beginPath();
				ctx.moveTo(x - r * 1.25, y);
				ctx.lineTo(x + r * 1.25, y);
				ctx.moveTo(x, y - r * 1.25);
				ctx.lineTo(x, y + r * 1.25);
				ctx.stroke();
				break;
			case 'handle-engaged':
				ctx.beginPath();
				ctx.arc(x, y, r * 1.1, 0, Math.PI * 2);
				ctx.stroke();
				ctx.beginPath();
				ctx.arc(x, y, r * 0.62, 0, Math.PI * 2);
				ctx.fill();
				break;
			case 'goal':
				polygon(ctx, x, y, r * 1.2, 6, Math.PI / 6);
				ctx.fill();
				ctx.stroke();
				break;
			default:
				ctx.beginPath();
				ctx.arc(x, y, r, 0, Math.PI * 2);
				ctx.fill();
		}
	}

	function drawPoints(ctx: CanvasRenderingContext2D) {
		const vb = visibleBounds();
		for (const p of atlas.points) {
			if (typeof p.x !== 'number' || typeof p.y !== 'number') continue;
			if (p.x < vb.minX || p.x > vb.maxX || p.y < vb.minY || p.y > vb.maxY) continue;
			const [x, y] = dataToScreen(p.x, p.y);
			drawGlyph(ctx, p, x, y);
		}
		ctx.globalAlpha = 1;
	}

	function drawClusterLabels(ctx: CanvasRenderingContext2D) {
		const coarseAlpha = Math.max(0, 1 - Math.max(0, view.zoom - 1.55) / 0.8);
		const fineStart = W < 760 ? 2.35 : 1.55;
		const fineAlpha = Math.min(1, Math.max(0, (view.zoom - fineStart) / 0.8)) * Math.max(0, 1 - Math.max(0, view.zoom - 5) / 1.2);
		const placed: { x: number; y: number; w: number; h: number }[] = [];
		const canPlace = (x: number, y: number, w: number, h: number) => {
			const box = { x: x - w / 2, y: y - h / 2, w, h };
			for (const p of placed) {
				if (box.x < p.x + p.w + 12 && box.x + box.w + 12 > p.x && box.y < p.y + p.h + 8 && box.y + box.h + 8 > p.y) return false;
			}
			placed.push(box);
			return true;
		};
		const drawSet = (clusters: Cluster[], alpha: number, size: number, max: number) => {
			if (alpha <= 0.02) return;
			ctx.font = `${size}px "Saira Condensed", sans-serif`;
			ctx.textAlign = 'center';
			ctx.textBaseline = 'middle';
			ctx.fillStyle = `rgba(214, 210, 201, ${0.82 * alpha})`;
			const sorted = [...clusters].sort((a, b) => (b.count ?? 0) - (a.count ?? 0));
			let drawn = 0;
			for (const cl of sorted) {
				if (drawn >= max) break;
				if (!cl.label || typeof cl.x !== 'number' || typeof cl.y !== 'number') continue;
				const [x, y] = dataToScreen(cl.x, cl.y);
				if (x < 28 || x > W - 28 || y < (W < 760 ? 94 : 56) || y > H - 140) continue;
				const maxChars = W < 760 ? 22 : 36;
				const raw = cl.label.toUpperCase();
				const text = raw.length > maxChars ? `${raw.slice(0, maxChars - 1)}…` : raw;
				const tw = ctx.measureText(text).width;
				if (!canPlace(x, y, tw, size + 4)) continue;
				label(ctx, text, x, y, maxChars);
				drawn++;
			}
		};
		drawSet(atlas.clusters_coarse, coarseAlpha, W < 760 ? 8 : 13, W < 760 ? 2 : 18);
		drawSet(atlas.clusters_fine, fineAlpha, W < 760 ? 8 : 11, W < 760 ? 4 : 34);
	}

	function drawPointLabels(ctx: CanvasRenderingContext2D) {
		if (view.zoom < 5.2) return;
		const alpha = Math.min(0.82, (view.zoom - 5.2) / 2);
		const vb = visibleBounds();
		const maxLabels = W < 760 ? 20 : 48;
		let drawn = 0;
		ctx.font = `${W < 760 ? 10 : 11}px "Inter", system-ui, sans-serif`;
		ctx.textAlign = 'center';
		ctx.textBaseline = 'bottom';
		ctx.fillStyle = `rgba(214, 210, 201, ${alpha})`;
		for (const p of atlas.points) {
			if (drawn >= maxLabels) return;
			if (!p.label || typeof p.x !== 'number' || typeof p.y !== 'number') continue;
			if (p.x < vb.minX || p.x > vb.maxX || p.y < vb.minY || p.y > vb.maxY) continue;
			const [x, y] = dataToScreen(p.x, p.y);
			label(ctx, p.label, x, y - 8, W < 760 ? 26 : 38);
			drawn++;
		}
	}

	function drawReticle(ctx: CanvasRenderingContext2D, p: Point) {
		if (typeof p.x !== 'number' || typeof p.y !== 'number') return;
		const [x, y] = dataToScreen(p.x, p.y);
		const r = Math.max(12, pointRadius(p) + 8);
		ctx.strokeStyle = p === selected ? '#ff9c62' : '#8ed2e0';
		ctx.lineWidth = 1.2;
		for (const [sx, sy] of [
			[-1, -1],
			[1, -1],
			[-1, 1],
			[1, 1]
		]) {
			ctx.beginPath();
			ctx.moveTo(x + sx * r, y + sy * (r + 7));
			ctx.lineTo(x + sx * r, y + sy * r);
			ctx.lineTo(x + sx * (r + 7), y + sy * r);
			ctx.stroke();
		}
	}

	function selectNeighbor(id: string) {
		const p = atlasById.get(id);
		if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') return;
		selected = p;
		focusDataAtScreen(p.x, p.y, W * 0.52, H * 0.48);
		view.zoom = Math.max(view.zoom, 4.2);
		scheduleFrame();
	}

	function draw() {
		frameRequested = false;
		if (!canvas) return;
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		ctx.save();
		ctx.scale(dpr, dpr);
		ctx.clearRect(0, 0, W, H);
		const bg = ctx.createRadialGradient(W * 0.52, H * 0.48, 0, W * 0.52, H * 0.48, Math.max(W, H) * 0.7);
		bg.addColorStop(0, 'rgba(19, 29, 34, 0.96)');
		bg.addColorStop(0.46, 'rgba(7, 11, 18, 0.98)');
		bg.addColorStop(1, 'rgba(2, 4, 8, 1)');
		ctx.fillStyle = bg;
		ctx.fillRect(0, 0, W, H);
		ctx.strokeStyle = 'rgba(126, 192, 212, 0.035)';
		for (let y = 0; y < H; y += 4) {
			ctx.beginPath();
			ctx.moveTo(0, y);
			ctx.lineTo(W, y);
			ctx.stroke();
		}
		const haloSet = view.zoom < 1.9 ? atlas.clusters_coarse : atlas.clusters_fine;
		drawHalos(ctx, haloSet, view.zoom < 1.9 ? 0.065 : 0.045, view.zoom < 1.9 ? 4.4 : 3.2);
		drawConnections(ctx);
		drawPoints(ctx);
		drawClusterLabels(ctx);
		drawPointLabels(ctx);
		if (hover) drawReticle(ctx, hover);
		if (selected && selected !== hover) drawReticle(ctx, selected);
		ctx.restore();
	}

	function nearest(x: number, y: number): Point | null {
		let best: Point | null = null;
		let bestD = Infinity;
		const coarseTouch = W < 760 ? 24 : 10;
		const maxD = Math.max(coarseTouch, (W < 760 ? 34 : 18) / Math.max(0.6, view.zoom));
		for (const p of atlas.points) {
			if (typeof p.x !== 'number' || typeof p.y !== 'number') continue;
			const [sx, sy] = dataToScreen(p.x, p.y);
			const d = Math.hypot(x - sx, y - sy);
			if (d < bestD && d <= maxD) {
				best = p;
				bestD = d;
			}
		}
		return best;
	}

	function onWheel(e: WheelEvent) {
		e.preventDefault();
		const rect = canvas.getBoundingClientRect();
		const mx = e.clientX - rect.left;
		const my = e.clientY - rect.top;
		const before = screenToData(mx, my);
		const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY;
		view.zoom = Math.max(minZoom, Math.min(maxZoom, view.zoom * Math.pow(0.995, dy)));
		const after = screenToData(mx, my);
		view.panX += after[0] - before[0];
		view.panY += after[1] - before[1];
		scheduleFrame();
	}

	function pointerMidpoint(): { x: number; y: number } | null {
		const pointers = [...activePointers.values()];
		if (pointers.length < 2) return null;
		return {
			x: (pointers[0].x + pointers[1].x) / 2,
			y: (pointers[0].y + pointers[1].y) / 2
		};
	}

	function pointerDistance(): number {
		const pointers = [...activePointers.values()];
		if (pointers.length < 2) return 0;
		return Math.hypot(pointers[0].x - pointers[1].x, pointers[0].y - pointers[1].y);
	}

	function startPinch() {
		const mid = pointerMidpoint();
		if (!mid) return;
		const [focalX, focalY] = screenToData(mid.x, mid.y);
		pinchStart = {
			distance: Math.max(1, pointerDistance()),
			zoom: view.zoom,
			focalX,
			focalY
		};
		dragging = false;
	}

	function focusDataAtScreen(dataX: number, dataY: number, screenX: number, screenY: number) {
		if (!bounds) return;
		view.panX = (screenX - W / 2) / (scale * view.zoom) + bounds.cx - dataX;
		view.panY = -(screenY - H / 2) / (scale * view.zoom) + bounds.cy - dataY;
	}

	function onPointerDown(e: PointerEvent) {
		e.preventDefault();
		canvas.setPointerCapture(e.pointerId);
		const rect = canvas.getBoundingClientRect();
		activePointers.set(e.pointerId, { x: e.clientX - rect.left, y: e.clientY - rect.top });
		moved = false;
		if (activePointers.size >= 2) {
			startPinch();
			return;
		}
		dragging = true;
		dragStart = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY };
	}

	function onPointerMove(e: PointerEvent) {
		const rect = canvas.getBoundingClientRect();
		if (activePointers.has(e.pointerId)) {
			activePointers.set(e.pointerId, { x: e.clientX - rect.left, y: e.clientY - rect.top });
		}
		if (activePointers.size >= 2 && pinchStart) {
			const mid = pointerMidpoint();
			const distance = pointerDistance();
			if (!mid || distance <= 0) return;
			view.zoom = Math.max(minZoom, Math.min(maxZoom, pinchStart.zoom * (distance / pinchStart.distance)));
			focusDataAtScreen(pinchStart.focalX, pinchStart.focalY, mid.x, mid.y);
			moved = true;
			hover = null;
			scheduleFrame();
			return;
		}
		if (dragging) {
			const dx = e.clientX - dragStart.x;
			const dy = e.clientY - dragStart.y;
			moved = moved || Math.hypot(dx, dy) > 4;
			view.panX = dragStart.panX + dx / (scale * view.zoom);
			view.panY = dragStart.panY - dy / (scale * view.zoom);
			hover = null;
			scheduleFrame();
			return;
		}
		const p = nearest(e.clientX - rect.left, e.clientY - rect.top);
		if (p !== hover) {
			hover = p;
			scheduleFrame();
		}
	}

	function onPointerUp(e: PointerEvent) {
		if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
		activePointers.delete(e.pointerId);
		if (activePointers.size >= 2) {
			startPinch();
			return;
		}
		pinchStart = null;
		dragging = activePointers.size === 1;
		const rect = canvas.getBoundingClientRect();
		const p = nearest(e.clientX - rect.left, e.clientY - rect.top);
		if (!moved && p) selected = p;
		if (dragging) {
			const remaining = [...activePointers.values()][0];
			dragStart = {
				x: remaining.x + rect.left,
				y: remaining.y + rect.top,
				panX: view.panX,
				panY: view.panY
			};
		}
		scheduleFrame();
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
		if (e.key === '0') {
			view = { zoom: 1, panX: 0, panY: 0 };
			scheduleFrame();
		}
	}

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
		updateScale();
		scheduleFrame();
	}

	onMount(() => {
		bounds = computeBounds();
		resize();
		ro = new ResizeObserver(resize);
		if (canvas?.parentElement) ro.observe(canvas.parentElement);
		window.addEventListener('keydown', handleKey);
	});

	onDestroy(() => {
		ro?.disconnect();
		activePointers.clear();
		window.removeEventListener('keydown', handleKey);
	});
</script>

<div class="atlas-shell" role="dialog" aria-label="semantic atlas" aria-modal="true">
	<canvas
		bind:this={canvas}
		onwheel={onWheel}
		onpointerdown={onPointerDown}
		onpointermove={onPointerMove}
		onpointerup={onPointerUp}
		onpointercancel={onPointerUp}
		onpointerleave={() => {
			if (!dragging && activePointers.size === 0) {
				hover = null;
				scheduleFrame();
			}
		}}
	></canvas>

	<header class="atlas-hud">
		<div>
			<div class="chrome">semantic atlas</div>
			<div class="meta mono">
				{atlas.points.length} points · {atlas.clusters_coarse.length} regions · {atlas.clusters_fine.length} clusters · {view.zoom.toFixed(1)}x
			</div>
		</div>
		<button class="close chrome" onclick={onClose}>close<span class="esc-hint"> · esc</span></button>
	</header>

	<div class="legend" aria-label="atlas point kinds">
		{#each Object.entries(palette).filter(([kind]) => kind !== 'other') as [kind, swatch]}
			<div class="legend-item">
				<span class={`dot glyph-${kind}`} style={`--dot: ${swatch.core}`}></span>
				<span>{pointKind(kind)}</span>
			</div>
		{/each}
	</div>

	{#if hover || selected}
		{@const p = hover ?? selected}
		{#if p}
			{@const links = pointLinks(p)}
			{@const neighbors = (p.neighbor_ids as string[] | undefined)?.filter((id) => atlasById.has(id)).slice(0, 5) ?? []}
			<div class="readout">
				<div class="readout-top">
					<div class="readout-kind chrome">{pointKind(p.kind)}</div>
					{#if selected === p}
						<div class="selected-mark chrome">selected</div>
					{/if}
				</div>
				<div class="readout-title">{p.label ?? p.id ?? 'untitled point'}</div>
				<div class="readout-meta mono">
					{#if p.promotion_status}{p.promotion_status} · {/if}
					{#if p.cluster_coarse != null}region {p.cluster_coarse}{/if}
					{#if p.cluster_fine != null} · cluster {p.cluster_fine}{/if}
				</div>
				<div class="readout-id mono">{pointFingerprint(p)}</div>
				{#if p.tags?.length}
					<div class="readout-tags mono">{p.tags.slice(0, 6).join(' · ')}</div>
				{/if}
				{#if links.length}
					<div class="link-row">
						{#each links as link (link.href)}
							<a class="source-link" href={link.href} target="_blank" rel="noreferrer">
								<span>{link.label}</span>
								<small>{link.note}</small>
							</a>
						{/each}
					</div>
				{:else}
					<div class="private-note">private memory point · no public entity link</div>
				{/if}
				{#if neighbors.length}
					<div class="neighbors">
						<div class="chrome neighbor-label">nearby</div>
						<div class="neighbor-row">
							{#each neighbors as id}
								<button class="neighbor-chip" onclick={() => selectNeighbor(id)}>
									{atlasById.get(id)?.kind ?? 'point'}
								</button>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>

<style>
	.atlas-shell {
		position: fixed;
		inset: 0;
		z-index: 70;
		background: #020408;
		color: var(--text);
		animation: bloom 180ms ease-out;
		overflow: hidden;
		overscroll-behavior: none;
	}

	canvas {
		display: block;
		width: 100%;
		height: 100%;
		cursor: grab;
		touch-action: none;
	}

	canvas:active {
		cursor: grabbing;
	}

	.atlas-hud {
		position: absolute;
		top: 18px;
		left: 20px;
		right: 20px;
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 18px;
		pointer-events: none;
	}

	.atlas-hud > * {
		pointer-events: auto;
	}

	.chrome {
		font-family: var(--font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--scan-hot);
		font-size: 11px;
	}

	.meta {
		margin-top: 4px;
		color: var(--scan-mid);
		font-size: 11px;
	}

	.close {
		padding: 7px 10px;
		border: 1px solid rgba(224, 144, 96, 0.36);
		background: rgba(7, 9, 15, 0.74);
		color: var(--scan-hot);
		cursor: pointer;
	}

	.legend,
	.readout {
		position: absolute;
		border: 1px solid rgba(74, 139, 154, 0.28);
		background: rgba(4, 7, 12, 0.74);
		backdrop-filter: blur(10px);
	}

	.legend {
		left: 20px;
		bottom: 18px;
		display: flex;
		flex-wrap: wrap;
		gap: 8px 12px;
		max-width: min(620px, calc(100vw - 40px));
		padding: 10px 12px;
		color: var(--text-dim);
		font-size: 10px;
	}

	.legend-item {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}

	.dot {
		position: relative;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--dot);
		box-shadow: 0 0 10px var(--dot);
	}

	.glyph-observation,
	.glyph-active-observation {
		border-radius: 1px;
		transform: rotate(45deg);
	}

	.glyph-interaction,
	.glyph-handle-engaged {
		background: transparent;
		border: 1px solid var(--dot);
	}

	.glyph-summary,
	.glyph-goal {
		clip-path: polygon(25% 0, 75% 0, 100% 50%, 75% 100%, 25% 100%, 0 50%);
	}

	.glyph-episodic {
		clip-path: polygon(50% 0, 100% 100%, 0 100%);
	}

	.glyph-post {
		clip-path: polygon(0 70%, 100% 20%, 66% 100%);
	}

	.glyph-note,
	.glyph-blog {
		border-radius: 0;
	}

	.glyph-url {
		width: 9px;
		height: 1px;
		border-radius: 0;
	}

	.readout {
		right: 20px;
		bottom: 18px;
		width: min(440px, calc(100vw - 40px));
		padding: 14px 16px;
	}

	.readout-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}

	.readout-kind {
		font-size: 9px;
		color: var(--text-dim);
	}

	.selected-mark {
		color: var(--scan-mid);
		font-size: 8px;
	}

	.readout-title {
		margin-top: 6px;
		font-size: 14px;
		line-height: 1.45;
		color: var(--text);
	}

	.readout-meta,
	.readout-tags,
	.readout-id {
		margin-top: 8px;
		color: var(--scan-mid);
		font-size: 10px;
	}

	.readout-id {
		color: var(--text-dim);
		word-break: break-word;
	}

	.link-row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
		gap: 8px;
		margin-top: 13px;
	}

	.source-link {
		display: grid;
		gap: 2px;
		padding: 9px 10px;
		border: 1px solid rgba(224, 144, 96, 0.34);
		background:
			linear-gradient(180deg, rgba(224, 144, 96, 0.1), rgba(4, 7, 12, 0.34)),
			rgba(4, 7, 12, 0.55);
		color: var(--scan-hot);
		text-decoration: none;
		text-transform: uppercase;
		letter-spacing: 0.09em;
		font-family: var(--font-chrome);
		font-size: 10px;
	}

	.source-link small {
		color: var(--text-dim);
		font-family: var(--font-ui);
		font-size: 10px;
		letter-spacing: 0;
		text-transform: none;
	}

	.private-note {
		margin-top: 12px;
		padding-left: 10px;
		border-left: 1px solid rgba(126, 192, 212, 0.42);
		color: var(--text-dim);
		font-size: 11px;
	}

	.neighbors {
		margin-top: 13px;
	}

	.neighbor-label {
		color: var(--text-dim);
		font-size: 8px;
	}

	.neighbor-row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 7px;
	}

	.neighbor-chip {
		padding: 6px 8px;
		border: 1px solid rgba(74, 139, 154, 0.28);
		background: rgba(7, 12, 18, 0.58);
		color: var(--scan-mid);
		font: inherit;
		font-size: 10px;
		text-transform: uppercase;
		cursor: pointer;
	}

	@media (max-width: 760px) {
		.atlas-hud {
			top: max(10px, env(safe-area-inset-top));
			left: 10px;
			right: 10px;
			align-items: center;
			gap: 10px;
		}

		.chrome {
			font-size: 10px;
			letter-spacing: 0.13em;
		}

		.meta {
			font-size: 10px;
			max-width: calc(100vw - 108px);
			white-space: normal;
			overflow: hidden;
			line-height: 1.3;
		}

		.close {
			min-width: 44px;
			min-height: 36px;
			padding: 6px 9px;
		}

		.esc-hint {
			display: none;
		}

		.legend {
			left: 10px;
			right: 10px;
			bottom: max(10px, env(safe-area-inset-bottom));
			max-width: none;
			flex-wrap: nowrap;
			overflow-x: auto;
			overflow-y: hidden;
			padding: 8px 10px;
			scrollbar-width: none;
			-webkit-overflow-scrolling: touch;
		}

		.legend::-webkit-scrollbar {
			display: none;
		}

		.readout {
			left: 10px;
			right: 10px;
			bottom: calc(max(10px, env(safe-area-inset-bottom)) + 48px);
			width: auto;
			max-height: min(42vh, 310px);
			overflow: auto;
			padding: 12px 13px;
		}

		.readout-title {
			font-size: 13px;
			line-height: 1.35;
		}

		.link-row {
			grid-template-columns: 1fr 1fr;
		}
	}

	@keyframes bloom {
		from {
			opacity: 0;
			transform: scale(0.985);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}
</style>
