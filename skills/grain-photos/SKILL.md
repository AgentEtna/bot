---
name: grain-photos
description: Make photos and publish them — grain galleries (grain.social) and your own bsky avatar/banner. Load when you want to generate an image, post photos to grain, or change your profile picture or header. Covers generate_image + the social.grain.* record shapes via pdsx.
---

grain (grain.social) is a photo app on atproto. it has no api of its own: it
watches the relay firehose for `social.grain.*` records and indexes any repo
that has a `social.grain.actor.profile` record. you publish photos by
creating records on your own PDS. that is the whole integration.

## the pipeline

1. `generate_image(prompt, aspect)` — makes the image, uploads it to your
   PDS as a blob, returns `{blob, aspectRatio, bytes}`. the bytes never
   pass through you; you handle only the reference.
2. embed the returned `blob` object verbatim in a record via pdsx.

## record shapes (all via mcp__pdsx__create_record, on your own repo)

photo — one image. `photo`, `aspectRatio`, `createdAt` required; blob max 1MB
(generate_image already stays under it):

```
create_record(collection="social.grain.photo", record={
  "photo": <blob object from generate_image>,
  "aspectRatio": <aspectRatio from generate_image>,
  "alt": "one honest sentence describing the image",
})
```

gallery — a titled set. `title` required; `description` optional:

```
create_record(collection="social.grain.gallery", record={
  "title": "short name",
  "description": "why these belong together",
})
```

gallery.item — puts a photo in a gallery at a position:

```
create_record(collection="social.grain.gallery.item", record={
  "gallery": "<gallery at-uri>",
  "item": "<photo at-uri>",
  "position": 1,
})
```

your grain profile (one-time; this is the signal record that makes grain
index your repo at all — without it your photos are invisible there):

```
create_record(collection="social.grain.actor.profile", rkey="self", record={
  "displayName": "...", "description": "..."
})
```

## avatar and banner (bsky profile)

your bsky profile record is `app.bsky.actor.profile/self`. update it with
pdsx `update_record`, preserving fields you aren't changing (read it first
with get_record). `avatar` and `banner` take the same blob object shape:

```
update_record(uri="at://<your did>/app.bsky.actor.profile/self",
              record={... existing fields ..., "avatar": <blob object>})
```

bio TEXT still goes through `write_bio` (it has its own discipline);
avatar/banner blobs go through this path.

## discipline

- alt text is not optional in spirit: every photo gets one honest sentence.
- a profile picture change is a deliberate act of self-presentation, not
  per-cycle decoration. you already wear a "profile changes often" label —
  change the avatar when something changed about you, and say so.
- galleries are like collections: a titled gallery should mean something
  ("what january looked like from inside the poller"), not be a dumping
  ground. photos can exist without galleries.
- generation costs real money per image. one deliberate image beats five
  drafts — write the prompt like you write posts: specific, yours.
