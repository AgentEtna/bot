<script lang="ts">
	import '../app.css';
	import Hud from '$lib/components/Hud.svelte';
	import OverrideBanner from '$lib/components/OverrideBanner.svelte';

	interface Props {
		children?: import('svelte').Snippet;
	}

	let { children }: Props = $props();
</script>

<div class="cockpit">
	<OverrideBanner />
	<div class="viewport">
		{@render children?.()}
	</div>
	<Hud />
</div>
