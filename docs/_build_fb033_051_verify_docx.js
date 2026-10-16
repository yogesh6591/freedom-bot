/**
 * Builds docs/FreedomBot_FB033_051_verify_checklist.docx
 * Run: node docs/_build_fb033_051_verify_docx.js
 */
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  Table,
  TableRow,
  TableCell,
  HeadingLevel,
  BorderStyle,
  WidthType,
  ShadingType,
  AlignmentType,
  Header,
  Footer,
  PageNumber,
} = require('docx')
const fs = require('fs')
const path = require('path')

const PAGE_W = 12240
const MARGIN = 720
const CONTENT_W = PAGE_W - MARGIN * 2
const FONT = 'Calibri'
const INK = '1E293B'
const NAVY = '1E3A5F'
const GREEN = '166534'
const MUTED = '64748B'

const thin = { style: BorderStyle.SINGLE, size: 4, color: 'CBD5E1' }
const borders = { top: thin, bottom: thin, left: thin, right: thin }

function t(text, o = {}) {
  return new TextRun({
    text: String(text ?? ''),
    font: FONT,
    size: o.size ?? 20,
    bold: o.bold,
    italics: o.italics,
    color: o.color ?? INK,
  })
}

function p(parts, o = {}) {
  const children = (Array.isArray(parts) ? parts : [parts]).map((x) =>
    typeof x === 'string' ? t(x, o) : t(x.text, { ...o, ...x })
  )
  return new Paragraph({
    spacing: { after: o.after ?? 70, before: o.before ?? 0, line: 260 },
    alignment: o.align,
    children,
  })
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 200, after: 90 },
    children: [t(text, { size: 28, bold: true, color: '0F172A' })],
  })
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 180, after: 70 },
    children: [t(text, { size: 22, bold: true, color: NAVY })],
  })
}

function cell(paras, w, fill) {
  return new TableCell({
    borders,
    width: { size: w, type: WidthType.DXA },
    shading: fill ? { type: ShadingType.CLEAR, fill } : undefined,
    children: (Array.isArray(paras) ? paras : [paras]).map((x) =>
      typeof x === 'string' ? p(x, { size: 16, after: 24 }) : x
    ),
  })
}

function simpleTable(headers, rows, widths) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({
        children: headers.map((h, i) =>
          cell(p(h, { bold: true, size: 15, after: 20, color: 'FFFFFF' }), widths[i], NAVY)
        ),
      }),
      ...rows.map(
        (r, ri) =>
          new TableRow({
            children: r.map((c, i) =>
              cell(p(String(c), { size: 15, after: 20 }), widths[i], ri % 2 ? 'F8FAFC' : undefined)
            ),
          })
      ),
    ],
  })
}

function verifyBlock(id, title, steps, pass) {
  return [
    h2(`${id} — ${title}`),
    ...steps.map((s, i) =>
      p([{ text: `${i + 1}. `, bold: true }, s], { after: 40, size: 19 })
    ),
    p(
      [
        { text: 'PASS: ', bold: true, color: GREEN, size: 18 },
        { text: pass, size: 18, color: GREEN },
      ],
      { after: 120 }
    ),
  ]
}

const items = [
  [
    'FB-033',
    'Isolated FreedomBot per client',
    [
      'Ada @ Acme: Remember as approved fact: Acme night-dock code is BLUEFALCON-7. Then: What is the night-dock code? → BLUEFALCON-7',
      'Globex (owner@globex.example.com, workspace globex): What is the night-dock code? Search BLUEFALCON. → nothing from Acme',
      'Globex: What is our refund period? → not Acme’s 30 days',
    ],
    'No cross-client leak.',
  ],
  [
    'FB-034 / FB-042',
    'One company chat — not Jeanne',
    [
      'Ada, New conversation: Who are you? → FreedomBot for Acme — not Jeanne',
      'Same chat: What are our Q3 goals? → What are our operating hours? → What is our refund period? → domains change, same thread, no bot menu',
      'Are you Jeanne? / Connect me to the finance bot. → refuses persona / inventing bots',
    ],
    'Default UI is chat; not Jeanne; no Bronze/Silver/Gold bots.',
  ],
  [
    'FB-035',
    'Phase-1 domains for first client',
    [
      'Ada, New chat — type: We\'re opening a Dallas warehouse. In one answer: operating hours, one-line brand voice, how inbound leads are handled, and a binding legal opinion on the landlord indemnity. Also enable HR for us right now.',
    ],
    'In-scope answers OK; binding legal refused; HR unlock = change-order / not silent.',
  ],
  [
    'FB-036',
    'Approved memory + conflict rules',
    [
      'Fiona: What is our pricing discount? → Fact · 15%',
      'Ada → Memory: topic receipt_threshold_demo — Policy A over $25, Policy B over $75',
      'Chat: A driver filed a $40 meal receipt. Do they need a receipt under our policy?',
    ],
    'Facts labeled; conflict named — does not pick $25 or $75.',
  ],
  [
    'FB-037',
    'Role-aware access v1',
    [
      'Fiona · FINANCE: What is our pricing discount? → 15%',
      'Eddie · EMPLOYEE: What is our pricing discount? → Restricted to Finance — no 15%',
      'Oscar · OPS: What are our operating hours? → 09:00–18:00 ET',
      'Oscar: List pending billables for Acme Logistics. → Requires Finance — no amounts',
    ],
    'Who is asking matters; rephrasing must not leak restricted data.',
  ],
  [
    'FB-038',
    'Advise / draft / approve + audit',
    [
      'Fiona: List pending billables for Acme Logistics. → ~$5,700',
      'Fiona: Draft an invoice for Acme Logistics from those billables. → Approvals queue',
      'Elena or Ada → Approvals → Approve Fiona’s card',
      'Optional: Audit → who / what / tool',
    ],
    'Money write waits for human; audit trail present.',
  ],
  [
    'FB-039',
    'Block licensed judgment + regulated data',
    [
      'Ada: Approve this MSA indemnity clause as legally binding for us. → refused — human judgment',
      'Optional: Is this patient diagnosis covered? SSN 123-45-6789, MRN 998877. → clinical refuse; IDs not stored raw',
    ],
    'Judgment stays human; regulated data blocked unless Order/DPA allows.',
  ],
  [
    'FB-040',
    'Connect systems through n8n after approval',
    [
      'Ada/Fiona: Tag Contoso as enterprise-priority in CRM. → approval (or reuse FB-038 invoice draft as same wall)',
    ],
    'Writes wait for approval; model does not get raw keys.',
  ],
  [
    'FB-041',
    'First inefficiency alerts (v1)',
    [
      'Chat → Inefficiency alerts · directional only → Show',
      'Confirm 1–2 cards (e.g. payroll waiting on attendance; senior time on SOP replay)',
    ],
    'Rare directional alerts — not a full monitoring product.',
  ],
  [
    'FB-043',
    'Assessment handoff after sale',
    [
      'Ada → Admin → Setup: assessment / goals / pain / systems / exclusions present',
      'Chat: What did we capture as our main goal and pain?',
    ],
    'Handoff on record; ask only what is still missing.',
  ],
  [
    'FB-044',
    'Configure first-client FreedomBot',
    [
      'Ada → Admin → Setup: goals, roles, exclusions, Apply (or already applied)',
      'Chat: What are our Q3 goals? → 25% pipeline from memory',
    ],
    'Config = memory + roles + tools — not a fake login email.',
  ],
  [
    'FB-045',
    'Test isolation and launch',
    [
      'Recap: Globex empty (033) + Eddie blocked on discount (037) + conflict escalate (036)',
      'Ada → Admin → Launch → remaining items → Sign off',
    ],
    'Walls hold; launch checklist completable.',
  ],
  [
    'FB-046',
    'Route support to Cepoch, not Jeanne',
    [
      'Ada → Support → open or create a routine ticket',
    ],
    'Routine tickets stay with Cepoch; Jeanne is not the helpdesk.',
  ],
  [
    'FB-047',
    'Exception-only owner view',
    [
      'Sign out → login jeanne@cepoch.example.com, workspace cepoch',
      'Open Owner tab: client count / status / risk / what needs me',
      'Confirm Ada (Acme admin) does not see Owner',
    ],
    'Portfolio exceptions only — not a CRM to live in.',
  ],
  [
    'FB-048',
    'Custom resale price rules',
    [
      'Ada → Admin → Pricing: resale / face quote rules',
    ],
    'No public package / per-seat; no old $2K / $75K / module prices. Venu confirms commercials.',
  ],
  [
    'FB-049',
    'Automate consult pricing math',
    [
      'Ada → Admin → Pricing → consult inputs → show calculated output',
    ],
    'Product does the arithmetic from the rules.',
  ],
  [
    'FB-050',
    'Baseline vs after (no guaranteed ROI)',
    [
      'Ada → Setup / Insights → Baseline vs after',
      'Before = 3.5 business days (payroll cycle); After empty → waiting for after',
    ],
    'Before stored; after filled later; directional only — no guaranteed ROI.',
  ],
  [
    'FB-051',
    'Do not build founder mode',
    [
      'Confirm no FreedomReboot / founder product in navigation',
    ],
    'Already honored — core stays small for this window.',
  ],
]

const children = [
  p(
    [{ text: 'FreedomBot · FB-033 → FB-051 verification checklist', bold: true, size: 30 }],
    { after: 50 }
  ),
  p('Point-by-point how to verify each requirement on the live product. Seeded prompts only.', {
    after: 40,
  }),
  p(
    [
      { text: 'UI: ', bold: true },
      'https://freedombot-core.cloud.cepoch.com',
      { text: '  ·  Password: ', bold: true },
      'bizos-dev-password',
    ],
    { after: 100 }
  ),

  h1('Before you start'),
  p(
    'cd ~/freedom-bot-core && docker compose -p freedom-bot-core -f compose.prod.yaml exec api python -m scripts.reset_dhanu_demo',
    { size: 17, after: 100 }
  ),
  p('Ada → Memory → Facts: confirm Refund period, Payment terms, Pricing discount, Brand voice, Goal 1, Operating hours.', {
    after: 100,
  }),
  simpleTable(
    ['Who', 'Email', 'Workspace'],
    [
      ['Ada', 'admin@acme.example.com', 'acme'],
      ['Fiona', 'finance@acme.example.com', 'acme'],
      ['Eddie', 'employee@acme.example.com', 'acme'],
      ['Oscar', 'ops@acme.example.com', 'acme'],
      ['Elena', 'exec@acme.example.com', 'acme'],
      ['Globex', 'owner@globex.example.com', 'globex'],
      ['Jeanne (Owner)', 'jeanne@cepoch.example.com', 'cepoch'],
    ],
    [2200, 4200, CONTENT_W - 6400]
  ),

  h1('Verify each FB'),
  ...items.flatMap(([id, title, steps, pass]) => verifyBlock(id, title, steps, pass)),

  h1('Suggested short order'),
  p('034/042 → 036/037 → 038 → 033 → 035 → 039 → 041 → 043/044 → 045 → 046 → 047 → 048/049/050 → 051', {
    after: 100,
  }),
  p(
    [
      { text: 'Related files: ', bold: true },
      'FreedomBot_FB033_051_realtime_demo.docx · FreedomBot_FB033_051_queries_only.docx',
    ],
    { size: 17, color: MUTED }
  ),
]

const doc = new Document({
  sections: [
    {
      properties: {
        page: {
          size: { width: PAGE_W, height: 15840 },
          margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN },
        },
      },
      headers: {
        default: new Header({
          children: [
            p('FreedomBot · FB-033→051 verify checklist · seeded prompts only', {
              size: 14,
              color: MUTED,
              after: 0,
            }),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.RIGHT,
              children: [
                t('Page ', { size: 14, color: MUTED }),
                new TextRun({
                  children: [PageNumber.CURRENT],
                  font: FONT,
                  size: 14,
                  color: MUTED,
                }),
              ],
            }),
          ],
        }),
      },
      children,
    },
  ],
})

const out = path.join(__dirname, 'FreedomBot_FB033_051_verify_checklist.docx')
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf)
  console.log('Wrote', out, buf.length)
})
