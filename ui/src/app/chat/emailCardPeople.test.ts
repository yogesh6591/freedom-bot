import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { EmailViewPerson } from '../../lib/types'
import { detailsForPerson, previewAsLabel } from './emailCardPeople'

function person(partial: Partial<EmailViewPerson> & { email: string; first_name: string }): EmailViewPerson {
  return {
    last_name: '',
    company: '',
    sources: {
      first_name: 'database',
      last_name: 'missing',
      company: 'missing',
      ...partial.sources
    },
    ...partial
  }
}

test('Preview as uses a typed name before the database name', () => {
  const raj = person({
    email: 'raj@acme.example.com',
    first_name: 'Raj',
    sources: { first_name: 'database', last_name: 'missing', company: 'missing' }
  })
  const shown = detailsForPerson(
    raj,
    { first: 'Shubham', last: 'Kumar', company: '' },
    'raj@acme.example.com'
  )
  assert.equal(previewAsLabel(shown, 'Hi {{firstName}},\n\nQuick note.\n'), 'Shubham')
})

test('Preview as keeps the other recipient on the database name', () => {
  const nina = person({
    email: 'nina@acme.example.com',
    first_name: 'Nina',
    sources: { first_name: 'database', last_name: 'missing', company: 'missing' }
  })
  const shown = detailsForPerson(
    nina,
    { first: 'Shubham', last: 'Kumar', company: '' },
    'raj@acme.example.com'
  )
  assert.equal(previewAsLabel(shown, 'Hi {{firstName}},\n\nQuick note.\n'), 'Nina')
})

test('Preview as follows a chat name when nothing was typed', () => {
  const shown = person({
    email: 'shub@new.example.com',
    first_name: 'Shubham',
    last_name: 'Kumar',
    sources: { first_name: 'chat', last_name: 'chat', company: 'missing' }
  })
  assert.equal(previewAsLabel(shown, 'Hi {{firstName}},\n\nQuick note.\n'), 'Shubham')
})

test('Preview as reads the name the greeting actually shows', () => {
  const stale = person({
    email: 'raj@acme.example.com',
    first_name: 'Raj',
    sources: { first_name: 'database', last_name: 'missing', company: 'missing' }
  })
  assert.equal(previewAsLabel(stale, 'Hi Shubham,\n\nQuick note.\n'), 'Shubham')
})

test('Preview as falls back to the mailbox when no name is on file', () => {
  const missing = person({
    email: 'zed@new.example.com',
    first_name: '',
    sources: { first_name: 'missing', last_name: 'missing', company: 'missing' }
  })
  assert.equal(previewAsLabel(missing, 'Hi {{firstName}},\n\nQuick note.\n'), 'zed')
})
