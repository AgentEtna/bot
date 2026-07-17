---
name: pdsx-fundamentals
description: How to use the pdsx MCP for atproto record CRUD on arbitrary lexicons. Load this when you want to do something on atproto that doesn't have a dedicated tool — saving to a custom lexicon, opening a tangled issue, writing a leaflet comment, etc.
---

pdsx is a generic atproto MCP. it lets you do CRUD on any lexicon as long as you (a) know the NSID and (b) construct a record matching that lexicon's schema. that means you can interact with anything on atproto — tangled issues, leaflet documents, cosmik cards, calendar events — without anyone writing a per-collection tool first.

## the operations

| call | use for |
|---|---|
| `mcp__pdsx__describe_repo(repo)` | list every collection a given repo has records in |
| `mcp__pdsx__list_records(collection, repo, limit, cursor)` | paginate records in a collection |
| `mcp__pdsx__get_record(uri)` | fetch one record by AT-URI |
| `mcp__pdsx__query(nsid, params?, host?, repo?)` | call a read-only XRPC query (GET) — the non-record read surface |
| `mcp__pdsx__create_record(collection, record, rkey?)` | write a new record on **your** PDS |
| `mcp__pdsx__update_record(uri, record)` | replace an existing record's value |
| `mcp__pdsx__delete_record(uri)` | delete a record from your PDS |
| `mcp__pdsx__whoami()` | confirm which DID/handle pdsx is authed as |

`create_record`, `update_record`, `delete_record` always write to **the authenticated repo** — that's you (`@phi.zzstoatzz.io`). you cannot write records into someone else's repo. you can read from any repo.

## read-only queries beyond record CRUD

`list_records`/`get_record` only cover *records*. for the rest of atproto's read surface — host-level sync, identity, server description, the `app.bsky.*.get*` family — use `query`. it's **GET-only**: it can never write. almost all of it runs unauthenticated; the few allowlisted session-backed NSIDs (notifications) attach your session automatically, still read-only. reach for it freely.

- who's hosted on a PDS: `mcp__pdsx__query("com.atproto.sync.listRepos", host="pds.zat.dev")`
- resolve a handle → DID: `mcp__pdsx__query("com.atproto.identity.resolveHandle", params={"handle": "bufo.uk"})`
- someone's public profile: `mcp__pdsx__query("app.bsky.actor.getProfile", params={"actor": "bufo.uk"})`

target one of: `repo=` (a handle/DID → routes to that user's PDS), `host=` (a service like `pds.zat.dev`), or neither (defaults to the public appview for `app.bsky.*` getters). for *writing* a record, this is the wrong tool — use `create_record`.

note on direction: `resolveHandle` only goes handle→DID. to go the other way (DID→handle), use `getProfile` — it returns the handle.

## finding the right lexicon

three ways, in order of effort:

1. **you already know the NSID.** common ones: `app.bsky.feed.post`, `network.cosmik.card`, `sh.tangled.repo.issue`, `pub.leaflet.document`, `io.zzstoatzz.phi.goal`. just call `create_record` with that collection.

2. **you know a repo that uses it.** call `mcp__pdsx__describe_repo(repo="zzstoatzz.io")` to see every collection that repo has records in — you'll often spot the lexicon you want by name.

3. **you want to read the schema before writing.** lexicon schemas themselves are stored as records under `com.atproto.lexicon.schema/{nsid}` on the lexicon owner's PDS. for `sh.tangled.repo.issue`, that means `mcp__pdsx__get_record(uri="at://did:plc:tangled-owner/com.atproto.lexicon.schema/sh.tangled.repo.issue")`. read the schema, see required fields, then construct.

## constructing a record

every record needs a `$type` field equal to the NSID, plus whatever the lexicon requires. pdsx auto-injects `$type` and `createdAt` if they're missing — but it doesn't validate the rest of your record. if you send a malformed record, the PDS rejects it with an XRPC error and you'll see the field name in the error message.

minimum example (a standalone cosmik note — the one cosmik write that still goes through pdsx; URL cards, collections, and connections go through the semble tools, see `cosmik-records`):

```
mcp__pdsx__create_record(
  collection="network.cosmik.card",
  record={
    "kind": "NOTE",
    "content": {"text": "the engram architecture re-derivation is structural convergence, not coincidence."}
  }
)
```

result: `{"uri": "at://did:plc:.../network.cosmik.card/3xxxxx", "cid": "..."}`

if you want a specific rkey (e.g. for `app.bsky.actor.profile/self`), pass `rkey="self"`. otherwise pdsx generates a TID.

## the consent / posting layer

pdsx will happily let you create `app.bsky.feed.post` records — but **don't post via pdsx**. the trusted posting tools (`post`, `like_post`, `repost_post`) handle mention-consent allowlisting, reply-ref construction (`post(text, in_reply_to=uri)` for any reply, including threading your own), grapheme splitting, and memory writes. raw pdsx posting bypasses all of that. use it for everything *except* posts.

pdsx's `query` tool is **GET-only** — sync, identity, and the `app.bsky` getter surface (`getQuotes`, `getProfile`, `getPostThread`, `searchPosts`, etc.). auth is automatic and not yours to choose: the few session-backed NSIDs on the server's allowlist (currently `app.bsky.notification.listNotifications` / `getUnreadCount`) attach your session by themselves; everything else is queried publicly. there is no `authenticated` parameter worth passing — it's deprecated and ignored. if a session-backed endpoint you need isn't allowlisted, raise it with the operator (extending the allowlist is a deliberate pdsx-server decision, not a parallel local tool).

## reading blobs and media

pdsx is still the right way to discover records: use `get_record`,
`list_records`, `describe_repo`, or `query` to find the AT-URI you care
about. if that record has a text/image blob you need to actually inspect,
call the native `inspect_record_media(uri=...)` tool with that AT-URI.

that tool is not a replacement for pdsx record CRUD. it is the bridge from
record-shaped data to model-readable media: it fetches allowed text/image
blobs (`text/*`, JSON, PNG, JPEG, GIF, WebP) and returns image blobs as real
multimodal content so you can see pixels instead of guessing from URLs,
titles, or alt text.

## owner-gating for durable public actions

some record types are durable, public, and visible (opening an issue against someone else's repo, vouching for a maintainer, following an account, mutating a goal record). these go through the like-as-approval pattern:

1. you post a request: `"@operator, like this to authorize: i want to <do thing>"`
2. operator likes the post
3. on the next batch where their like lands, the action is authorized
4. you execute exactly the action you described — never something adjacent that happened to ride the same batch

write the request post with `post` (operator-handle is on the mention-consent allowlist, so they get notified). then do nothing until you see the like in the next notifications batch. one approval = one specific action.

## domain-specific guidance

for record types you write often, there's usually a more specific skill that walks through the schema and includes worked examples:

- **cosmik writes** (notes, urls, collections, connections): see `cosmik-records` — most go through the semble tools now, not pdsx
- **tangled records** (issues, PRs, follows, vouches): planned
- **leaflet records** (documents, comments): planned
- **phi self-records** (goals, observations, mention-consent): planned

**important framing**: domain skills don't add new capabilities. pdsx already lets you write any record. skills are *lighthouses* — they tell you the schema, the conventions, the gotchas, so you do something well that you could already do crudely. the capability surface is wider than the skill surface, not narrower.

if no domain skill exists yet for what you want to do, you have everything you need above — find the lexicon, read its schema, construct a record, call `create_record`.
