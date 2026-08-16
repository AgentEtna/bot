# lexicons

phi defines seven custom record and query types under `io.zzstoatzz.phi.*`.
They live in `lexicons/`, one file per NSID, and are **published as records on
phi's own PDS** so anyone can resolve the schema from the NSID alone.

| NSID | type | what it is |
|---|---|---|
| `io.zzstoatzz.phi.self` | record | phi's self record — who she is, in her words. written only via `write_self` (owner-gated; raw pdsx writes to it are refused by `mcp_guard`) |
| `io.zzstoatzz.phi.goal` * | record | goals + interests, the compass in `[GOALS AND INTERESTS]` |
| `io.zzstoatzz.phi.override` | record | the operator override (safe mode); lives on the *operator's* repo, not phi's |
| `io.zzstoatzz.phi.mentionConsent` | record | handles phi is allowed to @-mention |
| `io.zzstoatzz.phi.editorialContext` | record | grounding notes coral's curator injects verbatim |
| `io.zzstoatzz.phi.entityDirectives` | record | per-entity directives for that same curator |
| `io.zzstoatzz.phi.getAbilities` | query | phi's tools and what each costs if it goes wrong |

\* `goal` predates the lexicon directory and has no schema file yet.

## authority: which domain owns an NSID

The authority is **every segment but the last, reversed**. `io.zzstoatzz.phi.self`
→ authority `phi.zzstoatzz.io`, name `self`. So phi owns her own lexicons, the
same way `typeahead.waow.tech` owns `tech.waow.typeahead.*`.

Resolution needs a DNS TXT record:

```
_lexicon.phi.zzstoatzz.io   TXT   did=did:plc:65sucjiel52gefhcdcypynsr
```

**This is not the same as the handle record.** `_atproto.phi.zzstoatzz.io`
already exists and claims the *handle*; `_lexicon.` claims *schema authority*,
and the protocol keeps them separate so a domain can delegate one without the
other. Without the `_lexicon.` record, resolvers return `AuthorityNotFound`
even though the schema records exist — verify with:

```bash
curl "https://lexicon.garden/xrpc/com.atproto.lexicon.resolveLexicon?nsid=io.zzstoatzz.phi.self"
```

zzstoatzz.io's DNS is at Namecheap, so that record is added by hand rather
than through the Cloudflare API the other waow.tech zones use.

## publishing

```bash
uv run --project . python scripts/publish_lexicons.py --dry-run   # what would change
uv run --project . python scripts/publish_lexicons.py             # publish
```

Each file becomes a `com.atproto.lexicon.schema` record with `rkey` = its NSID,
in phi's repo, using her own credentials from `.env`. `putRecord` overwrites, so
re-run after editing any lexicon — that is the whole update path.

The script refuses to publish if a file's path doesn't match its NSID
(`io.zzstoatzz.phi.self` must be at `lexicons/io/zzstoatzz/phi/self.json`), so a
typo in either shows up as a mismatch instead of a silent orphan.

## why bother

Two reasons beyond tidiness:

**It makes a constraint checkable.** `getAbilities` requires `risk.magnitude`
and `risk.reason` on every tool. That is what "a tool without a risk
declaration is not a valid tool" means in practice — `tests/test_abilities.py`
holds the code to the schema, in both directions, so a declaration cannot drift
from the tools that exist.

**It makes phi legible from outside.** Her records are already public on the
firehose; publishing the schemas means someone reading
`io.zzstoatzz.phi.goal` off her repo can find out what the fields mean
without reading this codebase.

What it does *not* do is make her safer. A published schema is documentation,
not enforcement — the gates are in `core/policy.py` and `core/mcp_guard.py`.
