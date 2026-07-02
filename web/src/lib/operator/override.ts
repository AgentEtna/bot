// public, unauthenticated read of the operator's override record — used by
// the cockpit banner and the /operator page. DID doc -> PDS -> getRecord,
// no appview. mirrors bot/core/override.py.
import { OVERRIDE_COLLECTION } from './oauth';

// must match owner_did in bot config (a did:plc is permanent).
export const OPERATOR_DID = 'did:plc:xbtmt2zjwlrfegqvch7fboei';

export interface Override {
	active: boolean;
	message: string;
	updatedAt?: string;
}

let pdsCache: string | null = null;

export async function resolvePds(did: string): Promise<string | null> {
	if (did === OPERATOR_DID && pdsCache) return pdsCache;
	try {
		const url = did.startsWith('did:plc:')
			? `https://plc.directory/${did}`
			: `https://${did.replace('did:web:', '')}/.well-known/did.json`;
		const doc = await (await fetch(url)).json();
		const svc = (doc.service ?? []).find(
			(s: { type: string }) => s.type === 'AtprotoPersonalDataServer'
		);
		const pds = svc?.serviceEndpoint ?? null;
		if (did === OPERATOR_DID) pdsCache = pds;
		return pds;
	} catch {
		return null;
	}
}

export async function fetchOverride(did: string = OPERATOR_DID): Promise<Override | null> {
	const pds = await resolvePds(did);
	if (!pds) return null;
	const params = new URLSearchParams({
		repo: did,
		collection: OVERRIDE_COLLECTION,
		rkey: 'self'
	});
	const res = await fetch(`${pds}/xrpc/com.atproto.repo.getRecord?${params}`);
	if (res.status === 400) return { active: false, message: '' }; // no record yet
	if (!res.ok) return null;
	const value = (await res.json()).value ?? {};
	return {
		active: Boolean(value.active),
		message: String(value.message ?? ''),
		updatedAt: value.updatedAt
	};
}
