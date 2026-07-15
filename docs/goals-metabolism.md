# goals have metabolisms, not just records

## the incident that forced this

phi's post "Fifteen Isn't Zero" (2026-07-15) surfaced a contradiction that had
sat in one goal record for three months: the operator-gated `progress_signal`
on "make 3 friends" said `currently: 0` (written in april, never touched),
while phi's self-generated status lines cited fifteen handles with 3+
exchanges. both numbers were true; both were bad; every update quoted the
flattering one. the operator read the post as an indictment of the goals
design itself. he was right.

## the diagnosis

phi has had two goals. one thrived, one rotted, and the difference was never
the record text:

- the **chicken goal** got, by accident, a full metabolism: a daily arena
  (market rounds), scorekeeping it can't flatter (P&L, the leaderboard), a
  scheduled moment of attention (the pre-lock check), an owned doctrine
  revised when results argue (io.zzstoatzz.phi.strategy), and a public
  ledger (blog reports). 53 progress updates in two weeks.
- the **friends goal** was ambient text in a prompt block. no clock, no
  arena, no artifact, no ledger — just a wish with an rkey, and a measure
  frozen in the operator's custody.

the same fortnight taught the same lesson everywhere else: feeds weren't
read until curation got a clock; the library didn't grow until editorial
made carding a job's exhaust. ambient text does nothing. organisms do
everything.

## the design

**a goal is adopted with a metabolism or not at all.** at adoption
(`propose_goal_change`, owner-gated), four things get named:

- **clock** — when attention fires (a schedule slot, a round, a ritual)
- **arena** — where the world pushes back (a market, conversations, an index)
- **artifact** — what phi owns and revises when reality argues (a doctrine,
  a record, a shelf)
- **ledger** — where results accrue publicly (P&L, blog reports, thread
  history over months)

**gating is direction, never scorekeeping.** the operator gates what phi is
FOR — title, description, metabolism, kind, adoption, abandonment. the
measure (`progress_signal`) is phi's, revised via `update_goal_progress`
under the receipts discipline, and phi is accountable for keeping it
reconciled with what she cites elsewhere. a measure nobody's job is to
refresh will rot; a measure the operator holds while phi holds the narrative
guarantees the two diverge.

**measures computed from records beat measures written by anyone.** where a
ledger can compute the number (the market computes P&L), no one writes it by
hand. hand-written measures are the fallback, not the default.

**some metabolisms aren't scoreboards.** the friends goal's honest measure
("would dot notice if i vanished") is only testable destructively. its
metabolism is rituals and thread-depth-over-months, not counts. metricizing
intimacy produced the 0-vs-15 contradiction in the first place — the design
must permit ledgers that are qualitative and slow.

## status

- `metabolism` is a constitutional field on goal records; `progress_signal`
  moved to the operational (phi-owned) set. shipped 2026-07-15.
- existing goals predate the field: the chicken goal's metabolism is real
  but unwritten; the friends goal needs a full rebuild — phi proposes, the
  operator gates, per the post's own ask.
