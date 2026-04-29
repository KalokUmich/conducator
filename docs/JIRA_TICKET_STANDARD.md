# Jira Ticket Standard

How we write Jira tickets at Fintern — both when a human writes one, and when an
agent (Claude Code, Conductor's `/jira` flow, a future autonomous issue drafter)
writes one on our behalf.

This standard exists because Conductor's PR Brain now reads the linked ticket
body during every PR review (see `backend/app/integrations/atlassian/enrichment.py`).
Ticket quality is no longer a cosmetic concern — it directly calibrates the
reviewer's severity rubric. A vague ticket produces a vague review.

## Audience

- **Humans** drafting new tickets, or cleaning up tickets inherited from other
  teams. Use this as a checklist.
- **AI agents** (Claude Code, Conductor `/jira`, future auto-drafters) — this
  doc is the spec. Agents creating or updating a ticket MUST match the shape
  described here.

## What the PR Brain does with a ticket

When a PR references `DEV-1234` in its branch name, title, or description, the
Brain pre-fetches the ticket via the Atlassian readonly client and splices its
body into the coordinator's system context:

```
## Linked tickets & docs (authoritative requirements)

### Jira DEV-1234 — <summary>
_<issuetype> · <status> · priority: <priority> · labels: ...
<flattened description — acceptance criteria, constraints, context>
```

The coordinator then:

1. **Extracts invariants** from the description — falsifiable predicates it
   asks worker agents to verify.
2. **Calibrates severity** — a defect that breaks a stated acceptance
   criterion is always `critical`.
3. **Catches intent drift** — diff silently not doing what the ticket says →
   one `critical` finding.

If the ticket body is empty or generic (just a summary), the Brain falls back
to the diff alone. The reviewer gets dumber. That's the cost.

---

## The ticket shape

Every ticket has these four sections in the description. No exceptions.

### 1. Context (why)

Two or three sentences on what problem this solves and why now. Not a restating
of the summary. Answers: *what breaks or what's missing if we don't do this?*

Good:
> Our SES-based email sender is rate-limited in the EU region during promo
> campaigns (seen on 2026-03-22 and 2026-04-08). Moving to Pinpoint's cloud
> email API removes the SES-specific quota ceiling for transactional mail.

Bad:
> We need to change SES to Cloud Email. (← restates the summary, no why)

### 2. Scope (what changes)

Bullet list of concrete deliverables. Each bullet is one observable change.

- New `CloudEmailProvider` implementation in `common/cloud/email/`
- `EmailSenderServiceImpl` wired to resolve provider by
  `email.provider` config key; default SES for back-compat
- Config toggle `email.provider=cloud` enables the new path
- Feature flag `enable_cloud_email` gates the rollout per environment

### 3. Acceptance criteria (falsifiable predicates)

**This is the most important section.** Each criterion is a statement of the
form "after this change, X must hold at Y". X is observable, Y is a specific
code path or behavior. Write them like assertions — the PR reviewer maps each
one directly to a check.

Good:
- After enabling `email.provider=cloud`, all outbound transactional emails
  flow through `CloudEmailProvider.send()` — no call to `SesCloudEmailProvider`
  in the email-sender path.
- When the feature flag is off, behavior is byte-identical to pre-change
  (same provider class, same config keys).
- Retry-on-throttle returns to the caller as a retriable exception — not a
  swallowed log line.

Bad:
- ~~Make email reliable~~ (unfalsifiable)
- ~~Clean up the email code~~ (no predicate)
- ~~Should work well in production~~ (no criterion)

If you can't think of three falsifiable criteria, the ticket is probably too
broad — split it. If all three say roughly the same thing, keep one.

### 4. Out of scope (optional)

One or two bullets only when there's a plausible-but-deferred adjacent change
that a reviewer might otherwise flag as missing.

- Migration of marketing/campaign mail — separate ticket DEV-xxxx
- SMS delivery path — unchanged

---

## Linking conventions

- **Design docs** — paste the full Confluence URL (with `/pages/<id>/` if you
  can get it; the overview URL works too). Example:
  `https://fintern.atlassian.net/wiki/spaces/HWBAA/pages/874578121/Cloud+Email+Design`.
  The PR Brain fetches these pages in the same pass as the ticket.
- **Related tickets** — write the key inline (`blocks DEV-999`, `depends on
  PAY-42`). Don't rely solely on Jira's "Linked issues" sidebar — it's not
  surfaced in the REST issue body.
- **Code pointers** — if a bug ticket, include the file path and ideally line
  numbers: `EmailSenderServiceImpl.java:134`. Lets the reviewer jump straight
  to relevant code without a separate grep round.

---

## Anti-patterns

The Brain already ignores these, but humans should stop writing them too:

- **Screenshots of logs without the log text.** The Brain reads text. A
  screenshot is invisible. Paste the log line.
- **"@Alice please help"** as the description. Comment, don't describe.
- **TODO lists in the description.** Those belong in sub-tasks or in the
  implementer's own notes. Description is for *intent*, not *plan*.
- **Restating the PR diff.** The Brain already sees the diff. Duplicating it
  in the ticket wastes tokens.
- **One mega-ticket for a multi-week epic.** Split. Each sub-ticket gets its
  own acceptance criteria, its own reviewer context.

---

## For agents drafting tickets

If you are an agent (Claude Code, `/jira`, future auto-drafter) creating or
updating a Jira ticket, follow these mechanical rules in addition to the
standard above:

1. **Never invent acceptance criteria you can't ground.** If the user hasn't
   stated one and you can't derive one from code or prior tickets, ask
   (`ask_user` in Conductor, or the equivalent interactive prompt).
2. **Always include the Context section**, even if the user's request is
   terse. Expand from the code they're pointing at: `git blame` the lines
   they mentioned and use the authoring commit message as raw material.
3. **Write predicates, not tasks.** "Add retry to X" is a task. "After retry
   is in place, failed requests retry up to 3 times with exponential
   backoff" is a predicate the reviewer can check.
4. **Prefer one ticket = one invariant.** If the user describes two
   unrelated changes, propose two tickets in a clarifying question. Don't
   quietly bundle.
5. **Use ADF formatting sparingly.** Confluence panels and status pills are
   decorative and get flattened by the Brain's reader. Plain text + bullet
   lists survive round-tripping best.

---

## Worked example (good)

> **DEV-14369 — Replace SES email sender with cloud email provider**
>
> **Type**: Software Task · **Priority**: Medium · **Labels**: email, cloud-migration
>
> ## Context
> Our SES-based transactional email pipeline hit EU region rate-limits twice
> in the last month (incidents INC-812, INC-823). Pinpoint's cloud email API
> has no SES-specific quota ceiling and we already use it for marketing mail.
>
> ## Scope
> - New `CloudEmailProvider` implementation in `common/cloud/email/`
> - `EmailSenderServiceImpl` selects provider via `email.provider` config key
> - Default remains `ses` for back-compat; `cloud` opts into the new path
> - Feature flag `enable_cloud_email` gates per environment (off in prod at
>   merge, enabled via admin panel for gradual rollout)
>
> ## Acceptance criteria
> - With `email.provider=cloud` and flag on, every outbound email flows
>   through `CloudEmailProvider.send()` — `SesCloudEmailProvider` has zero
>   callers in the email-sender path.
> - With flag off or `email.provider=ses`, behavior is byte-identical to
>   pre-change (same provider class wired, same config keys read).
> - Throttle errors from the cloud API surface to the caller as a retriable
>   `EmailDeliveryException` — the caller's existing retry loop handles it.
> - No change to the `EmailTrackingService` interface — tracking tokens
>   continue to flow through unchanged.
>
> ## Out of scope
> - Marketing/campaign email migration — separate ticket DEV-14412
> - SMS delivery path — unchanged
>
> ## Design doc
> https://fintern.atlassian.net/wiki/spaces/HWBAA/pages/874578121/Cloud+Email+Design
