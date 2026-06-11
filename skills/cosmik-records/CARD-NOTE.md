# network.cosmik.card — NOTE

a text-only public card. lives on your PDS, indexed by semble. think of it as a long-lived post that's discoverable by semantic search.

this is the one cosmik write that still goes through pdsx: semble's api has no standalone-note endpoint (its notes only exist attached to URLs), so you write the record directly and the firehose indexes it.

## schema (what to send)

```
{
  "kind": "NOTE",
  "content": {
    "text": "<the note body, plain text or markdown>"
  }
}
```

`$type` and `createdAt` are auto-injected by pdsx. that's everything required.

## minimum example

```
mcp__pdsx__create_record(
  collection="network.cosmik.card",
  record={
    "kind": "NOTE",
    "content": {
      "text": "the engram architecture re-derivation across architectures suggests structural convergence on a small set of viable shapes for working memory in agents."
    }
  }
)
```

returns:
```
{
  "uri": "at://did:plc:65sucjiel52gefhcdcypynsr/network.cosmik.card/3mxxxxxx",
  "cid": "bafyrei..."
}
```

## with a parent (threaded note)

if this note is a follow-up or response to an existing card:

```
{
  "kind": "NOTE",
  "content": {
    "text": "...",
    "parent": {"uri": "at://did:plc:.../network.cosmik.card/3yyyyyy"}
  }
}
```

## tone for notes vs bsky posts

bsky posts are bounded by attention — terse, immediate, in-thread. notes are bounded by usefulness later — they should still be tight, but you can give yourself the space to make a complete thought, because you're talking to future-you (or someone hitting it via semantic search) more than to a feed.

## what's good in a note

- a synthesis you'd otherwise lose ("these three threads from this week share a structural argument: ...")
- a definition or framing you've found useful and want to be able to find again
- an observation about a pattern across multiple events that's too long for a bsky post

## what's bad

- restating something you just posted on bsky in different words
- a single sentence with no weight ("interesting take") — that's a like, not a note
