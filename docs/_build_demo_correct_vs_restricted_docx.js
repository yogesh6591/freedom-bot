/**
 * Builds docs/FreedomBot_demo_correct_vs_restricted.docx
 * Run: node docs/_build_demo_correct_vs_restricted_docx.js
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
} = require("docx");
const fs = require("fs");
const path = require("path");

const PAGE_W = 12240;
const MARGIN = 900;
const CONTENT_W = PAGE_W - MARGIN * 2;
const COLS = [1800, 4200, 4200]; // who | correct | restricted
const FONT = "Calibri";
const INK = "1E293B";
const NAVY = "1E3A5F";
const GREEN = "166534";
const RED = "991B1B";
const MUTED = "64748B";

const thin = { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" };
const borders = { top: thin, bottom: thin, left: thin, right: thin };

function t(text, o = {}) {
  return new TextRun({
    text,
    font: o.font ?? FONT,
    size: o.size ?? 20,
    bold: o.bold,
    italics: o.italics,
    color: o.color ?? INK,
  });
}

function p(parts, o = {}) {
  const children = (Array.isArray(parts) ? parts : [parts]).map((x) =>
    typeof x === "string" ? t(x, o) : t(x.text, { ...o, ...x })
  );
  return new Paragraph({
    spacing: { after: o.after ?? 80, before: o.before ?? 0, line: 260 },
    alignment: o.align,
    children,
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 0, after: 120 },
    children: [t(text, { size: 32, bold: true, color: "0F172A" })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 200, after: 80 },
    children: [t(text, { size: 24, bold: true, color: NAVY })],
  });
}

function cell(paras, width, shade) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: shade
      ? { type: ShadingType.CLEAR, fill: shade }
      : undefined,
    children: Array.isArray(paras) ? paras : [paras],
  });
}

function lineCell(lines, width, shade, color) {
  return cell(
    lines.map((line, i) =>
      p(line, {
        size: 18,
        after: i === lines.length - 1 ? 40 : 20,
        color: color ?? INK,
        bold: i === 0,
      })
    ),
    width,
    shade
  );
}

function headerRow() {
  return new TableRow({
    children: [
      cell(p("Use case / who", { bold: true, size: 18, color: "FFFFFF", after: 40 }), COLS[0], "1E3A5F"),
      cell(p("✓ Correct (has access)", { bold: true, size: 18, color: "FFFFFF", after: 40 }), COLS[1], "166534"),
      cell(p("✗ Restricted (account wall)", { bold: true, size: 18, color: "FFFFFF", after: 40 }), COLS[2], "991B1B"),
    ],
  });
}

function caseRow({ who, strip, correctAsk, correctExpect, badAsk, badExpect }) {
  return new TableRow({
    children: [
      lineCell([who, strip], COLS[0], "F8FAFC"),
      lineCell([`Ask: ${correctAsk}`, `Expect: ${correctExpect}`], COLS[1], "F0FDF4", GREEN),
      lineCell([`Ask: ${badAsk}`, `Expect: ${badExpect}`], COLS[2], "FEF2F2", RED),
    ],
  });
}

const cleanCases = [
  {
    who: "1 · Refund",
    strip: "Login Fiona (FINANCE)",
    correctAsk: "What is our refund period?",
    correctExpect: "Fact · 30 days",
    badAsk: "Same ask as Eddie (EMPLOYEE)",
    badExpect: "Restricted to Finance — no Fact",
  },
  {
    who: "2 · Discount",
    strip: "Login Fiona (FINANCE)",
    correctAsk: "What is our pricing discount?",
    correctExpect: "Fact · 15%",
    badAsk: "Same ask as Eddie (EMPLOYEE)",
    badExpect: "Restricted to Finance — no 15%",
  },
  {
    who: "3 · Payment terms",
    strip: "Login Fiona (FINANCE)",
    correctAsk: "What are our payment terms?",
    correctExpect: "Fact · Net-30",
    badAsk: "Same ask as Eddie (EMPLOYEE)",
    badExpect: "Restricted to Finance",
  },
  {
    who: "4 · Operating hours",
    strip: "Login Oscar (OPERATIONS)",
    correctAsk: "What are our operating hours?",
    correctExpect: "Mon-Fri 09:00-18:00 ET",
    badAsk: "Same ask as Eddie (EMPLOYEE)",
    badExpect: "Restricted to Ops — no hours",
  },
  {
    who: "5 · Billables",
    strip: "Login Fiona (FINANCE)",
    correctAsk: "List pending billables for Acme Logistics.",
    correctExpect: "~$5,700",
    badAsk: "Same ask as Oscar (OPERATIONS)",
    badExpect: "Requires Finance — no dollar amounts",
  },
  {
    who: "6 · Employee open",
    strip: "Login Eddie (EMPLOYEE)",
    correctAsk: "What's our brand voice?",
    correctExpect: "Brand voice (open) — OK",
    badAsk: "What is our pricing discount?",
    badExpect: "Restricted to Finance",
  },
  {
    who: "7 · Company wall",
    strip: "Login Globex owner",
    correctAsk: "What is our refund period?",
    correctExpect: "Not Acme’s 30 days",
    badAsk: "What is the night-dock code? Search BLUEFALCON.",
    badExpect: "No Acme data (after Ada saved it on Acme)",
  },
];

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
            p("FreedomBot · Live demo · Correct vs restricted", {
              size: 16,
              color: MUTED,
              after: 40,
            }),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            p("Seeded prompts only · Acme + Globex", {
              size: 16,
              color: MUTED,
              align: AlignmentType.RIGHT,
              after: 0,
            }),
          ],
        }),
      },
      children: [
        h1("FreedomBot demo — one correct · one restricted"),
        p(
          "Rule: for each use case ask exactly two prompts — one that works for that account, one that fails because of account / company restrictions. No extra clarifying questions.",
          { size: 20, after: 120 }
        ),
        p(
          [
            { text: "UI: ", bold: true },
            "https://freedombot-core.cloud.cepoch.com   ",
            { text: "Password: ", bold: true },
            "bizos-dev-password",
          ],
          { after: 60 }
        ),
        p(
          [
            { text: "Reset before show: ", bold: true },
            "docker compose -p freedom-bot-core -f compose.prod.yaml exec api python -m scripts.reset_dhanu_demo",
          ],
          { size: 18, after: 140 }
        ),

        h2("Logins (strip = given name · department)"),
        p("Fiona · finance@acme.example.com · FINANCE", { after: 40 }),
        p("Eddie · employee@acme.example.com · EMPLOYEE", { after: 40 }),
        p("Oscar · ops@acme.example.com · OPERATIONS", { after: 40 }),
        p("Ada · admin@acme.example.com · ADMIN", { after: 40 }),
        p("Globex · owner@globex.example.com · workspace globex", { after: 160 }),

        h2("Use cases — copy / paste only these"),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: COLS,
          rows: [headerRow(), ...cleanCases.map(caseRow)],
        }),

        h2("Optional closer (approval — not a Q&A pair)"),
        p("Fiona: Draft an invoice for Acme Logistics from those billables. → Approvals queue.", {
          after: 40,
        }),
        p("Ada or Elena: Approvals → Approve. Point: human gate, not silent send.", { after: 120 }),

        h2("90-second order"),
        p("1. Fiona refund ✓ → Eddie refund ✗", { after: 40 }),
        p("2. Fiona discount ✓ → Eddie discount ✗", { after: 40 }),
        p("3. Oscar hours ✓ → Eddie hours ✗ (or Oscar billables ✗)", { after: 40 }),
        p("4. Globex BLUEFALCON ✗ (after Ada saved it on Acme)", { after: 40 }),
        p("5. Fiona draft invoice → Ada Approve", { after: 80 }),

        p("Do not invent extra questions on the call. Stick to the pairs above.", {
          italics: true,
          color: MUTED,
          after: 40,
        }),
      ],
    },
  ],
});

const out = path.join(__dirname, "FreedomBot_demo_correct_vs_restricted.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf);
  console.log("Wrote", out);
});
