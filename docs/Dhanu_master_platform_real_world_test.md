# FreedomBot — Master platform real-world test (Dhanu)

**URL:** http://localhost:3001  
**Client slug:** `acme`  
**Password (all users below):** `bizos-dev-password`

One person per job. Do not mix with leftover RBAC test accounts.

**Before testing:**

```bash
docker compose up -d --build
docker compose exec api python -m scripts.reset_dhanu_demo
```

The reset puts billables back to pending, removes draft invoices, cancels pending approvals, retires leftover test memory and restores every user's access. Run it before each full walkthrough, and use a **New chat** for each person.

A correct block always names the area, e.g. *"That's restricted to the Finance area, which your account doesn't have access to — an admin can grant it."* "No record", a guess, or an offer to create it is a **FAIL**.

Word copy: [Dhanu_master_platform_real_world_test.docx](./Dhanu_master_platform_real_world_test.docx)

---

## Accounts for THIS demo only (use these 7)

| Person | Email | Job in the story | Department access | UI badge (permission) |
|--------|-------|------------------|-------------------|------------------------|
| Fiona Finance | `finance@acme.example.com` | Accountant | finance | OPERATOR |
| Oscar Ops | `ops@acme.example.com` | Operations coordinator | ops | OPERATOR |
| Hannah HR | `hr@acme.example.com` | HR recruiter | hr + salary | OPERATOR |
| Eddie Employee | `employee@acme.example.com` | Employee | general only | VIEWER |
| Lara Lead | `lead@acme.example.com` | Team lead | ops | DRAFTER |
| Elena Exec | `exec@acme.example.com` | Executive (approves) | exec, finance, legal, hr, ops | APPROVER |
| Ada Admin | `admin@acme.example.com` | Admin (config) | all | ADMIN |

### Important

- **OPERATOR** on the badge = permission level (can run tools). It is **not** “operations department.”
- Fiona = accountant; Oscar = ops. Both may show OPERATOR — that is OK.
- Department = **Restricted data access**, not the badge name.
- Each scenario names **exactly one** login. No “or Avery / Omar.”

### Do NOT use for this walkthrough

| Skip | Email | Why |
|------|-------|-----|
| Omar Operator | `operator@acme.example.com` | Duplicate finance access vs Fiona |
| Avery Approver | `approver@acme.example.com` | Overlaps Elena — use Elena only |
| Vera Viewer | `viewer@acme.example.com` | Use Eddie instead |
| Dana Drafter | `drafter@acme.example.com` | Use Lara instead |

---

## Scenario A — Department buckets

**A1 — Fiona only** (`finance@…`)  
Ask discount → **15%**. Ask operating hours → **"restricted to the Operations area"**, no hours.

**A2 — Oscar only** (`ops@…`)  
Ask discount → **"restricted to the Finance area"**, no 15%, no offer to create a policy. Ask hours → **09:00–18:00 ET**.

**A3 — Hannah then Fiona**  
Hannah: Engineer II salary → **$120k–$140k**. Fiona: same → **"restricted to the Salary / compensation area"**.

---

## Scenario B — Configure (Ada only)

Admin → Eddie → turn **ops** ON → as Eddie ask payroll process → sees the 5-step SOP → turn ops **OFF** → Eddie (New chat) asks again → **"restricted to the Operations area"**.

---

## Scenario C — Approvals (Fiona + Elena only)

1. Fiona: pending billables Acme Logistics → ~$5,700.  
2. Fiona: draft invoice → queued.  
3. **Elena Exec only** (not Avery): Approvals → card says **Requested by Fiona Finance (OPERATOR)** → Approve → draft invoice DRAFT-… **$5,700** created.

---

## Scenario D — Payroll process (Oscar only)

Ask: same payslip process as last month → SOP **Monthly payroll from attendance**.  
(Do not use Lara here — she is for Scenario G.)

---

## Scenario E — HR match (Hannah, then Eddie)

Hannah: Jordan Lee vs Ops Coordinator → **Likely fit, 4/4 must-haves, missing SQL basics (nice-to-have)**, human decides.  
Eddie: same → **"restricted to the HR area"**.

---

## Scenario F — Invoice wall

Fiona (after C): list/draft again → **"no pending billables — nothing to invoice"**, points to the existing $5,700 draft; nothing new in Approvals. "Show invoices for Acme Logistics" → DRAFT-… $5,700.  
Oscar: list/generate → **"requires Finance access"**, no amounts.

---

## Scenario G — Levels (exactly three)

| Login | Expect |
|-------|--------|
| Eddie | Scope of work yes; full MSA "restricted to the Legal area" |
| Lara | Payroll SOP yes; board Q3 "restricted to the Executive / board area" |
| Elena | Board Q3 hiring freeze yes |

---

## Scorecard

| # | Check | Pass? |
|---|-------|-------|
| 1 | Only the 7 accounts above (no Omar/Avery/Vera/Dana) | |
| 2 | Fiona ≠ Oscar (finance vs ops) | |
| 3 | Ada toggles Eddie’s ops | |
| 4 | Fiona drafts; Elena alone approves | |
| 5 | Oscar replays payroll SOP | |
| 6 | Hannah match; Eddie blocked | |
| 7 | Oscar blocked on billables | |
| 8 | Eddie / Lara / Elena different info | |

Tester: ______________  Date: __________  
