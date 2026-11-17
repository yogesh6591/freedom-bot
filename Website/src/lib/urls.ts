/** Main FreedomBot app — Sign in always goes here. */
export const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ||
  'https://freedombot-core.cloud.cepoch.com'

export function appSignInUrl(workspaceSlug?: string): string {
  const url = new URL('/', APP_URL.endsWith('/') ? APP_URL : `${APP_URL}/`)
  if (workspaceSlug) url.searchParams.set('workspace', workspaceSlug)
  return url.toString()
}
