import type { Metadata } from 'next'
import { SiteShell } from '@/components/SiteShell'

export const metadata: Metadata = {
  title: 'Privacy policy'
}

const SECTIONS = [
  {
    title: '1. Overview',
    body: 'This Privacy Policy explains how FreedomBot (“we”, “us”) collects, uses, and shares information when you visit this website, create an account, complete onboarding, or use a FreedomBot workspace.'
  },
  {
    title: '2. Information we collect',
    body: 'Account data (name, work email, password hash, company name, workspace identifier), billing details processed by our payment provider, product usage and audit logs, support communications, and website analytics such as pages viewed and approximate location derived from IP address.'
  },
  {
    title: '3. How we use information',
    body: 'We use information to provision and secure workspaces, process subscriptions, provide customer support, improve product reliability, enforce acceptable use, and communicate service-related notices. We do not sell personal information.'
  },
  {
    title: '4. Workspace content',
    body: 'Content you store in FreedomBot (memory, conversations, integrations data) is processed to operate your workspace. Tenant isolation keeps client workspaces separated. Platform restrictions may block certain regulated categories, including health data and card data, before model processing.'
  },
  {
    title: '5. Sharing',
    body: 'We share data with subprocessors that help us host infrastructure, process payments, send transactional email, and provide monitoring — under contractual confidentiality and security obligations. We may disclose information if required by law or to protect users and the platform.'
  },
  {
    title: '6. Retention',
    body: 'Account and billing records are retained while your subscription is active and for a limited period afterward as required for legal, tax, and security purposes. Workspace audit retention follows the plan and customer settings (for example 90 days or one year).'
  },
  {
    title: '7. Security',
    body: 'We apply access controls, encryption in transit, tenant isolation, and audit logging. No method of transmission or storage is perfectly secure; please use strong passwords and protect admin credentials.'
  },
  {
    title: '8. Your choices',
    body: 'You may update account profile details, request access or deletion where applicable, and opt out of non-essential marketing communications. Some retention is required for security and compliance.'
  },
  {
    title: '9. International transfers',
    body: 'If data is processed in another country, we use appropriate safeguards consistent with applicable privacy law.'
  },
  {
    title: '10. Children',
    body: 'FreedomBot is built for business users and is not directed to children under 16. We do not knowingly collect personal information from children.'
  },
  {
    title: '11. Changes',
    body: 'We may update this policy and will post the revised version with a new effective date.'
  },
  {
    title: '12. Contact',
    body: 'Privacy questions: privacy@freedombot.app (or your designated FreedomBot privacy contact).'
  }
]

export default function PrivacyPage() {
  return (
    <SiteShell>
      <section className="section">
        <div className="container-page max-w-3xl">
          <p className="eyebrow">Legal</p>
          <h1 className="mt-3 display text-[2.5rem] sm:text-5xl">
            Privacy policy
          </h1>
          <p className="mt-4 text-sm text-fg-muted">
            Effective date: November 17, 2026 · Last updated: November 17, 2026
          </p>
          <div className="mt-10 space-y-8">
            {SECTIONS.map((section) => (
              <article key={section.title}>
                <h2 className="text-lg font-semibold text-fg">{section.title}</h2>
                <p className="mt-2 text-sm leading-7 text-fg-muted">{section.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
    </SiteShell>
  )
}
