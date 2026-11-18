# Marketing website: Next.js production build, served by the main app at /website.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked into the pages and redirects at build time: where Sign in and Sign up go.
ARG NEXT_PUBLIC_APP_URL=https://freedombot-core.cloud.cepoch.com
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/next.config.ts ./
USER node
EXPOSE 3100
CMD ["npx", "next", "start", "-p", "3100"]
