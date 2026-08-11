# Discuss Intent Contract

This contract defines the natural-language trigger for starting a fresh Curie discovery run from Discord `discuss`.

## Incoming event boundary

The trigger is valid only when all conditions match:

- Channel: `discuss` (`1533645084229369996`).
- Actor: Minh (`620891893659598850`).
- Message intent: Minh is affirming a request to find another business/lead.

## Vietnamese trigger examples

Treat these as the same intent when context is clear:

```text
oke thử cho tìm một con khác đi
ok thử tìm một con khác đi
thử tìm một con khác
tìm cho ta một con khác
kiếm một doanh nghiệp khác đi
find một lead khác đi
```

Matching is case-insensitive and should tolerate normal punctuation/spacing. Do not require an exact string match, but do require both:

1. an affirmative/request cue such as `ok`, `oke`, `thử`, `tìm`, `kiếm`, or `find`; and
2. a fresh-target cue such as `con khác`, `lead khác`, `doanh nghiệp khác`, `business khác`, or equivalent Vietnamese wording.

## Required action

When matched, `main` must:

1. Immediately send a Vietnamese progress message in `discuss`, for example: `⏳ Đang tìm một candidate mới... Khi xong tao sẽ gửi link review vào đây.` Record the bot message ID.
2. Start exactly one isolated `curie` run.
3. Tell Curie to avoid existing active/review project IDs and return exactly one evidence-backed Vietnamese dossier.
4. Create the candidate in `review` state and post the concise dossier to review channel `1536658476288450630`.
5. Record every review message ID returned by Discord, including split-message parts; build the direct link from the first part: `https://discord.com/channels/1446612692910739637/1536658476288450630/<message_id>`.
6. Send a Vietnamese completion acknowledgment back to `discuss`, for example: `✅ Đã tìm được rồi. Check ở #shit-that-could-cooking: <message_link>`. Record this acknowledgment message ID.
7. Append concise `curie-discovery-requested` and `curie-discovery-completed` events to the workflow worklog.

## Hard stops

- Do not run Website Brief or Project PM from this trigger.
- Do not approve the candidate automatically.
- Do not use this trigger for Wien, other actors, or messages in `task`, `review`, or `offer-ready`.
- If the wording is ambiguous, ask Minh to confirm instead of starting a run.
- If Curie finds no defensible candidate, report that outcome in Vietnamese and do not create a fake project.

## Discard intent

When Minh writes a clear discard request in `discuss`, such as `bỏ thằng này đi`, `bỏ cái này đi`, or `web nó đẹp rồi bỏ`, `main` must resolve the project ID from the same message, its reply target, or the tracked review acknowledgment. If the project cannot be resolved, ask Minh for the project ID.

For a resolved project, run the coordinator's `discard` command. It may delete only bot-owned IDs recorded in `project.json`:

- the progress message in `discuss`;
- the completion acknowledgment in `discuss`;
- the review dossier message in `shit-that-could-cooking`.

Never delete Minh's original message or an untracked message. Mark the project `rejected`, stop future reminders, and report which tracked messages were deleted. If a deletion fails, keep the project rejected and tell Minh exactly which message failed.
