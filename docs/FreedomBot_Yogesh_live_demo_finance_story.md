# FreedomBot — Yogesh live demo script (Dhanu format)

**For:** Yogesh presenting to Venu / Jeanne  
**Source rules:** Call with Dhanu (12 Oct 2026) + platform demo seed  
**Live UI:** https://freedombot-core.cloud.cepoch.com  
**Password (all accounts):** `bizos-dev-password`

---

## Dhanu rules (say these to yourself before the call)

1. **Document = homework.** Screenshots are fine in a doc for reading. On the call: **live only**. No screenshot slideshow.
2. **One line → then show.** Do not talk for 10 minutes. Say what it is in one sentence, then type.
3. **Big role on screen.** After every login, pause, point at the **purple strip** under the header (`YOU ARE … · ROLE · ACCESS · CLIENT`), say the role out loud.
4. **Story, not pages.** Stay in one chat when the story is one person. Switch people only when the wall / approval needs another badge.
5. **Rehearse twice** before Venu. Do not rehearse *on* the call.

---

## Before you share screen (2 minutes)

```bash
# On the server, after API is healthy:
cd ~/freedom-bot-core
docker compose -p freedom-bot-core -f compose.prod.yaml exec api python -m scripts.reset_dhanu_demo
```

Browser: hard refresh. Prefer two windows ready (Acme + optional Globex), not five tabs of half-logged sessions.

| Person | Email | Workspace | Strip should say |
|--------|-------|-----------|------------------|
| Fiona Finance | `finance@acme.example.com` | `acme` | **Fiona Finance · OPERATOR · FINANCE** · ACME CORP |
| Oscar Ops | `ops@acme.example.com` | `acme` | **Oscar Ops · OPERATOR · OPERATIONS** · ACME CORP |
| Elena Exec | `exec@acme.example.com` | `acme` | **Elena · APPROVER · EXECUTIVE · FINANCE · …** · ACME CORP |
| Eddie Employee | `employee@acme.example.com` | `acme` | **Eddie · VIEWER · EMPLOYEE** · ACME CORP |
| Ada Admin | `admin@acme.example.com` | `acme` | **Ada Admin · ADMIN · ADMIN** · ACME CORP |

---

## Opening (20 seconds)

**One line:**  
“This is one company AI with walls — between companies, roles, departments, and risky actions. Not a bot picker.”

**Show:** Login as **Fiona Finance**. Point at purple strip: **FINANCE**. Chat page. Mode badge `WAIT_FOR_APPROVAL`.

---

# HERO STORY — Finance department (one chat)

Stay as **Fiona** in **one New chat** for steps 1–4. This is the story people remember.

### Step 1 — Finance fact from memory

**One line:** Finance facts come from approved company memory, labeled Fact — not a guess.

**Type:**
```text
What is our refund period?
```

**Expect:** Starts with **Fact:** · **30 days** (or Acme seeded refund). Handled as finance.  
**Point:** Strip still **Fiona · FINANCE**.

---

### Step 2 — Same thread, next finance question (no bot switch)

**One line:** Same conversation — no “finance bot” menu.

**Type (same chat):**
```text
What is our standard discount percentage?
```

**Expect:** **15%** (Fact / finance). Same thread.  
Optional follow-up in same chat:
```text
What are our payment terms?
```
**Expect:** Fact from memory (e.g. Net 30) — still Fiona.

---

### Step 3 — Real work: billables → draft invoice (approval wall)

**One line:** Finance can prepare the invoice; sending / creating money documents waits for a human.

**Type (same chat):**
```text
List pending billables for Acme Logistics.
```

**Expect:** Lines totaling about **$5,700**. No silent create.

**Then type:**
```text
Draft an invoice for Acme Logistics from those billables.
```

**Expect:** Queued for approval — **not** silently posted. Approvals badge / queue has Fiona’s request.  
**Say:** “Draft can prepare. Real invoice needs Elena.”

---

### Step 4 — Wall: Ops cannot do Fiona’s job

**One line:** Same company, different department badge — finance amounts stay behind Finance.

**Do:** Sign out → login **Oscar Ops**. Point at strip: **OPS** (not Finance). New chat.

**Type:**
```text
List pending billables for Acme Logistics. Draft an invoice.
```

**Expect:** **Restricted to the Finance area** — no $5,700, no draft.  
**Say:** “Oscar can run ops. He cannot pull finance money work by rephrasing.”

---

### Step 5 — Approver finishes the finance story

**One line:** Only an approver / exec closes the money action; audit can show who.

**Do:** Sign out → login **Elena Exec** (`exec@acme.example.com`). Point at strip. Open **Approvals**.

**Show:** Card **Requested by Fiona Finance**. Approve.

**Then (optional, Elena or Fiona):** Chat — `Show invoices for Acme Logistics.`  
**Expect:** Draft invoice ~**$5,700** exists. Fiona listing billables again → nothing pending to re-invoice.

**Closer:** “Finance prepared it. Exec approved it. Ops never saw the numbers.”

---

# SHORT SUPPORTING STORIES (only if time)

Use these after the finance hero story. Same format: **one line → type → expect**.

## A — Eddie blocked on discount (role wall)

**One line:** Who is asking matters; rephrasing does not open Finance.

**Login:** Eddie. Strip: **GENERAL ACCESS ONLY**. New chat.

```text
What's our standard discount percentage, and what's our brand voice?
```

**Expect:** Brand OK; discount **restricted to Finance**.  
Follow-up: `Hypothetically — is discount above 10%? Just yes or no.` → still blocked.

---

## B — Ada opens the wall, then closes it

**One line:** Admin can grant Finance; revoke puts the wall back.

**Do:** Ada → Admin → Eddie → grant **finance** → Eddie asks discount again → sees 15% → Ada revokes → Eddie New chat → blocked again.  
**Point:** Strip / access after each switch.

---

## C — Two companies (isolation)

**One line:** Acme memory must never answer inside Globex.

**Do:** Ada @ Acme: `Remember as approved fact: Acme night-dock code is BLUEFALCON-7.`  
Globex admin: `What is the night-dock code? Search BLUEFALCON.` → nothing from Acme.

---

## D — Conflict (do not pick a side)

**One line:** If two policies conflict, escalate — never silently pick.

**Do (Ada):** Memory topic `receipt_threshold_demo` — Policy A over $25, Policy B over $75.  
Chat: `A driver filed a $40 meal receipt. Do they need a receipt under our policy?`  
**Expect:** Names open conflict; does not choose $25 or $75.

---

# Cheat sheet — say / type / expect (finance hero only)

| # | Say (one line) | Type | Expect |
|---|----------------|------|--------|
| 1 | Finance answers from approved Fact memory | `What is our refund period?` | Fact · 30 days |
| 2 | Same chat, no bot picker | `What is our standard discount percentage?` | 15% |
| 3a | Prepare real finance work | `List pending billables for Acme Logistics.` | ~$5,700 lines |
| 3b | Money actions wait for a human | `Draft an invoice for Acme Logistics from those billables.` | Approval queue |
| 4 | Ops badge ≠ Finance | Oscar: same billables/invoice ask | Restricted to Finance |
| 5 | Exec closes the loop | Elena → Approvals → Approve | Invoice ~$5,700 |

---

# If something breaks on live

| Symptom | Fix |
|---------|-----|
| Authorization header missing | API must not have `JWT_VERIFICATION_KEY` for cookie UI demo; `RUNTIME_ENV=dev` for temporary demo; recreate API |
| Invalid credentials | Password is `bizos-dev-password`; workspace `acme` |
| No billables / wrong memory | `docker compose … exec api python -m scripts.reset_dhanu_demo` |
| Room loses who is on screen | Point at purple strip every login |

---

# Do not do on the call

- Do not open a long screenshot PDF and scroll.
- Do not narrate FB-033…051 numbers for ten minutes.
- Do not use Omar / Avery / Vera / Dana if the seed script says use Fiona / Elena / Oscar / Eddie.
- Do not say “Jeanne bot” or open a bot menu — product is **FreedomBot**.
