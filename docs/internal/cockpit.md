# cockpit (internal)

> **internal** — operator-facing surface. committed publicly like
> everything else, but not part of phi's advertised interface; expect
> rough edges and no stability promises.

the sveltekit app in `web/` (svelte 5, adapter-static), served by the
fastapi process at [phi.zzstoatzz.io](https://phi.zzstoatzz.io). pure
SPA: `ssr`/`prerender` disabled globally, unknown routes fall back to
`index.html` server-side and a styled `+error.svelte` client-side.

## surfaces

| route | what |
|---|---|
| `/` | HUD: identity, status pill (from `/health`), lens cycler (mind map / constellation), counts |
| `/docket` | daily promotion candidates from the docket flow |
| `/capabilities` | phi's registered tools, from `/api/abilities` (ground truth, not hand-curated) |
| `/market` | phi's top chicken market book: current position (with round context), all-time p&l + net-worth sparkline banded by market round, trade ledger with settlement outcomes and profile links. reads topchicken.cee.wtf via the bot's `/api/chicken/*` proxy (upstream has no CORS headers) |
| `/operator` | **operator override editor** — atproto OAuth login, write your `io.zzstoatzz.phi.override` record, see the live state phi obeys. see [../safety.md](../safety.md) |

`OverrideBanner` renders cockpit-wide when the operator's override is
active (public read of the record, ~60s cadence), linking to `/operator`.

## oauth notes

- browser flow (`@atproto/oauth-client-browser`), doodl's house pattern:
  static client metadata at `/oauth-client-metadata.json`, granular
  scope (`repo:io.zzstoatzz.phi.override?action=create&action=update`),
  `slingshot.microcosm.blue` for handle resolution. no bsky appview.
- **authz is repo ownership**: anyone can sign in and write the record
  to their own repo; the bot only reads the operator's (`owner_did`).
- localhost dev uses the atproto loopback client (metadata derived from
  the `client_id` query string; no hosted doc needed).
- historical trap: PDSes older than atproto PR #5147 (2026-06-25)
  reject same-site OAuth clients (`Sec-Fetch-Site: same-site`), which
  bites here because the cockpit and the operator's PDS share
  `zzstoatzz.io`. the operator's PDS runs a patched image — see
  `pds-infra`.

## dev

```bash
cd web && bun install && bun run dev   # vite proxies /api/* to the python server
bun run check                          # svelte-check
```

the docker build compiles `web/` in a bun stage and the fastapi app
mounts `web/build/` at `/` (see `src/bot/main.py` "frontend mount" for
the routing layering — explicit routes, then static, then SPA fallback).
