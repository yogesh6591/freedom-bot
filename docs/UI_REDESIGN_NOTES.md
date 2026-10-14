# FreedomBot Enterprise UI Redesign

## Overview

This package is the existing FreedomBot application source with a large-scale presentation redesign. The design moves away from the original purple gradient / horizontal pill-navigation interface and towards a restrained enterprise workspace built around clear typography, navigation, permissions, and repeatable control patterns.

## What changed

- **Workspace shell:** dark slate sidebar; product mark; company/workspace identification; role-gated groups for Assistant, Memory, Approvals, Support, Audit, Administration, and Platform owner; user/account footer; clear current-page state; no oversized demo-persona strip.
- **Navigation & mobile:** dedicated top utility bar; responsive slide-out menu; Escape and focus management; keyboard-visible controls.
- **Conversation UI:** separate recent-conversation rail; clearer conversation and assistant hierarchy; proper reading column; useful starter prompts; restrained action signals; composer with multiline input and keyboard hint.
- **Login:** editorial split layout with clear authentication fields, optional workspace identifier, accessible show/hide password, and security context. Login payload and session refresh remain unchanged.
- **System-wide UI:** neutral / evergreen design tokens, light and dark mode, improved typography, standardized buttons, forms, tabs, badges, panels, tables, loading/empty/error states and focus treatment.
- **Feature screens:** consistent headers and information hierarchy across Memory, Approvals, Audit, Admin, Support and Owner.

## Preserved behavior

No backend Python source, route contract, workspace isolation logic, approval flow, authorization logic, or data model has been modified. The actual frontend continues to call the existing API and use the existing session provider. Sample data is **not** present in the application source.

## Validation performed

- TypeScript strict check (`tsc --noEmit`): PASS.
- ESLint on changed UI files: PASS.
- Tailwind CSS compilation: PASS.
- Isolated render tests for all eight routes (chat, login, approvals, memory, admin, audit, support, owner) using production React components and mock context: PASS.
- A complete `next build` could not be executed in the preparation environment: the supplied dependency folder contained macOS SWC binaries, and the Linux SWC binary could not be downloaded in this network-restricted environment. This **is not** a passing production build. Run a fresh dependency installation and full build in the target environment before deployment.
- Live backend API, browser screenshot, cross-browser/device, E2E, deployment, and accessibility audit checks have not been completed. Production sign-off still requires these checks.

## Installation / production verification

From `freedombot_9sep/ui` on your normal development/build machine:

```bash
npm ci
npm run typecheck
npx eslint src
npm run build
```

Use your own `ui/.env.local` based on `ui/.env.example`, with the correct deployed API origin. The source ZIP intentionally omits the original `.env`, `.env.local`, runtime caches, `.next`, and `node_modules`. The existing package lock and both Dockerfiles are included.

## Useful paths

- `ui/src/components/AppShell.tsx` — workspace navigation and shell
- `ui/src/components/BrandMark.tsx` — consistent logo mark
- `ui/src/app/globals.css` and `ui/tailwind.config.ts` — styling foundations
- `ui/src/components/common/primitives.tsx` — buttons, fields, status, cards, tabs, tables
- `ui/src/app/chat/page.tsx` — assistant workspace
- `ui/src/app/login/page.tsx` — authentication experience
- `ui/src/app/{admin,approvals,audit,memory,support,owner}/page.tsx` — feature surfaces

## Preview note

The separate offline design preview uses a bundled client-side harness with **sample data and stubbed network calls**. It is only for visual review, and it is deliberately excluded from this production-source package.
