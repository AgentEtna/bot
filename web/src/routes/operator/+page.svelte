<script lang="ts">
	import { onMount } from 'svelte';
	import { Agent } from '@atproto/api';
	import type { OAuthSession } from '@atproto/oauth-client-browser';
	import { initOAuth, OVERRIDE_COLLECTION } from '$lib/operator/oauth';
	import { fetchOverride, OPERATOR_DID, type Override } from '$lib/operator/override';

	let oauth = $state<Awaited<ReturnType<typeof initOAuth>> | null>(null);
	let session = $state<OAuthSession | null>(null);
	let handleInput = $state('');
	let status = $state('');
	let loaded = $state(false);

	// the operator's live override (what the bot actually obeys) — public read
	let live = $state<Override | null>(null);
	// editor state (writes to the signed-in user's OWN repo)
	let active = $state(false);
	let message = $state('');
	let saving = $state(false);

	const isOperator = $derived(session?.did === OPERATOR_DID);

	onMount(async () => {
		live = await fetchOverride();
		// NEVER throw out of init: a failed OAuth init degrades to signed-out.
		try {
			oauth = await initOAuth();
			const result = await oauth.init(); // parses OAuth return params
			if (result?.session) {
				session = result.session;
				await loadOwn();
			}
		} catch (err) {
			console.error('oauth init failed; continuing signed out:', err);
		}
		loaded = true;
	});

	async function loadOwn() {
		if (!session) return;
		const own = await fetchOverride(session.did);
		if (own) {
			active = own.active;
			message = own.message;
		}
	}

	async function signIn(e: SubmitEvent) {
		e.preventDefault();
		if (!oauth || !handleInput.trim()) return;
		status = 'taking you to sign in…';
		try {
			await oauth.signIn(handleInput.trim().replace(/^@/, ''));
		} catch (err) {
			status = `sign in failed: ${String(err)}`;
		}
	}

	async function signOut() {
		if (oauth && session) await oauth.revoke(session.did);
		session = null;
		status = '';
	}

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!session) return;
		saving = true;
		status = '';
		try {
			const agent = new Agent(session);
			await agent.com.atproto.repo.putRecord({
				repo: session.did,
				collection: OVERRIDE_COLLECTION,
				rkey: 'self',
				record: {
					$type: OVERRIDE_COLLECTION,
					active,
					message,
					updatedAt: new Date().toISOString()
				}
			});
			status = active ? 'override set — phi will see it within ~60s' : 'override lifted';
			if (isOperator) live = { active, message };
		} catch (err) {
			status = `save failed: ${String(err)}`;
		} finally {
			saving = false;
		}
	}
</script>

<main class="page">
	<div class="page-inner">
		<h1>operator override</h1>

		<p class="explainer">
			safe mode is an <code>{OVERRIDE_COLLECTION}</code> record on the <em>operator's</em> repo —
			public, inspectable, and honest. while active, phi's outward-facing tools (post / like /
			repost) refuse with the message below, and it renders as a banner in her system prompt.
			anyone can sign in and write this record to their own repo; the bot only obeys the
			operator's copy. repo ownership is the allowlist.
		</p>

		{#if live}
			<div class="live {live.active ? 'live-active' : ''}">
				<span class="live-label">live state (what phi obeys):</span>
				{#if live.active}
					<strong>override ACTIVE</strong>
					<blockquote>{live.message}</blockquote>
				{:else}
					inactive — phi is operating normally
				{/if}
			</div>
		{/if}

		{#if !loaded}
			<div class="status">loading…</div>
		{:else if !session}
			<form onsubmit={signIn} class="login">
				<input type="text" placeholder="your handle (e.g. zzstoatzz.io)" bind:value={handleInput} />
				<button type="submit">sign in with atproto</button>
			</form>
		{:else}
			<div class="session-row">
				signed in as <code>{session.did}</code>
				{#if !isOperator}
					<span class="warn">
						(not the operator — you can write this record to your own repo, but phi won't obey it)
					</span>
				{/if}
				<button class="linkish" onclick={signOut}>sign out</button>
			</div>

			<form onsubmit={save} class="editor">
				<label class="toggle">
					<input type="checkbox" bind:checked={active} />
					override active (safe mode)
				</label>
				<textarea
					rows="6"
					placeholder="your message to phi — why the override is on, and how to reach you. shown to her verbatim."
					bind:value={message}
				></textarea>
				<button type="submit" disabled={saving || (active && !message.trim())}>
					{saving ? 'saving…' : 'save override record'}
				</button>
			</form>
		{/if}

		{#if status}
			<div class="status">{status}</div>
		{/if}
	</div>
</main>

<style>
	.explainer {
		opacity: 0.75;
		max-width: 60ch;
		line-height: 1.5;
	}
	.live {
		border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
		padding: 0.75rem 1rem;
		margin: 1rem 0;
		border-radius: 4px;
	}
	.live-active {
		border-color: #e0a458;
		background: color-mix(in srgb, #e0a458 12%, transparent);
	}
	.live-label {
		display: block;
		font-size: 0.8em;
		opacity: 0.6;
		margin-bottom: 0.25rem;
	}
	.live blockquote {
		margin: 0.5rem 0 0;
		padding-left: 0.75rem;
		border-left: 2px solid currentColor;
		white-space: pre-wrap;
	}
	.login,
	.editor {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		max-width: 34rem;
		margin-top: 1rem;
	}
	.login input,
	.editor textarea {
		font: inherit;
		padding: 0.5rem;
		background: transparent;
		color: inherit;
		border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
		border-radius: 4px;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	button {
		font: inherit;
		padding: 0.5rem 0.9rem;
		cursor: pointer;
	}
	button.linkish {
		background: none;
		border: none;
		text-decoration: underline;
		padding: 0;
		margin-left: 0.75rem;
	}
	.session-row {
		margin: 1rem 0;
	}
	.warn {
		opacity: 0.7;
		font-size: 0.9em;
	}
	.status {
		margin-top: 1rem;
		opacity: 0.8;
	}
</style>
