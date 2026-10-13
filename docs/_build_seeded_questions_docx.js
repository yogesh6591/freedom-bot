/** Builds docs/FreedomBot_Yogesh_seeded_questions_live_demo.docx */
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  HeadingLevel,
  Header,
  Footer,
  PageNumber,
  AlignmentType,
  BorderStyle,
  Table,
  TableRow,
  TableCell,
  WidthType,
  ShadingType
} = require('docx')
const fs = require('fs')
const path = require('path')

const FONT = 'Calibri'
const INK = '1E293B'
const NAVY = '1E3A5F'
const PURPLE = '5B21B6'
const GREEN = '166534'
const PAGE_W = 12240
const MARGIN = 900
const CONTENT_W = PAGE_W - MARGIN * 2
const thin = { style: BorderStyle.SINGLE, size: 4, color: 'CBD5E1' }
const borders = { top: thin, bottom: thin, left: thin, right: thin }

function t(text, o = {}) {
  return new TextRun({
    text,
    font: FONT,
    size: o.size ?? 22,
    bold: o.bold,
    italics: o.italics,
    color: o.color ?? INK
  })
}

function p(parts, o = {}) {
  const children = (Array.isArray(parts) ? parts : [parts]).map((x) =>
    typeof x === 'string' ? t(x, o) : t(x.text, { ...o, ...x })
  )
  return new Paragraph({
    spacing: { after: o.after ?? 90, before: o.before ?? 0, line: 276 },
    children
  })
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 240, after: 120 },
    children: [t(text, { size: 30, bold: true, color: '0F172A' })]
  })
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 200, after: 90 },
    children: [t(text, { size: 24, bold: true, color: NAVY })]
  })
}

function box(label, body, bg = 'F5F3FF') {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            borders,
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: bg },
            children: [
              p([{ text: label, bold: true, color: PURPLE, size: 18 }], { after: 40 }),
              p([{ text: body, italics: label.startsWith('TYPE') }], { after: 40 })
            ]
          })
        ]
      })
    ]
  })
}

const md = fs.readFileSync(
  path.join(__dirname, 'FreedomBot_Yogesh_seeded_questions_live_demo.md'),
  'utf8'
)
const children = [
  p([{ text: 'FREEDOMBOT — SEEDED QUESTIONS LIVE DEMO (YOGESH)', bold: true, size: 32 }], {
    after: 60
  }),
  p('Only ask what is already in Acme Memory / billables. Password: bizos-dev-password', {
    after: 140
  })
]

let inCode = false
let codeBuf = []
for (const line of md.split('\n')) {
  if (line.startsWith('```')) {
    if (!inCode) {
      inCode = true
      codeBuf = []
    } else {
      inCode = false
      const body = codeBuf.join(' ').trim()
      if (body && !body.startsWith('cd ') && !body.startsWith('docker')) {
        children.push(box('TYPE IN CHAT', body, 'F1F5F9'))
      } else if (body) {
        children.push(box('SERVER', body, 'FEF3C7'))
      }
      codeBuf = []
    }
    continue
  }
  if (inCode) {
    codeBuf.push(line)
    continue
  }
  if (line.startsWith('# ')) {
    if (!line.includes('Yogesh live')) children.push(h1(line.replace(/^# /, '')))
    continue
  }
  if (line.startsWith('## ')) {
    children.push(h1(line.replace(/^## /, '')))
    continue
  }
  if (line.startsWith('### ')) {
    children.push(h2(line.replace(/^### /, '')))
    continue
  }
  if (line.startsWith('**One line:**')) {
    children.push(
      box('ONE LINE', line.replace(/^\*\*One line:\*\*\s*/, '').replace(/^"|"$/g, ''))
    )
    continue
  }
  if (line.startsWith('**Expect:**') || line.startsWith('**Expect')) {
    children.push(
      p([
        { text: 'EXPECT: ', bold: true, color: GREEN, size: 20 },
        {
          text: line.replace(/^\*\*Expect:\*\*\s*/, '').replace(/\*\*/g, ''),
          size: 20
        }
      ])
    )
    continue
  }
  if (line.startsWith('|') || line.startsWith('---') || !line.trim()) continue
  if (line.startsWith('- ')) {
    children.push(p('• ' + line.slice(2).replace(/\*\*/g, '').replace(/`([^`]+)`/g, '$1')))
    continue
  }
  children.push(p(line.replace(/\*\*/g, '').replace(/`([^`]+)`/g, '$1')))
}

const doc = new Document({
  sections: [
    {
      properties: {
        page: {
          size: { width: PAGE_W, height: 15840 },
          margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN }
        }
      },
      headers: {
        default: new Header({
          children: [
            p(
              [
                {
                  text: 'FreedomBot · Seeded questions only · Yogesh live demo',
                  size: 16,
                  color: '64748B'
                }
              ],
              { after: 0 }
            )
          ]
        })
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.RIGHT,
              children: [
                t('Page ', { size: 16, color: '64748B' }),
                new TextRun({
                  children: [PageNumber.CURRENT],
                  font: FONT,
                  size: 16,
                  color: '64748B'
                })
              ]
            })
          ]
        })
      },
      children
    }
  ]
})

const out = path.join(__dirname, 'FreedomBot_Yogesh_seeded_questions_live_demo.docx')
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf)
  console.log('Wrote', out)
})
