# FreedomBot Website

Marketing site for FreedomBot: Home, Features, Pricing, Terms and Privacy.

It is served by the main app at **https://freedombot-core.cloud.cepoch.com/website**. The main app (`ui`)
rewrites `/website/*` to this site's own container, and this site uses `basePath: '/website'`.

- **Sign up** buttons open the main app's onboarding at `/signup` (pricing cards pass `?plan=`).
- **Sign in** buttons open the main app's sign-in at `/`, which goes to the AI chat (`/chat`).
- Old links to this site's `/signup`, `/onboarding/*` and `/login` redirect to the main app.

## Run locally

```bash
cd Website
npm install
cp .env.example .env.local   # NEXT_PUBLIC_APP_URL=http://localhost:3000 to test with a local main app
npm run dev
```

Open [http://localhost:3100/website](http://localhost:3100/website), or through the main app at
`http://localhost:3000/website` (the main app's `WEBSITE_PROXY_URL` defaults to `http://127.0.0.1:3100`).

## Production

`compose.prod.yaml` builds this folder as the `website` service (`fb-core-website`). Build and start it with the ui:

```bash
docker compose -f compose.prod.yaml up -d --build website ui
```
