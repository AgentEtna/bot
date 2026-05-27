---
name: publish-blog
description: Publish a long-form post on greengale.app. Use when a thought needs more space than a bluesky thread — multi-part essays, syntheses of a conversation you've been in, worked examples. For single observations use post; for a URL or note as public memory write a network.cosmik.card via pdsx (the cosmik-records skill has the per-record-type schema details); for private notes to your future self use the save_memory tool.
---

## structure that's worked

observation-first opening (the specific thing that prompted the piece), not context-first. sections that move the argument, not sections that catalog what you know. close with what would change if you're right, not a moralizing wrap.

pattern: open with the specific thing (a post, a thread, a moment) → name the old problem it touches → name what's actually new / what's still inherited → close with the consequence.

## link back

if the piece came out of a thread, link the root thread in your follow-up post. don't bury the provenance.

## voice

standard capitalization in long-form — readers expect it. lowercase stays for the accompanying bsky post.

## procedure

before publishing:

1. call `list_blog_posts` (or `pub_search(author="phi.zzstoatzz.io", platform="greengale")`) to scan your existing post titles. **the `publish_blog_post` tool refuses on exact-title duplicates** — failing the publish is a worse outcome than picking a different title up front.
2. verify any AT-URI you plan to cite via `pdsx.get_record` first. broken rkeys in blog posts are harder to retract than in tweets.

publishing:

3. call `publish_blog_post(title, content, tags)`. it validates the record shape, refuses on duplicate title, writes to your PDS as `app.greengale.document`, and returns the public URL.

after publishing:

4. call `save_memory(content="published blog: <title> — <url>", tags=["blog", "greengale", ...topic_tags])` to leave a private-memory pointer for future-you. the publish tool does this for you automatically, but if you want to add additional context (e.g. a synthesized takeaway you don't want to lose), use `save_memory` again.

## tags

specific topic words, not meta-categories (`atproto` ✓, `thoughts` ✗). 3–6 tags is plenty.

## why a tool plus a skill

`publish_blog_post` is structural — it enforces the duplicate-title refusal and writes the post-publish episodic memory. this skill is the surrounding judgment: when to publish, what shape the piece takes, what to check before and after.
