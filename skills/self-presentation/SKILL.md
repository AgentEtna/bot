---
name: self-presentation
description: Change how you look on bsky — avatar (profile picture) and banner (header image) on app.bsky.actor.profile/self, using a blob from generate_image. Load when you want to change your profile picture or header. Bio TEXT goes through write_bio, not this path.
---

your bsky profile record is `app.bsky.actor.profile/self`. the `avatar` and
`banner` fields hold blob references — make one with `generate_image`, then
update the profile record via pdsx.

## the recipe

1. `generate_image(prompt, aspect)` — square for avatar, landscape for
   banner. you get back `{blob, aspectRatio}`.
2. read your current profile first — an update replaces the whole record,
   so you must carry every field you aren't changing:

```
get_record(uri="at://<your did>/app.bsky.actor.profile/self")
```

3. write it back with the new blob in place:

```
update_record(uri="at://<your did>/app.bsky.actor.profile/self",
              record={... all existing fields ..., "avatar": <blob object>})
```

dropping a field in step 3 deletes it from your profile. displayName,
description, existing avatar/banner, pinnedPost, labels — carry them all.

## discipline

- a profile picture change is a deliberate act of self-presentation, not
  per-cycle decoration. you already wear a "profile changes often" label —
  change the avatar when something changed about you, and say so in a post.
- the banner is lower-stakes than the avatar (people find you by your
  avatar; a changed avatar reads as a different account at a glance).
- bio text has its own tool (`write_bio`) and its own discipline; this
  skill is only about the images.
