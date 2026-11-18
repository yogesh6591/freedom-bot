/**
 * The main FreedomBot app. The website is served by it at /website, so sign-in and onboarding live there:
 * Sign in -> the app's sign-in (then the AI chat), Sign up -> the app's /signup onboarding.
 */
export const APP_URL = (process.env.NEXT_PUBLIC_APP_URL || 'https://freedombot-core.cloud.cepoch.com').replace(
  /\/+$/,
  ''
)

/** A page of the main app, e.g. appUrl('/signup'). */
export function appUrl(path = '/'): string {
  return `${APP_URL}${path.startsWith('/') ? path : `/${path}`}`
}

/** Sign in: the app's sign-in page, which opens the AI chat. */
export const SIGN_IN_URL = appUrl('/')

/** Sign up: the app's onboarding. A plan from the pricing cards is passed on as ?plan=. */
export function signupUrl(plan?: string): string {
  return plan ? appUrl(`/signup?plan=${encodeURIComponent(plan)}`) : appUrl('/signup')
}
