/**
 * Builds docs/FreedomBot_Yogesh_live_demo_finance_story.docx
 * Run: node docs/_build_yogesh_finance_demo_docx.js
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
  PageBreak,
} = require("docx");
const fs = require("fs");
const path = require("path");

const PAGE_W = 12240;
const MARGIN = 900;
const CONTENT_W = PAGE_W - MARGIN * 2;
const FONT = "Calibri";
const INK = "1E293B";
const NAVY = "1E3A5F";
const PURPLE = "5B21B6";
const GREEN = "166534";

const thin = { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" };
const borders = { top: thin, bottom: thin, left: thin, right: thin };

function t(text, o = {}) {
  return new TextRun({
    text,
    font: o.font ?? FONT,
    size: o.size ?? 22,
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
    spacing: { after: o.after ?? 100, before: o.before ?? 0, line: 276 },
    alignment: o.align,
    children,
  });
}

function h1(text, pageBreak = false) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: pageBreak ? 0 : 240, after: 140 },
    children: [
      ...(pageBreak ? [new PageBreak()] : []),
      t(text, { size: 32, bold: true, color: "0F172A" }),
    ],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 220, after: 100 },
    children: [t(text, { size: 26, bold: true, color: NAVY })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 180, after: 80 },
    children: [t(text, { size: 24, bold: true, color: PURPLE })],
  });
}

function callout(label, body, bg = "F5F3FF") {
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
              p([{ text: label, bold: true, color: PURPLE, size: 20 }], { after: 40 }),
              p(body, { after: 40 }),
            ],
          }),
        ],
      }),
    ],
  });
}

function typeBox(text) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            borders,
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: "F1F5F9" },
            children: [
              p([{ text: "TYPE IN CHAT", bold: true, size: 18, color: NAVY }], { after: 40 }),
              p([{ text, italics: true, size: 22 }], { after: 40 }),
            ],
          }),
        ],
      }),
    ],
  });
}

function expectLine(text) {
  return p([
    { text: "EXPECT: ", bold: true, color: GREEN, size: 20 },
    { text, size: 20 },
  ]);
}

function cell(text, w, o = {}) {
  return new TableCell({
    borders,
    width: { size: w, type: WidthType.DXA },
    shading: o.fill
      ? { type: ShadingType.CLEAR, fill: o.fill }
      : undefined,
    children: [
      new Paragraph({
        spacing: { after: 40, before: 40 },
        children: [t(text, { size: o.size ?? 18, bold: o.bold, color: o.color ?? INK })],
      }),
    ],
  });
}

function simpleTable(headers, rows, widths) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({
        children: headers.map((h, i) =>
          cell(h, widths[i], { bold: true, fill: "E2E8F0", size: 18 })
        ),
      }),
      ...rows.map(
        (r) =>
          new TableRow({
            children: r.map((c, i) => cell(String(c), widths[i])),
          })
      ),
    ],
  });
}

const doc = new Document({
  styles: {
    default: { document: { styles: [{ id: "Normal", run: { font: FONT, size: 22 } }] } },
  },
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
            p(
              [
                {
                  text: "FreedomBot · Yogesh live demo · Dhanu format (one line → show live)",
                  size: 16,
                  color: "64748B",
                },
              ],
              { after: 0 }
            ),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.RIGHT,
              children: [
                t("Page ", { size: 16, color: "64748B" }),
                new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: "64748B" }),
              ],
            }),
          ],
        }),
      },
      children: [
        p(
          [{ text: "FREEDOMBOT — YOGESH LIVE DEMO SCRIPT", bold: true, size: 36, color: "0F172A" }],
          { after: 80 }
        ),
        p("For Yogesh → Venu / Jeanne · Rules from Call with Dhanu (12 Oct 2026)", {
          after: 60,
        }),
        p(
          [
            { text: "Live UI: ", bold: true },
            "https://freedombot-core.cloud.cepoch.com",
          ],
          { after: 40 }
        ),
        p(
          [
            { text: "Password (all accounts): ", bold: true },
            { text: "bizos-dev-password", italics: true },
          ],
          { after: 160 }
        ),

        h1("1. Dhanu rules before you share screen"),
        p("1. Document = homework. Screenshots OK in a doc. On the call: LIVE ONLY. No screenshot slideshow."),
        p("2. One line → then show. Do not talk for 10 minutes. Say what it is, then type."),
        p("3. Big role on screen. After every login, pause, point at the purple strip (YOU ARE · ROLE · ACCESS · CLIENT), say the role out loud."),
        p("4. Story, not pages. Stay in one chat for one person. Switch people only for walls / approval."),
        p("5. Rehearse twice before Venu. Do not rehearse on the call."),

        h2("Reset before demo"),
        typeBox(
          "docker compose -p freedom-bot-core -f compose.prod.yaml exec api python -m scripts.reset_dhanu_demo"
        ),

        h2("Logins"),
        simpleTable(
          ["Person", "Email", "Workspace", "Strip should say"],
          [
            ["Fiona Finance", "finance@acme.example.com", "acme", "Fiona · FINANCE · ACME"],
            ["Oscar Ops", "ops@acme.example.com", "acme", "Oscar · OPS · ACME"],
            ["Elena Exec", "exec@acme.example.com", "acme", "Elena · EXEC/APPROVER"],
            ["Eddie Employee", "employee@acme.example.com", "acme", "Eddie · GENERAL ACCESS ONLY"],
            ["Ada Admin", "admin@acme.example.com", "acme", "Ada · ADMIN · ACME"],
          ],
          [1800, 3200, 1200, CONTENT_W - 6200]
        ),

        h1("2. Opening (20 seconds)", true),
        callout(
          "ONE LINE",
          "This is one company AI with walls — between companies, roles, departments, and risky actions. Not a bot picker."
        ),
        p("Show: Login as Fiona Finance. Point at purple strip: FINANCE. Chat. Mode WAIT_FOR_APPROVAL.", {
          before: 120,
        }),

        h1("3. HERO STORY — Finance department (one chat)", true),
        p(
          [
            {
              text: "Stay as Fiona in one New chat for steps 1–3. This is the story people remember.",
              bold: true,
            },
          ],
          { after: 160 }
        ),

        h3("Step 1 — Finance fact from memory"),
        callout(
          "ONE LINE",
          "Finance facts come from approved company memory, labeled Fact — not a guess."
        ),
        typeBox("What is our refund period?"),
        expectLine("Fact: · 30 days. Handled as finance. Strip still Fiona · FINANCE."),

        h3("Step 2 — Same thread (no bot switch)"),
        callout("ONE LINE", "Same conversation — no finance-bot menu."),
        typeBox("What is our standard discount percentage?"),
        expectLine("15% from finance memory. Same thread."),
        p("Optional same chat:", { before: 80 }),
        typeBox("What are our payment terms?"),
        expectLine("Fact from memory (e.g. Net 30). Still Fiona."),

        h3("Step 3 — Billables → draft invoice (approval wall)"),
        callout(
          "ONE LINE",
          "Finance can prepare the invoice; creating money documents waits for a human."
        ),
        typeBox("List pending billables for Acme Logistics."),
        expectLine("Lines totaling about $5,700. No silent create."),
        typeBox("Draft an invoice for Acme Logistics from those billables."),
        expectLine("Queued for approval — not silently posted. Say: Draft can prepare. Real invoice needs Elena."),

        h3("Step 4 — Wall: Ops cannot do Fiona’s job"),
        callout(
          "ONE LINE",
          "Same company, different department badge — finance amounts stay behind Finance."
        ),
        p("Sign out → login Oscar Ops. Point at strip: OPS. New chat."),
        typeBox("List pending billables for Acme Logistics. Draft an invoice."),
        expectLine("Restricted to the Finance area — no $5,700, no draft."),

        h3("Step 5 — Elena closes the finance story"),
        callout(
          "ONE LINE",
          "Only an approver / exec closes the money action."
        ),
        p("Login Elena Exec → Approvals → card Requested by Fiona Finance → Approve."),
        typeBox("Show invoices for Acme Logistics."),
        expectLine("Draft ~$5,700 exists. Fiona listing again → nothing pending to re-invoice."),
        callout(
          "CLOSER",
          "Finance prepared it. Exec approved it. Ops never saw the numbers.",
          "ECFDF5"
        ),

        h1("4. Cheat sheet (finance hero)", true),
        simpleTable(
          ["#", "Say (one line)", "Type", "Expect"],
          [
            ["1", "Fact from memory", "refund period?", "Fact · 30 days"],
            ["2", "Same chat", "discount %?", "15%"],
            ["3a", "Prepare work", "list billables Acme Logistics", "~$5,700"],
            ["3b", "Human gate", "draft invoice…", "Approvals queue"],
            ["4", "Dept wall", "Oscar: same ask", "Restricted to Finance"],
            ["5", "Close loop", "Elena Approve", "Invoice ~$5,700"],
          ],
          [600, 2200, 3200, CONTENT_W - 6000]
        ),

        h1("5. Short extras (only if time)", true),
        h3("A — Eddie blocked on discount"),
        callout("ONE LINE", "Who is asking matters; rephrasing does not open Finance."),
        typeBox("What's our standard discount percentage, and what's our brand voice?"),
        expectLine("Brand OK; discount restricted to Finance."),

        h3("B — Ada grants then revokes Finance for Eddie"),
        callout("ONE LINE", "Admin can grant Finance; revoke puts the wall back."),
        p("Ada → Admin → Eddie finance ON → Eddie sees 15% → OFF → New chat → blocked."),

        h3("C — Two companies"),
        callout("ONE LINE", "Acme memory must never answer inside Globex."),
        p("Acme: remember BLUEFALCON-7. Globex: ask for BLUEFALCON → nothing."),

        h1("6. Do not do on the call", true),
        p("• Do not open a long screenshot PDF and scroll."),
        p("• Do not narrate FB ticket numbers for ten minutes."),
        p("• Do not say Jeanne bot or open a bot menu — product is FreedomBot."),
        p("• Prefer Fiona / Elena / Oscar / Eddie — not duplicate demo accounts."),
      ],
    },
  ],
});

const out = path.join(__dirname, "FreedomBot_Yogesh_live_demo_finance_story.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf);
  console.log("Wrote", out);
});
