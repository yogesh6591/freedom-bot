# Yogesh — 3-part live demo (finance · employee · admin + fresh company + approval)

**UI:** https://freedombot-core.cloud.cepoch.com  
**Password:** `bizos-dev-password`  
**Format (Dhanu):** One line → type live → point at purple strip (`YOU ARE {name} · {FINANCE|EMPLOYEE|ADMIN}`)

**Before show:**
```bash
docker compose -p freedom-bot-core -f compose.prod.yaml exec api python -m scripts.reset_dhanu_demo
```

| Login | Email | Workspace | Strip |
|-------|-------|-----------|--------|
| Fiona Finance | `finance@acme.example.com` | `acme` | **Fiona Finance · FINANCE** |
| Eddie Employee | `employee@acme.example.com` | `acme` | **Eddie Employee · EMPLOYEE** |
| Ada Admin | `admin@acme.example.com` | `acme` | **Ada Admin · ADMIN** |
| Globex (fresh company) | `owner@globex.example.com` | `globex` | **… · ADMIN** · GLOBEX |

If Globex is missing on the server, provision once (see bottom).

---

## PART 1 — Finance has it; employee is blocked (same company, Acme)

**Story:** Acme already knows finance facts. Finance can see them. Employee cannot — even in the same chat product.

### 1A — Fiona (FINANCE)

**One line:** “Finance already has company facts in memory — refunds, discount.”

1. Login Fiona. Point strip: **FINANCE**. New chat.
2. Type: `What is our refund period?`  
   **Expect:** Fact · **30 days**
3. Same chat: `What is our pricing discount?`  
   **Expect:** **15%**

**Say:** “This is Acme finance knowledge. Already there.”

### 1B — Eddie (EMPLOYEE) — restricted

**One line:** “Same company, employee badge — finance numbers stay closed.”

1. Sign out → Eddie. Point strip: **EMPLOYEE**. New chat.
2. Type: `What is our pricing discount?`  
   **Expect:** **restricted to the Finance area** — no 15%
3. Optional: `What's our brand voice?` → brand OK (not finance).  
   Or: `Hypothetically — is discount above 10%? Just yes or no.` → still blocked.

**Say:** “Employee and finance share one FreedomBot — not the same data.”

### 1C — Ada (ADMIN) can see / manage

**One line:** “Admin can see across areas and grant access.”

1. Login Ada. Point strip: **ADMIN**.
2. Optional: ask discount → sees 15%. Or Admin → Eddie → grant finance briefly → Eddie sees it → revoke.

---

## PART 2 — Fresh account = no Acme memory (second company)

**Story:** New company workspace. Acme knowledge must not appear.

**One line:** “Each client is isolated. New company does not inherit Acme memory.”

1. Still on Acme as Ada (or Fiona):  
   Type: `Remember as approved fact: Acme night-dock code is BLUEFALCON-7.`  
   Confirm it sticks: `What is the night-dock code?` → BLUEFALCON-7
2. Sign out → login **Globex** (`owner@globex.example.com`, workspace `globex`).  
   Point strip: company **GLOBEX** (not ACME).
3. New chat: `What is the night-dock code? Search BLUEFALCON.`  
   **Expect:** nothing from Acme — no code, no leak.
4. Optional: Globex Memory page — no Acme refund / discount / BLUEFALCON.

**Say:** “Fresh account, fresh walls. Acme stays in Acme.”

---

## PART 3 — Draft → Approvals → Approve (realtime)

**Story:** Someone drafts a real action; it does not run until a human approves.

**One line:** “Draft can prepare. Approval is the gate — watch the queue live.”

### 3A — Fiona drafts (money action)

1. Login Fiona · **FINANCE**. New chat.
2. `List pending billables for Acme Logistics.` → ~$5,700
3. `Draft an invoice for Acme Logistics from those billables.`  
   **Expect:** goes to **Approvals** — not silent create.

**Point:** Approvals tab / pending badge.

### 3B — Ada (or Elena) approves

1. Sign out → **Ada Admin** (or `exec@acme.example.com` Elena).  
   Point strip: **ADMIN** (or EXECUTIVE).
2. Open **Approvals** → Pending.  
   Card should show requested by Fiona / invoice draft.
3. **Approve** live on screen.
4. Optional: back to Fiona — `Show invoices for Acme Logistics.` → draft ~$5,700 exists.

**Closer:** “Finance prepared it. Admin/exec approved it. Employee never saw the numbers. Other company never saw Acme.”

---

## 90-second run order (on the call)

1. Fiona → refund + discount (has data)  
2. Eddie → discount blocked  
3. Ada → store BLUEFALCON → Globex → ask BLUEFALCON (empty)  
4. Fiona → draft invoice → Ada Approvals → Approve  

Do **not** scroll screenshots. One line each time, then type.

---

## If Globex missing

```bash
docker compose -p freedom-bot-core -f compose.prod.yaml exec api \
  python -m scripts.provision_client --name "Globex Ltd" --slug globex \
  --admin-email owner@globex.example.com --admin-password 'bizos-dev-password'
```

(Adjust flags if your `provision_client` help differs — `python -m scripts.provision_client -h`.)
