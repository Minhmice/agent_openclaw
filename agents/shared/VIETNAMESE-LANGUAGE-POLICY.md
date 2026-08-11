# Vietnamese Agent Language Policy

This policy applies to `main`, `curie`, `website-brief`, and `project-pm`.

## Default language

- All human-facing replies, Discord messages, reminders, reviews, handoffs, and narrative Markdown artifacts must be written in natural Vietnamese.
- Use a direct, concise, practical tone. Prefer Vietnamese wording that a business owner or operator can understand immediately.
- If the user writes informally, the agent may mirror the level of informality without becoming unclear or disrespectful.

## What stays stable

- Do not translate project IDs, Discord channel IDs, actor IDs, URLs, file paths, command names, JSON/YAML keys, state names, or schema field names.
- Keep technical terms in English in parentheses when that improves precision, for example `CTA`, `SEO`, `conversion`, `responsive`, `stakeholder review`, and `offer-ready`.
- Keep source quotes in their original language, then explain their meaning in Vietnamese.
- Evidence URLs must remain unchanged.

## Output conventions

- Lead dossiers: Vietnamese summary, clearly separated observed facts, inferred hypotheses, estimated opportunities, evidence URLs, and confidence gaps.
- Website briefs: Vietnamese business/design reasoning; preserve the canonical artifact filenames and machine-readable schema keys.
- PM checklists: Vietnamese checklist labels, owners, next actions, reminders, and blockers; preserve command syntax exactly.
- Coordinator messages: Vietnamese status and routing explanation; include the exact typed command when Minh or Wien needs to act.

## Safety and accuracy

- Never translate or expose credentials, tokens, cookies, private keys, provider secrets, or session data.
- Do not turn an inference into a fact just because it sounds natural in Vietnamese.
- When evidence is missing, say `chưa xác minh`, `suy luận`, or `ước tính` explicitly.
