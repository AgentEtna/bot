// atproto OAuth for the operator page — the doodl house pattern
// (@atproto/oauth-client-browser, static client metadata, granular scopes,
// slingshot for handle resolution; no bsky appview anywhere).
//
// authz model: anyone can sign in and write an io.zzstoatzz.phi.override
// record — to their OWN repo. the bot only reads the operator's copy
// (owner_did in bot config), so repo ownership is the allowlist.
import { BrowserOAuthClient } from '@atproto/oauth-client-browser';
import type { OAuthClientMetadataInput } from '@atproto/oauth-client-browser';

export const OVERRIDE_COLLECTION = 'io.zzstoatzz.phi.override';

export const SCOPE = `atproto repo:${OVERRIDE_COLLECTION}?action=create&action=update`;

const PROD_ORIGIN = 'https://phi.zzstoatzz.io';

function redirectUri(): string {
	return `${location.origin}/operator`;
}

function clientMetadata(): OAuthClientMetadataInput {
	const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';

	if (isLocal) {
		// atproto loopback dev client: config is derived from the client_id
		// query string; no hosted document required.
		const clientId =
			`http://localhost?redirect_uri=${encodeURIComponent(redirectUri())}` +
			`&scope=${encodeURIComponent(SCOPE)}`;
		return {
			client_id: clientId,
			client_name: 'phi cockpit (dev)',
			redirect_uris: [redirectUri()],
			scope: SCOPE,
			grant_types: ['authorization_code', 'refresh_token'],
			response_types: ['code'],
			token_endpoint_auth_method: 'none',
			application_type: 'web',
			dpop_bound_access_tokens: true
		};
	}

	// production: MUST match static/oauth-client-metadata.json, which the
	// auth server fetches from the client_id URL.
	return {
		client_id: `${PROD_ORIGIN}/oauth-client-metadata.json`,
		client_name: 'phi cockpit',
		client_uri: `${PROD_ORIGIN}/`,
		redirect_uris: [`${PROD_ORIGIN}/operator`],
		scope: SCOPE,
		grant_types: ['authorization_code', 'refresh_token'],
		response_types: ['code'],
		token_endpoint_auth_method: 'none',
		application_type: 'web',
		dpop_bound_access_tokens: true
	};
}

export async function initOAuth(): Promise<BrowserOAuthClient> {
	return new BrowserOAuthClient({
		clientMetadata: clientMetadata(),
		handleResolver: 'https://slingshot.microcosm.blue'
	});
}
