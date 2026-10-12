# FB-045 — First-client launch checklist

Automated isolation tests pass in CI / local pytest. Remaining work is **human sign-off on the real first-client deployment**.

## In the product

Admin → **Launch** tab, or:

- `GET /api/launch/checklist`
- `POST /api/launch/checklist/items` `{ "item_id", "status": "done"|"waived" }`
- `POST /api/launch/sign-off` `{ "force": false }`

## Checklist

| Id | Item | Who |
|----|------|-----|
| `isolation_tests` | Tenant-wall / M01 suite green | Eng |
| `assessment_handoff` | Checkout/contract or manual handoff prefills setup | Ops |
| `baselines` | At least one sold-workflow baseline on Setup | Ops |
| `exclusions` | Out-of-scope list written | Ops |
| `order_dpa` | Order/DPA categories + OpenAI-no-BAA reviewed | Ops / Jeanne |
| `roles_approvers` | Client admin / operator / approver can sign in | Ops |
| `smoke_chat` | One grounded Q&A + one approval path on real DB | Ops |
| `sign_off` | Owner records go-live | Jeanne |

## Sale webhook (FB-043)

After checkout or contract, the sale system POSTs:

```http
POST /api/owner/sale-events
X-FreedomBot-Sale-Secret: <SALE_WEBHOOK_SECRET>
Content-Type: application/json

{
  "event": "checkout",
  "client_slug": "acme",
  "assessment": { "goals": ["…"], "systems": ["…"], "exclusions": ["…"] }
}
```

That starts First-client setup automatically — no paste into the admin UI.

- `event` is `checkout`, `contract` or `sale`. Wire both checkout and contract if
  you have both: the same assessment is handed off once, and the later event
  returns `"duplicate": true` without changing anything.
- A deployment pinned to one client (`CLIENT_SLUG`) refuses other clients' sales (404).
- Each handoff is audited in the client's workspace as `ASSESSMENT_HANDOFF_AUTO`.
- The admin setup screen shows "Started automatically after checkout/contract";
  the pasted-JSON box stays only as a fallback.

## Order/DPA setting (FB-039)

`order_dpa_allows` (`phi`, `card`, `child`, `biometrics`) is the one per-client
setting; anything not listed is refused. PHI additionally needs
`llm_baa_covered` — the model vendor comes from `MODEL_ID`, and OpenAI has no
BAA, so PHI stays blocked in tool calls *and* is refused in chat before it
reaches the model.
