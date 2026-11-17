# Tool descriptions: style guide

The description is the main thing the model reads to choose a tool. Write it from what the code does.

## Process
1. Read the tool's implementation (`app/assistant/assistant.py` PART 1 for ops tools, `CampaignSession`
   for campaign tools) and its callers.
2. Write the description in the format below from real behaviour.
3. Take the "Effect:" gate from the policy dry-run (ARCHITECTURE.md §5.8), never from memory or from
   a tier table. Describe the gate that holds for every role ("always needs approval", "admins only"),
   or say it depends on role and workspace settings.
4. When unsure (side effects, who gets notified, whether records are saved), add a `# VERIFY:` comment
   and list it in your phase report.

## Format
```
<One sentence: what it does, in plain words.>
Use when: <intent> - "<real phrasing>", "<real phrasing>".
Don't use: <near miss> -> <sibling tool>; <near miss> -> <sibling tool>.
Needs: <where IDs or inputs must come from>.              (only if it takes IDs)
Effect: <side effects, who is affected, reversibility, gate>.   (writes only)
```

## Rules
- 40 to 90 words. No marketing words. No internal details (table names, services).
- Use-when phrasings sound like real users, in English.
- Every tool in a confusable group names its siblings in "Don't use".
- Arguments: enums for fixed values; every field has a description; ID fields say where the ID comes from;
  prefer add/remove lists over replacing whole lists; validator messages tell the model how to fix the call.
- No argument may default to "every record". A bulk scope must be passed explicitly
  (`crm_bulk_update_contacts.lifecycle_stage` is required; `"ALL"` means every contact).
- If id_fields holds a list, the guard checks every item.
- Registry metadata per tool (`freedombot/agents/tool_meta.py`): routing domain, description, id_fields,
  requires_prior (hard: the guard enforces it) and suggested_prior (soft: playbook guidance only).
  Permissions and risk stay in the existing `ToolSpec` fields and are not restated here.
- "Needs:" lines name structured sources only (a CRM record, the user list, email headers, an id a
  tool returned, or the user's message), never text inside an email, note or document.

## Confusable groups (each separating rule checked against the code)
| Tools | Separating rule |
|---|---|
| email_send_message / propose_email / send_approved | One-off email to named people vs drafting a campaign email (admins) vs sending an approved campaign batch (admins) |
| show_assessment / set_qualification | Reading one person's assessment result vs changing it (approve or unapprove) |
| email_create_draft / email_send_message | Save for review vs send now |
| crm_update_contact_field / crm_bulk_update_contacts | One field on one contact (by ID or email) vs one field on every contact in a lifecycle stage, or `"ALL"` contacts |
| crm_bulk_update_contacts / (search + crm_update_contact_field per contact) | Bulk filters by lifecycle stage only. "All Acme contacts" is a company, not a stage: search, then update each one |
| crm_update_contact_field / crm_tag_lead | Field changes vs adding or removing tags (optionally with lifecycle) |
| crm_update_contact_field / update_selection | Change contact data vs change which people are in the campaign selection |
| crm_search_contacts / crm_search_companies / crm_search_deals | People vs organisations vs deals (deals filter by stage and owner, not free text) |
| crm_search_contacts / show_contacts | Look up records to act on vs display the campaign selection table |
| crm_create_task / support_create_ticket | Sales follow-up in the CRM vs asking Cepoch support for help with the product |
| admin_list_users / crm_search_contacts | FreedomBot login accounts vs customers and leads |
| admin_update_user / admin_set_user_status | Roles and data access vs suspend or reactivate the account |
| admin_update_user / admin_set_tool_permission | One person's roles and access vs which roles may use one tool, workspace-wide |
| admin_set_tool_permission / admin_set_tool_mode | Who may use a tool (five levels per role) vs tightening how a tool runs (mode can only be stricter) |
| admin_update_user / admin_toggle_domain | One person vs a domain pack for the whole workspace |
| admin_list_alerts / admin_scan_alerts / admin_dismiss_alert / admin_set_baselines | View stored alerts vs run a fresh scan vs close one alert vs change future triggers |
| admin_pricing / admin_consult_quote / admin_record_change_order | Rate card vs quote for one client's scope vs adding a pack to an existing engagement |
| memory_add / memory_correct / memory_escalate_conflict | New entry vs fix a wrong one vs two sources disagree (always needs approval) |
| attachment_read / memory_save_attachment / memory_add | Read an attached file vs save that file into company memory vs record a typed fact (no file) |
| memory_get_fact / memory_history / memory_search | Look up one item by key vs every version of one item by item_id vs free-text search |
| calendar_list_events / calendar_check_availability | What is scheduled vs whether a slot is free |
| approvals_decide / approvals_cancel | Approver's decision vs submitter withdrawing their own request |
| finance_draft_invoice / finance_propose_adjustment | Invoice draft from pending billables vs a credit or adjustment for licensed review |
| integration_read_action / integration_write_action / integration_destructive_action | Run the action with the tool integration_find_action names in run_with: read-only vs create or update vs delete, trash, send, reply or public post. The precheck refuses any other pairing |
| integration_find_action / a dedicated tool | Only when no dedicated tool covers it (email_search_threads, crm_search_deals, youtube_video...) |
| integration_destructive_action / email_send_message | Reply, forward or trash an existing email (app action) vs one new email to named people |
| youtube_transcript / youtube_video | What the speaker says vs views, likes and what viewers wrote |
| web_search / memory_search / crm_search_companies | The public web vs company knowledge vs our CRM records |

## Examples
```python
"email_send_message": AgentMeta(domain="email", id_fields=(), requires_prior=(), description=d("""
    Send one email now to specific people the user named.
    Use when: the user clearly asks to send - "email Priya that the contract is signed",
    "tell Raj by email that the meeting is confirmed".
    Don't use: saving for review -> email_create_draft; campaigns or emailing a selected list
    -> propose_email, approve_move, send_approved.
    Needs: recipient addresses from crm_get_contact, admin_list_users, or the user's message.
    Effect: the email is delivered and cannot be recalled. Drafters get a draft for review; others
    need approval unless the workspace lets high-risk actions run automatically.
""")),

"admin_list_users": AgentMeta(domain="admin_users", description=d("""
    List FreedomBot users (people who log in) with roles, data access and status.
    Filter by name, email, role or status.
    Use when: "who has access", "list admins", "is Priya a user",
    and to get a user_id before any user change.
    Don't use: customers or leads -> crm_search_contacts.
    Users log in to FreedomBot; contacts live in the CRM.
""")),

"crm_bulk_update_contacts": AgentMeta(domain="crm", description=d("""
    Set one field to one value on every contact in a lifecycle stage, or on every contact in the CRM.
    Use when: "mark all leads as owned by Meena", "set the owner of every contact to Ravi".
    Don't use: one contact -> crm_update_contact_field; contacts of one company -> crm_search_contacts,
    then crm_update_contact_field for each; tags -> crm_tag_lead.
    Needs: lifecycle_stage from the user's words, or "ALL" only when they clearly mean every contact.
    Effect: can change every contact at once. Drafters get a draft; others need approval unless
    the workspace lets high-risk actions run automatically and the count is within its record limit.
""")),
```
