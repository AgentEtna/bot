<script lang="ts">
	import { page } from '$app/state';
</script>

<main class="page">
	<div class="page-inner errorbox">
		<div class="code">{page.status}</div>
		<p class="msg">
			{#if page.status === 404}
				nothing lives at <code>{page.url.pathname}</code>
			{:else}
				{page.error?.message ?? 'something went sideways'}
			{/if}
		</p>
		<a href="/">← back to the cockpit</a>
	</div>
</main>

<style>
	.page {
		position: fixed;
		inset: 0;
		overflow-y: auto;
		padding: 96px 16px 80px;
	}
	.page-inner {
		max-width: 720px;
		margin: 0 auto;
	}
	.errorbox {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.75rem;
		padding-top: 4rem;
	}
	.code {
		font-size: 3.5rem;
		font-weight: 200;
		opacity: 0.35;
		letter-spacing: 0.05em;
	}
	.msg {
		margin: 0;
		opacity: 0.8;
	}
	a {
		color: inherit;
		opacity: 0.7;
	}
	a:hover {
		opacity: 1;
	}
</style>
