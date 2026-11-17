/** Default fake values for local testing — prefill every form so you can click through. */
export const DEMO_FILLER = {
  fullName: 'Alex Rivera',
  email: 'alex@globex.com',
  password: 'FreedomBot123!',
  companyName: 'Globex Ltd',
  workspaceSlug: 'globex',
  purchasedDomains: [
    'general',
    'operations',
    'sales',
    'intake',
    'planning'
  ],
  planId: 'growth' as const,
  cardName: 'Alex Rivera',
  cardNumber: '4242 4242 4242 4242',
  expiry: '12/28',
  cvc: '123',
  agree: true
}
