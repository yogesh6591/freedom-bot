# Onboarding branch: live test script

Covers what the `onboarding` branch adds:
- CRM records saved where each user chooses: FreedomBot (default), HubSpot or Google Drive.
- Company and deal creation without duplicates.
- Recurring scheduled tasks.
- No duplicate social posts.
- Only 8 apps.

## 0. Deploy to the live server

This changes the live site for every client.

```bash
cd ~/freedom-bot-core && git fetch && git checkout onboarding && git pull
docker compose -p freedom-bot-core -f compose.prod.yaml up -d --build
docker logs fb-core-api 2>&1 | grep -i "scheduled tasks"     # the scheduler started
```

- **Roll back:** `git checkout agentv2 && docker compose -p freedom-bot-core -f compose.prod.yaml up -d --build`
- **If chat says "reasoning.effort is not supported with this model":** set `MODEL_REASONING_EFFORT=none` in the server `.env`, then rebuild.
- **Composio:** Google Drive, Sheets, Docs, HubSpot and Facebook must each have an auth config, or they can't be connected.

## Accounts

| Who | Login | Role |
|---|---|---|
| Amelia | admin@acme.example.com | Admin. In Acme, admins skip approval. |
| Omar | operator@acme.example.com | Operator. His changes wait in Approvals. |
| Viewer | any viewer account in Acme | Read-only. |

Before you start: Amelia connects **Google Sheets** and **Google Drive** on Integrations. HubSpot is optional; leave it disconnected for section A.

## A. Where CRM records are saved

| # | Who | Type in chat (or do) | Expected |
|---|---|---|---|
| A1 | Amelia | Where are my CRM records saved? | "FreedomBot" (choice: Automatic). |
| A2 | Amelia | Open Integrations | A **Save CRM records in** box showing FreedomBot. HubSpot shows "(connect it first)". |
| A3 | Amelia | Add QA NovaTech Solutions as a company, domain qa-novatech.example.com, industry Information Technology. | Company created, ID starts with `co_`. Reply says **saved in FreedomBot**. |
| A4 | Amelia | Add the company QA NovaTech Solutions again. | "already exists", same ID, nothing new added. |
| A5 | Amelia | Save Ethan Parker, ethan.parker@qa-novatech.example.com, CEO at QA NovaTech Solutions, as a contact in HubSpot. | Refused: "HubSpot is not connected for you, so nothing was saved there". Nothing in Approvals. |
| A6 | Amelia | From now on save my CRM records in Google Drive. | "New CRM records will be saved in Google Drive, in a sheet named FreedomBot CRM". |
| A7 | Amelia | Add the company Initech, domain initech.com. | **Saved in Google Drive.** It may first wait in Approvals because saving to Drive is high-risk; approve it. Drive now has a **FreedomBot CRM** sheet with Contacts, Companies, Deals, Notes and Tasks tabs, and Initech is in Companies. |
| A8 | Amelia | Add Lisa Ray, lisa@initech.com, CTO at Initech, then create a $12,000 deal "Initech Pilot" for Initech with Lisa as the contact, closing 2026-12-31. | Lisa in the Contacts tab; the deal in the Deals tab, linked to Initech and Lisa. |
| A9 | Amelia | Create the deal "Initech Pilot" for Initech again. | "already exists in your FreedomBot CRM sheet", no new row. |
| A10 | Amelia | Show my deals. / What's my pipeline? | Lists Initech Pilot $12,000, from Google Drive. |
| A11 | Amelia | Move the Initech Pilot deal to won. | Says this is changed in the sheet itself, gives the link, changes nothing. |
| A12 | Amelia | Integrations: open **Open the FreedomBot CRM sheet** | Opens the same Google Sheet. |
| A13 | Amelia | Save Ravi Kumar, ravi@globex.example.com, as a contact in FreedomBot. | **Saved in FreedomBot**, because naming a place in the message wins over the saved choice. Not in the sheet. |
| A14 | Amelia | Disconnect Google Sheets on Integrations, then: Add a contact Maya Lee, maya@umbrella.example.com. | Saved in FreedomBot, with the note "Your saved choice is Google Drive, but it is not connected now…". |
| A15 | Amelia | Set my CRM storage back to automatic. | "saved automatically: HubSpot when it is connected, otherwise FreedomBot". |
| A16 | Omar | From now on keep my CRM in FreedomBot. | Waits in Approvals. Amelia approves. Omar asks "Where are my CRM records saved?" and gets FreedomBot. |
| A17 | Omar | Integrations: change **Save CRM records in** to FreedomBot | "Sent for approval…". |
| A18 | Viewer | From now on save my CRM records in FreedomBot. | Not permitted. On Integrations the box is hidden, or changing it is refused. |

## B. HubSpot (only if a HubSpot account is connected)

| # | Who | Type in chat | Expected |
|---|---|---|---|
| B1 | Amelia | Connect HubSpot on Integrations, then: Where are my CRM records saved? | Automatic means **HubSpot**. |
| B2 | Amelia | Add QA NovaTech Solutions as a company. | Created in HubSpot, or "already exists" with its HubSpot ID. Check in HubSpot. |
| B3 | Amelia | Create a $25,000 deal "QA NovaTech AI Automation Project" for QA NovaTech Solutions, stage proposal, closing 2026-12-31. | A HubSpot deal in "Presentation scheduled", linked to the company. Running it again says "already exists". |
| B4 | Amelia | Add Olivia Carter, olivia.carter@qa-novatech.example.com, as a contact in FreedomBot. | Saved in FreedomBot, not HubSpot. |

## C. Recurring scheduled tasks (the scheduler checks every minute)

| # | Who | Type in chat | Expected |
|---|---|---|---|
| C1 | Amelia | Every day at `<5 minutes from now>` email me a short summary of my unread emails. | Scheduled, ID starts with `sch_`, with the next run time. |
| C2 | Amelia | Repeat C1 exactly. | "already scheduled", no second copy. |
| C3 | Amelia | (after the time) What have I scheduled? Did it run? | Last run "done" with a short result; the run also shows as its own chat. |
| C4 | Omar | Every Monday at 9 AM write a short AI tip and post it on our Facebook Page. | Setting it up waits in Approvals. Each Monday's post also waits for approval. |
| C5 | Amelia | Stop my daily unread email summary. | "stopped". It's gone from "What have I scheduled?". |

## D. No duplicate posts (needs Facebook connected)

| # | Who | Type in chat | Expected |
|---|---|---|---|
| D1 | Amelia | Post on our Facebook Page: "FreedomBot duplicate test 11 Nov - please ignore." | Posted. |
| D2 | Amelia | Post exactly the same text again. | "Not posted: the same content was already published there on … Nothing was run." |
| D3 | Amelia | Post "FreedomBot duplicate test 11 Nov - version 2." | Posted (different text). |

## E. Only the 8 apps

| # | Who | Do | Expected |
|---|---|---|---|
| E1 | anyone | Integrations page | Only Gmail, Google Calendar, Google Drive, Google Sheets, Google Docs, YouTube, HubSpot, Facebook. Searching "Slack" finds nothing. |
| E2 | Amelia | Send a Slack message to James saying hello. | Refused: FreedomBot works with the 8 apps only. |

## Checks from the server

```bash
DB="docker exec fb-core-db psql -U bizos -d bizos_ws_acme -c"
# Each person's CRM storage choice and their Drive sheet
$DB "SELECT owner_user_id, destination, sheet_url, updated_at FROM crm_storage_choice;"
# Records saved in FreedomBot's own CRM
$DB "SELECT id, name, domain FROM crm_companies ORDER BY created_at DESC LIMIT 5;"
$DB "SELECT id, name, amount, stage FROM crm_deals ORDER BY created_at DESC LIMIT 5;"
# Schedules and posts
$DB "SELECT id, title, status, next_run_at, last_status FROM scheduled_tasks ORDER BY created_at DESC;"
$DB "SELECT app, target, left(preview,60), posted_at FROM published_posts ORDER BY posted_at DESC;"
```
