import { OnboardingShell } from '@/components/OnboardingShell'

export default function OnboardingLayout({
  children
}: {
  children: React.ReactNode
}) {
  return <OnboardingShell>{children}</OnboardingShell>
}
