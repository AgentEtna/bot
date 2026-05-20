<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { hudReadout, logbook } from '$lib/state.svelte';
	import type {
		Atlas,
		Docket,
		DiscoveryEntry,
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
		docket: Docket | null;
		atlas: Atlas | null;
	}

	let { goals, observations, known, candidates, docket, atlas }: Props = $props();

	let canvas: HTMLCanvasElement;
	let W = 0;
	let H = 0;
	let dpr = 1;
	let frameRequested = false;
	let hovered = $state<Hotspot | null>(null);
	let hotspots = $state<Hotspot[]>([]);

	type Tone = 'run' | 'prompt' | 'memory' | 'trigger' | 'action' | 'docket';
	type Rect = { x: number; y: number; w: number; h: number };
	type Hotspot = Rect & {
		label: string;
		readout: string;
		tone: Tone;
		entry?: LogbookEntry;
	};

	const promptStrata = [
		'identity / time / relays',
		'goals + active observations',
		'recent operations + discovery',
		'author memory + episodic recall',
		'atlas + docket + public memory'
	];

	function resolve(name: string): string {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
	}

	function tone(t: Tone): string {
		if (t === 'run') return resolve('--hud-hot');
		if (t === 'prompt') return resolve('--warn');
		if (t === 'memory') return resolve('--scan-mid');
		if (t === 'trigger') return resolve('--text-mid');
		if (t === 'action') return resolve('--text');
		return resolve('--hud-mid');
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
		void docket;
		void atlas;
		scheduleFrame();
	});

	function bounds() {
		const mobile = W < 720;
		const padX = mobile ? 22 : 52;
		const top = mobile ? 116 : 110;
		const bottom = mobile ? 74 : 62;
		return {
			mobile,
			x: padX,
			y: top,
			w: Math.max(320, W - padX * 2),
			h: Math.max(420, H - top - bottom)
		};
	}

	function runRect(): Rect {
		const b = bounds();
		const w = b.mobile ? b.w * 0.78 : Math.min(420, b.w * 0.36);
		const h = b.mobile ? 82 : 110;
		return {
			x: b.x + (b.w - w) / 2,
			y: b.y + b.h * (b.mobile ? 0.38 : 0.36),
			w,
			h
		};
	}

	function promptRect(): Rect {
		const b = bounds();
		const r = runRect();
		return {
			x: b.mobile ? b.x : r.x - 34,
			y: b.y,
			w: b.mobile ? b.w : r.w + 68,
			h: b.mobile ? 142 : 154
		};
	}

	function memoryRect(): Rect {
		const b = bounds();
		const r = runRect();
		return {
			x: b.mobile ? b.x : r.x - 80,
			y: r.y + r.h + (b.mobile ? 34 : 56),
			w: b.mobile ? b.w : r.w + 160,
			h: b.mobile ? 118 : 132
		};
	}

	function triggerPoints() {
		const b = bounds();
		const r = runRect();
		const x = b.mobile ? b.x + 40 : b.x + Math.min(150, b.w * 0.13);
		const baseY = b.mobile ? r.y + 18 : r.y - 18;
		const gap = b.mobile ? (r.h - 36) / 2 : 50;
		return [
			{ label: 'notifications', x, y: baseY, readout: 'notifications · unread batch every poll cycle' },
			{ label: 'cycle', x, y: baseY + gap, readout: 'cycle · scheduled integrated musing/relay/workflow pass' },
			{ label: 'reflection', x, y: baseY + gap * 2, readout: 'reflection · daily memory and service-health pass' }
		];
	}

	function actionPoints() {
		const b = bounds();
		const r = runRect();
		const x = b.mobile ? b.x + b.w - 40 : b.x + b.w - Math.min(150, b.w * 0.13);
		const baseY = b.mobile ? r.y + 18 : r.y - 18;
		const gap = b.mobile ? (r.h - 36) / 2 : 50;
		return [
			{ label: 'post', x, y: baseY, readout: 'posting tools · reply, like, repost, top-level post' },
			{ label: 'state', x, y: baseY + gap, readout: 'state tools · observe, recall, goals, owner-gated mutations' },
			{ label: 'mcp', x, y: baseY + gap * 2, readout: 'MCP tools · PDS records, publication search, prefect detail' }
		];
	}

	function addHotspot(rect: Rect, toneName: Tone, label: string, readout: string, entry?: LogbookEntry) {
		hotspots.push({ ...rect, tone: toneName, label, readout, entry });
	}

	function rounded(ctx: CanvasRenderingContext2D, r: Rect, radius = 8) {
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

	function chrome(ctx: CanvasRenderingContext2D, label: string, x: number, y: number, size = 10) {
		ctx.font = `${size}px "Saira Condensed", sans-serif`;
		ctx.fillStyle = resolve('--text-dim');
		ctx.fillText(label.toUpperCase(), x, y);
	}

	function label(ctx: CanvasRenderingContext2D, value: string, x: number, y: number, maxW: number, color = '--text-mid', size = 12) {
		ctx.font = `${size}px "Inter", system-ui, sans-serif`;
		ctx.fillStyle = resolve(color);
		let out = value;
		while (ctx.measureText(out).width > maxW && out.length > 4) out = out.slice(0, -2);
		if (out !== value) out = `${out.slice(0, -1)}…`;
		ctx.fillText(out, x, y);
	}

	function drawBeam(ctx: CanvasRenderingContext2D, from: { x: number; y: number }, to: { x: number; y: number }, toneName: Tone, dashed = false) {
		ctx.save();
		ctx.strokeStyle = tone(toneName);
		ctx.globalAlpha = toneName === 'run' ? 0.5 : 0.28;
		ctx.lineWidth = toneName === 'run' ? 1.4 : 1;
		if (dashed) ctx.setLineDash([5, 7]);
		ctx.beginPath();
		const dx = (to.x - from.x) * 0.45;
		ctx.moveTo(from.x, from.y);
		ctx.bezierCurveTo(from.x + dx, from.y, to.x - dx, to.y, to.x, to.y);
		ctx.stroke();
		ctx.restore();
	}

	function drawNode(ctx: CanvasRenderingContext2D, x: number, y: number, nodeLabel: string, toneName: Tone, readout: string, entry?: LogbookEntry) {
		const r = bounds().mobile ? 16 : 19;
		ctx.save();
		ctx.strokeStyle = tone(toneName);
		ctx.fillStyle = 'rgba(13, 17, 25, 0.9)';
		ctx.lineWidth = 1.2;
		ctx.beginPath();
		for (let i = 0; i < 6; i++) {
			const a = -Math.PI / 2 + (i * Math.PI) / 3;
			const px = x + Math.cos(a) * r;
			const py = y + Math.sin(a) * r;
			if (i === 0) ctx.moveTo(px, py);
			else ctx.lineTo(px, py);
		}
		ctx.closePath();
		ctx.fill();
		ctx.stroke();
		if (!bounds().mobile) chrome(ctx, nodeLabel, x + r + 8, y + 4, 10);
		ctx.restore();
		addHotspot({ x: x - r, y: y - r, w: r * 2, h: r * 2 }, toneName, nodeLabel, readout, entry);
	}

	function drawPrompt(ctx: CanvasRenderingContext2D) {
		const p = promptRect();
		ctx.save();
		rounded(ctx, p, 10);
		ctx.fillStyle = 'rgba(201, 160, 90, 0.035)';
		ctx.fill();
		ctx.strokeStyle = 'rgba(201, 160, 90, 0.42)';
		ctx.stroke();
		chrome(ctx, 'prompt compositor', p.x + 14, p.y + 22, 11);
		label(ctx, 'dynamic blocks condense into one context surface', p.x + 14, p.y + 42, p.w - 28, '--text-dim', 11);

		const gap = 8;
		const barH = Math.max(13, (p.h - 64 - gap * (promptStrata.length - 1)) / promptStrata.length);
		let y = p.y + 56;
		for (let i = 0; i < promptStrata.length; i++) {
			const inset = 12 + i * 8;
			const bar = { x: p.x + inset, y, w: p.w - inset * 2, h: barH };
			rounded(ctx, bar, 3);
			ctx.fillStyle = `rgba(201, 160, 90, ${0.05 + i * 0.018})`;
			ctx.fill();
			ctx.strokeStyle = 'rgba(201, 160, 90, 0.34)';
			ctx.stroke();
			if (!bounds().mobile || i % 2 === 0) {
				label(ctx, promptStrata[i], bar.x + 9, bar.y + bar.h - 4, bar.w - 18, '--text-mid', 10);
			}
			addHotspot(bar, 'prompt', promptStrata[i], `[${promptStrata[i]}] prompt stratum`);
			y += barH + gap;
		}
		ctx.restore();
	}

	function drawRun(ctx: CanvasRenderingContext2D) {
		const r = runRect();
		ctx.save();
		const glow = ctx.createRadialGradient(r.x + r.w / 2, r.y + r.h / 2, 10, r.x + r.w / 2, r.y + r.h / 2, r.w * 0.7);
		glow.addColorStop(0, 'rgba(224, 144, 96, 0.2)');
		glow.addColorStop(1, 'rgba(224, 144, 96, 0)');
		ctx.fillStyle = glow;
		ctx.fillRect(r.x - r.w * 0.35, r.y - r.h, r.w * 1.7, r.h * 3);
		rounded(ctx, r, 12);
		ctx.fillStyle = 'rgba(184, 107, 58, 0.12)';
		ctx.fill();
		ctx.strokeStyle = resolve('--hud-hot');
		ctx.lineWidth = 1.6;
		ctx.stroke();
		chrome(ctx, 'agent.run()', r.x + 18, r.y + 30, 15);
		label(ctx, 'one loop, path-specific deps, tool calls inside the pass', r.x + 18, r.y + 58, r.w - 36, '--text-mid', 12);
		if (!bounds().mobile) {
			label(ctx, 'summary string only returns after the work is done', r.x + 18, r.y + 78, r.w - 36, '--text-dim', 11);
		}
		ctx.restore();
		addHotspot(r, 'run', 'agent.run', 'same agent loop; entry points differ by prompt and deps');
	}

	function drawMemory(ctx: CanvasRenderingContext2D) {
		const m = memoryRect();
		ctx.save();
		rounded(ctx, m, 10);
		ctx.fillStyle = 'rgba(74, 139, 154, 0.045)';
		ctx.fill();
		ctx.strokeStyle = 'rgba(74, 139, 154, 0.42)';
		ctx.stroke();
		chrome(ctx, 'memory substrate', m.x + 14, m.y + 22, 11);
		const parts = [
			{ name: 'PDS', value: `${goals.length} goals · ${observations.length} observations`, entry: goals[0] ? ({ kind: 'goal', goal: goals[0] } as LogbookEntry) : undefined },
			{ name: 'TPUF', value: `${known.length} people carried`, entry: known[0] ? ({ kind: 'handle', handle: known[0].label.replace(/^@/, ''), engaged: true, payload: {} } as LogbookEntry) : undefined },
			{ name: 'ATLAS', value: atlas ? `${atlas.points.length} points · ${atlas.clusters_fine.length} fine` : 'pending', entry: undefined }
		];
		const gap = 12;
		const cellW = (m.w - 28 - gap * (parts.length - 1)) / parts.length;
		for (let i = 0; i < parts.length; i++) {
			const cell = { x: m.x + 14 + i * (cellW + gap), y: m.y + 44, w: cellW, h: m.h - 58 };
			rounded(ctx, cell, 6);
			ctx.fillStyle = 'rgba(7, 9, 15, 0.38)';
			ctx.fill();
			ctx.strokeStyle = i === 2 ? 'rgba(184, 107, 58, 0.4)' : 'rgba(74, 139, 154, 0.28)';
			ctx.stroke();
			chrome(ctx, parts[i].name, cell.x + 10, cell.y + 21, 10);
			label(ctx, parts[i].value, cell.x + 10, cell.y + 44, cell.w - 20, '--text-mid', 11);
			addHotspot(cell, i === 2 ? 'docket' : 'memory', parts[i].name, `${parts[i].name} · ${parts[i].value}`, parts[i].entry);
		}
		ctx.restore();
	}

	function drawDocket(ctx: CanvasRenderingContext2D) {
		const b = bounds();
		const m = memoryRect();
		const count = docket?.candidates.length ?? 0;
		const r = {
			x: b.mobile ? m.x + m.w - 112 : m.x + m.w + 22,
			y: b.mobile ? m.y + 12 : m.y + 10,
			w: 98,
			h: 72
		};
		ctx.save();
		rounded(ctx, r, 8);
		ctx.fillStyle = 'rgba(184, 107, 58, 0.08)';
		ctx.fill();
		ctx.strokeStyle = 'rgba(224, 144, 96, 0.45)';
		ctx.stroke();
		chrome(ctx, 'docket', r.x + 12, r.y + 22, 11);
		ctx.font = '24px "JetBrains Mono", monospace';
		ctx.fillStyle = resolve('--hud-hot');
		ctx.fillText(String(count), r.x + 12, r.y + 52);
		label(ctx, 'pressure', r.x + 46, r.y + 49, 44, '--text-dim', 10);
		ctx.restore();
		const first = docket?.candidates[0];
		addHotspot(
			r,
			'docket',
			'docket',
			first ? `docket · ${count} candidates · ${first.title}` : 'docket · no candidates loaded',
			first ? { kind: 'docket', candidate: first } : undefined
		);
	}

	function drawActors(ctx: CanvasRenderingContext2D) {
		const r = runRect();
		for (const p of triggerPoints()) {
			drawBeam(ctx, { x: p.x + 20, y: p.y }, { x: r.x, y: r.y + r.h / 2 }, 'trigger', true);
			drawNode(ctx, p.x, p.y, p.label, 'trigger', p.readout);
		}
		for (const p of actionPoints()) {
			drawBeam(ctx, { x: r.x + r.w, y: r.y + r.h / 2 }, { x: p.x - 20, y: p.y }, 'action');
			drawNode(ctx, p.x, p.y, p.label, 'action', p.readout);
		}
	}

	function drawFeedback(ctx: CanvasRenderingContext2D) {
		const p = promptRect();
		const r = runRect();
		const m = memoryRect();
		drawBeam(ctx, { x: p.x + p.w / 2, y: p.y + p.h }, { x: r.x + r.w / 2, y: r.y }, 'prompt');
		drawBeam(ctx, { x: r.x + r.w / 2, y: r.y + r.h }, { x: m.x + m.w / 2, y: m.y }, 'memory');
		drawBeam(ctx, { x: m.x + m.w * 0.16, y: m.y }, { x: p.x + p.w * 0.12, y: p.y + p.h * 0.68 }, 'memory', true);
	}

	function drawHeader(ctx: CanvasRenderingContext2D) {
		const b = bounds();
		chrome(ctx, 'phi cognitive circuit', b.x, b.y - 24, 12);
		const stats = [
			`${goals.length} goals`,
			`${observations.length} observations`,
			`${known.length} people`,
			`${candidates.length} horizon`,
			`${docket?.candidates.length ?? 0} docket`,
			atlas ? `${atlas.points.length} atlas points` : 'atlas pending'
		];
		label(ctx, stats.join(' · '), b.x, b.y - 5, b.w, '--scan-mid', 12);
	}

	function drawBackdrop(ctx: CanvasRenderingContext2D) {
		const b = bounds();
		const cx = b.x + b.w / 2;
		const cy = b.y + b.h * 0.45;
		ctx.save();
		ctx.strokeStyle = resolve('--grid');
		ctx.lineWidth = 1;
		for (let i = 1; i <= 4; i++) {
			ctx.beginPath();
			ctx.ellipse(cx, cy, (b.w * i) / 8, (b.h * i) / 9, -0.08, 0, Math.PI * 2);
			ctx.stroke();
		}
		ctx.restore();
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
		drawPrompt(ctx);
		drawMemory(ctx);
		drawRun(ctx);
		drawFeedback(ctx);
		drawActors(ctx);
		drawDocket(ctx);
		if (hovered) drawHover(ctx, hovered);
		ctx.restore();
	}

	function drawHover(ctx: CanvasRenderingContext2D, h: Hotspot) {
		ctx.save();
		rounded(ctx, h, 8);
		ctx.strokeStyle = resolve('--hud-hot');
		ctx.lineWidth = 1.5;
		ctx.stroke();
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
