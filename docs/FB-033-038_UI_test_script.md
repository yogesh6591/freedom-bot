# FreedomBot UI test script — FB-033 to FB-038

**Where:** http://localhost:3001  
**Client slug on login:** `acme`  
**Password for all demo users:** `bizos-dev-password`

| Role | Email |
|------|--------|
| Operator | `operator@acme.example.com` |
| Approver | `approver@acme.example.com` |
| Admin | `admin@acme.example.com` |
| Viewer | `viewer@acme.example.com` |

Stack must be up (`docker compose up -d`). If Chat answers look wrong after a code change, rebuild: `docker compose up -d --build api ui`.

---

## Before you start

1. Open http://localhost:3001/login
2. Enter email, password, client slug **acme**
3. Click **Sign in**
4. Left nav should show: **Chat · Memory · Approvals · Audit** (Admin only for admin)

---

## FB-034 — One company chat + persona

**Login as:** Operator

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Click **Chat** | Title **FREEDOMBOT** (not Jeanne). Badge **WAIT_FOR_APPROVAL**. **No** domain dropdown / Bronze-Silver-Gold menu |
| 2 | Click **New conversation** (if present) | Empty chat |
| 3 | Type: `What are our Q3 goals and priorities?` → **Send** | Reply about growing enterprise pipeline **25%**. Under reply: **Handled as: strategy**. Tool used: `strategy_list_priorities` |
| 4 | In the **same** chat type: `ok, what refund terms apply?` → **Send** | Reply about **30 days**. **Handled as: finance**. You did not re-enter company context |
| 5 | Type: `Are you Jeanne?` → **Send** | Says it is **FreedomBot** / Acme’s AI assistant, **not** Jeanne |

**Pass:** one chat, auto routing, never presents as Jeanne.

---

## FB-036 — Fact vs estimate + conflicts

### A. Fact label

**Login as:** Operator → **Chat** → **New conversation**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Type: `What is our refund period?` → **Send** | Answer starts with **Fact:** and **30 days** |

### B. Conflicting policies

**Login as:** Admin → **Chat** → **New conversation**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Type: `Record a fact with key demo_cap_a, title Discount cap A, content Max discount 10%, topic demo discount cap` → **Send** | Confirms recorded |
| 2 | Type: `Record a fact with key demo_cap_b, title Discount cap B, content Max discount 20%, topic demo discount cap` → **Send** | Second value held as pending / conflict (not treated as the final fact alone) |
| 3 | Click **Memory** in the left nav | Panel **Conflicting policies** with topic **demo discount cap** and both values |
| 4 | Sign out. Login as **Approver**. Click **Memory** | Same conflict panel. Button **This one stands** on the pending side |
| 5 | Click **This one stands** on the value you want | Conflict clears. Losing value stays only in history |

**Pass:** Fact: / Estimate: labels; conflicts need a person; agent does not pick a side.

---

## FB-037 — Restricted data + see vs run

### A. Restricted memory (row filter)

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Login as **Operator** → **Memory** | You do **not** see `salary_bands` or `hr_leave_policy_internal`. Board/exec items may be missing |
| 2 | Sign out. Login as **Approver** → **Memory** | You **do** see **board_q3_plan** (exec). You still do **not** see salary |
| 3 | Sign out. Login as **Admin** → **Memory** | You see **salary_bands**, **hr_leave_policy_internal**, **board_q3_plan**. Rows may show `restricted: salary` / `hr` / `exec` |

### B. Grant a scope

**Login as:** Admin → **Admin** → **Users**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Find **operator@…** | Column **Restricted data access** with toggles: exec / hr / salary / finance / legal |
| 2 | Click **salary** so it is on | Operator now has salary scope |
| 3 | Sign out. Login as **Operator** → **Memory** | **salary_bands** now visible |
| 4 | (Cleanup) Admin → Users → turn **salary** off on operator again | Operator loses salary again |

### C. See a process vs run it

**Login as:** Viewer → **Chat**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Look under FREEDOMBOT header | Text like **X tools you can run · … · Y visible but not runnable for your role** |
| 2 | Try: `Send an email to buyer@example.com saying hello` → **Send** | Refused / not sent. Viewer cannot run the write |

**Pass:** wrong role never gets restricted rows; view ≠ run.

---

## FB-035 — Only paid domains

**Login as:** Admin → **Admin** → **Domains**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Open Domains tab | Each domain: **purchased** or **add-on**. Acme demo has all **purchased** and enabled |
| 2 | For a **purchased** domain that is not `general` | Button **Disable** / **Enable** works |
| 3 | For an **add-on** (on a new client, or after you mark one unpaid) | Button **Record change order** — not a free Enable |
| 4 | Click **Record change order** → enter `CO-TEST-1` → OK | Domain becomes purchased and can be enabled. Audit later shows **CHANGE_ORDER** |

**Note:** Acme already purchased everything for the demo. To see the 409 / change-order path clearly, provision a second client (see FB-033) with only the base package, then try to enable **legal** or **finance**.

**Pass:** unpaid domain cannot be switched on without a change order.

---

## FB-038 — Modes, approval queue, audit

### A. Per-tool mode (Admin)

**Login as:** Admin → **Admin** → **Tools**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Find `email_send_message` or a CRM write tool | Column **Tool mode** — often `WAIT_FOR_APPROVAL` |
| 2 | Find a read tool like `memory_search` | Mode often blank (workspace) or `AUTO_WITHIN_SCOPE` |
| 3 | Per role columns | Shows permission plus **view / run** under each |

### B. Approval queue

**Login as:** Operator → **Chat** → **New conversation**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Type: `Draft and send an email to buyer@example.com saying thanks for the meeting` → **Send** | Banner **Awaiting approval**. Nothing actually sent. Link to approval queue |
| 2 | Click **Approvals** in the left nav (or the link) | Pending action listed |
| 3 | Sign out. Login as **Approver** → **Approvals** | Same pending item |
| 4 | Click **Approve** (or Reject) | Status updates. If approved, execution may run next |

Header badge **APPROVAL REQUIRED** / mode **WAIT_FOR_APPROVAL** stays visible during this test.

### C. Audit log

**Login as:** Approver or Admin → **Audit**

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Open Audit | Rows with **When · Event · User · Tool · Mode · Decision · Status** |
| 2 | Filter event type **POLICY_DECISION** or **TOOL_CALL** | Mode column shows e.g. `WAIT_FOR_APPROVAL` |
| 3 | (Admin only) Click **Apply retention** if shown | Only old rows past the window can be deleted; a purge is itself logged |

**Pass:** writes wait in Approvals; audit records who / what / tool / mode.

---

## FB-033 — Isolated FreedomBot per client

UI alone cannot create a second deployment. Use the script once, then prove isolation in the UI.

### A. Provision (terminal, once)

```bash
cd /Users/gaurav/Desktop/yash/freedombot_9sep
.venv/bin/python -m scripts.provision_client \
  --name "Globex Ltd" --slug globex \
  --admin-email owner@globex.example.com \
  --admin-password 'a-strong-test-password'
```

Expect: `deploy/clients/globex/client.env` and `compose.yaml`. DB created for Globex.

### B. Prove walls in the UI

| Step | Do this | You should see |
|------|---------|----------------|
| 1 | Login Acme as Operator → **Memory** | Acme facts (refund period, goal_1, etc.) |
| 2 | Sign out. Login with email `owner@globex.example.com`, password from above, client slug **globex** | Workspace is Globex, not Acme |
| 3 | Open **Memory** / **Chat** | **No** Acme refund period / Acme goal. Empty or template starter SOPs only |
| 4 | Ask in Chat: `What is our refund period?` | No Acme “30 days” fact |

**Pass:** one client never sees another client’s data.

---

## Suggested order (about 20 minutes)

1. **FB-034** Operator Chat (routing + Jeanne)  
2. **FB-036** Fact: + conflict (Admin then Approver)  
3. **FB-037** Memory by role + Viewer see/run  
4. **FB-038** Tools → send email → Approvals → Audit  
5. **FB-035** Domains tab (change order if testing a new client)  
6. **FB-033** Provision Globex + login switch  

---

## Quick pass / fail checklist

| FB | Pass if |
|----|---------|
| **034** | No domain menu; strategy then finance in one chat; never “I am Jeanne” |
| **036** | Refund answer uses **Fact:**; conflicts show in Memory; Approver resolves |
| **037** | Operator ≠ salary; Admin sees salary; Viewer cannot send email |
| **035** | Domains show purchased / add-on; change order required for unpaid |
| **038** | Email goes to Approvals; Audit has Mode column |
| **033** | Globex login never shows Acme memory |

---

## Related

- Approach / design notes: `docs/FB-033-038_approach.md`
- Automated tests: `tests/test_fb033_038.py` (`pytest tests/test_fb033_038.py -q`)
