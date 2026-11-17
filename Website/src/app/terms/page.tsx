import type { Metadata } from 'next'
import { SiteShell } from '@/components/SiteShell'

export const metadata: Metadata = {
  title: 'Terms of service'
}

const SECTIONS = [
  {
    title: '1. Agreement',
    body: 'By creating a FreedomBot account, starting onboarding, or accessing a workspace, you agree to these Terms of Service on behalf of yourself and the organization you represent.'
  },
  {
    title: '2. The service',
    body: 'FreedomBot is a governed company assistant platform that provides organizational memory, capability packs, integrations, and approval workflows inside multi-tenant workspaces. Features available to you depend on the subscription plan and purchased packs.'
  },
  {
    title: '3. Accounts and workspaces',
    body: 'You must provide accurate admin information during signup. Your workspace identifier (domain/slug) is unique, used for sign-in and data isolation, and cannot be changed after provisioning. You are responsible for safeguarding credentials and for activity under your account.'
  },
  {
    title: '4. Subscriptions and payment',
    body: 'Paid plans are billed according to the pricing selected during onboarding. Fees are non-refundable except where required by law or expressly stated in a written order. Failure to pay may result in suspension of the workspace.'
  },
  {
    title: '5. Acceptable use',
    body: 'You may not misuse FreedomBot to violate law, bypass access controls, process prohibited regulated data where blocked, attempt unauthorized access to other tenants, or reverse engineer the platform. We may suspend accounts that create material risk.'
  },
  {
    title: '6. Customer data',
    body: 'You retain ownership of content you submit to your workspace. You grant FreedomBot a limited license to host, process, and display that content solely to provide the service. Platform policy may refuse certain regulated categories (for example health or card data) before model processing.'
  },
  {
    title: '7. Approvals and automation',
    body: 'FreedomBot may draft messages and propose actions. Outbound sends and other high-risk actions remain subject to your workspace approval policy. You remain responsible for final decisions made by your users and approvers.'
  },
  {
    title: '8. Confidentiality and security',
    body: 'Each party will protect the other’s confidential information with reasonable care. FreedomBot maintains administrative, technical, and organizational measures appropriate to a multi-tenant SaaS workspace, including tenant isolation and audit logging.'
  },
  {
    title: '9. Disclaimers',
    body: 'The service is provided “as is.” FreedomBot does not warrant uninterrupted availability or that model-generated drafts are error-free. Outputs should be reviewed before reliance for legal, financial, or operational decisions.'
  },
  {
    title: '10. Limitation of liability',
    body: 'To the maximum extent permitted by law, FreedomBot’s aggregate liability arising from these terms is limited to the fees paid for the service in the three months preceding the claim. Neither party is liable for indirect or consequential damages.'
  },
  {
    title: '11. Termination',
    body: 'Either party may terminate for material breach if not cured within a reasonable period after notice. Upon termination, access ends and workspace data is handled according to the retention policy and applicable law.'
  },
  {
    title: '12. Changes',
    body: 'We may update these terms. Material changes will be posted on this page with an updated effective date. Continued use after the effective date constitutes acceptance.'
  },
  {
    title: '13. Contact',
    body: 'Questions about these terms: legal@freedombot.app (or your designated FreedomBot account contact).'
  }
]

export default function TermsPage() {
  return (
    <SiteShell>
      <section className="section">
        <div className="container-page max-w-3xl">
          <p className="eyebrow">Legal</p>
          <h1 className="mt-3 display text-[2.5rem] sm:text-5xl">
            Terms of service
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
