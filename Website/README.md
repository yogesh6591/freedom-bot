# FreedomBot Website

Marketing site, signup, and self-serve onboarding for FreedomBot.
Sign in goes to the main app (`NEXT_PUBLIC_APP_URL`).

## What’s included

- **Home** — product story, capability packs, onboarding overview
- **Features** — platform capabilities
- **Pricing** — three subscription tiers (Starter / Growth / Enterprise)
- **Terms** & **Privacy**
- **Signup / Login**
- **Onboarding**
  1. Company + workspace identifier
  2. Domain access selection (General, Sales, Planning, Finance, Legal, …)
  3. Plan selection (must cover the domains you picked)
  4. Payment (demo checkout)
  5. Complete → handoff to main FreedomBot login with only those domains enabled

## Run locally

```bash
cd Website
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3100](http://localhost:3100).

The main FreedomBot UI typically runs on port `3000`. After onboarding, customers are sent to:

`NEXT_PUBLIC_APP_URL/?workspace=<slug>`

## Connecting to FreedomBot (creates real workspaces)

After payment the website's server calls FreedomBot's `POST /api/public/signup`, which creates the client
exactly as a platform owner's **New client** form does: its own workspace, the departments chosen, and the
first admin. The completion page then shows the workspace ID (made from the business name), the admin
email and the password, once.

1. On the FreedomBot server, set `SELF_SERVE_SIGNUP_KEY` to a long random value in `.env` and restart.
   Empty means website signup is off.
2. In this website's environment (`.env.local` or the host's settings), set:
   - `FREEDOMBOT_API_URL` to the FreedomBot API, e.g. `https://freedombot-core.cloud.cepoch.com`
   - `SELF_SERVE_SIGNUP_KEY` to the same value. Server-only: never prefix it with `NEXT_PUBLIC_`.

Each address can make 5 signups an hour.

## Still to do before a public launch

1. Payment is still the demo checkout: replace it with Stripe Checkout / Billing, and send the real payment
   reference to `/api/provision`. Until then, anyone who reaches the payment page can create a workspace.
2. The password typed at signup is kept in the browser only until the completion page shows it, then erased.
