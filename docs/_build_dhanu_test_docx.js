/**
 * Builds docs/Dhanu_master_platform_real_world_test.docx
 * Run: NODE_PATH=/tmp/node_modules node docs/_build_dhanu_test_docx.js
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
  LevelFormat,
  Header,
  Footer,
  PageNumber,
} = require("docx");
const fs = require("fs");
const path = require("path");

const PAGE_W = 12240;
const MARGIN = 720;
const CONTENT_W = PAGE_W - MARGIN * 2;

const thin = { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" };
const borders = { top: thin, bottom: thin, left: thin, right: thin };

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after ?? 120, before: opts.before ?? 0, line: 276 },
    children: [
      new TextRun({
        text,
        font: "Calibri",
        size: opts.size ?? 22,
        bold: opts.bold,
        italics: opts.italics,
        color: opts.color ?? "1E293B",
      }),
    ],
  });
}

function runs(parts, spacing = {}) {
  return new Paragraph({
    spacing: { after: 120, line: 276, ...spacing },
    children: parts.map(
      (part) =>
        new TextRun({
          text: part.text,
          font: "Calibri",
          size: part.size ?? 22,
          bold: part.bold,
          italics: part.italics,
          color: part.color ?? "1E293B",
        })
    ),
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 280, after: 160 },
    children: [new TextRun({ text, font: "Calibri", size: 32, bold: true, color: "0F172A" })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, font: "Calibri", size: 26, bold: true, color: "1E3A5F" })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, font: "Calibri", size: 24, bold: true, color: "334155" })],
  });
}

function cell(text, width, opts = {}) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: opts.header
      ? { type: ShadingType.CLEAR, fill: "1E3A5F" }
      : opts.fill
        ? { type: ShadingType.CLEAR, fill: opts.fill }
        : opts.warn
          ? { type: ShadingType.CLEAR, fill: "FEF3C7" }
          : undefined,
    children: [
      new Paragraph({
        spacing: { after: 40, before: 40 },
        children: [
          new TextRun({
            text,
            font: "Calibri",
            size: opts.size ?? 17,
            bold: opts.header || opts.bold,
            color: opts.header ? "FFFFFF" : "1E293B",
          }),
        ],
      }),
    ],
  });
}

function table(headers, rows, widths, rowOpts = []) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({
        children: headers.map((h, i) => cell(h, widths[i], { header: true })),
      }),
      ...rows.map(
        (row, ri) =>
          new TableRow({
            children: row.map((c, i) =>
              cell(String(c), widths[i], {
                fill: ri % 2 === 1 ? "F8FAFC" : undefined,
                ...(rowOpts[ri] || {}),
              })
            ),
          })
      ),
    ],
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { after: 80, line: 276 },
    children: [new TextRun({ text, font: "Calibri", size: 22, color: "1E293B" })],
  });
}

function step(n, action, expect) {
  return [
    runs(
      [
        { text: `Step ${n}. `, bold: true, color: "1E3A5F" },
        { text: action },
      ],
      { before: 80 }
    ),
    runs([
      { text: "Expect: ", bold: true, color: "166534" },
      { text: expect, color: "166534" },
    ]),
  ];
}

function scenarioBlock(title, who, story, steps) {
  return [
    h2(title),
    runs([
      { text: "Login as: ", bold: true },
      { text: who },
    ]),
    p(story, { italics: true, color: "475569" }),
    ...steps.flatMap((s, i) => step(i + 1, s.do, s.expect)),
    runs([
      { text: "Pass if: ", bold: true },
      { text: steps[steps.length - 1].pass || "Behaviour matches Expect for every step." },
    ]),
  ];
}

const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [
          {
            level: 0,
            format: LevelFormat.BULLET,
            text: "•",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 360, hanging: 180 } } },
          },
        ],
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: PAGE_W, height: 15840 },
          margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
        },
      },
      headers: {
        default: new Header({
          children: [
            new Paragraph({
              border: {
                bottom: { style: BorderStyle.SINGLE, size: 12, color: "EAB308", space: 8 },
              },
              spacing: { after: 120 },
              children: [
                new TextRun({
                  text: "FreedomBot  ·  Master platform  ·  Real-world test (Dhanu)",
                  font: "Calibri",
                  size: 18,
                  color: "64748B",
                }),
              ],
            }),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.CENTER,
              children: [
                new TextRun({ text: "Page ", font: "Calibri", size: 16, color: "94A3B8" }),
                new TextRun({
                  children: [PageNumber.CURRENT],
                  font: "Calibri",
                  size: 16,
                  color: "94A3B8",
                }),
              ],
            }),
          ],
        }),
      },
      children: [
        new Paragraph({
          spacing: { after: 80 },
          children: [
            new TextRun({
              text: "FREEDOMBOT",
              font: "Calibri",
              size: 20,
              bold: true,
              color: "CA8A04",
            }),
          ],
        }),
        new Paragraph({
          spacing: { after: 120 },
          children: [
            new TextRun({
              text: "Master platform — real-world test script",
              font: "Calibri",
              size: 40,
              bold: true,
              color: "0F172A",
            }),
          ],
        }),
        p(
          "Use this as a live company walkthrough for Dhanu’s feedback. One person per job — no overlapping demo accounts in this script."
        ),
        p(
          "URL: http://localhost:3001   ·   Client slug: acme   ·   Password (all below): bizos-dev-password",
          { bold: true }
        ),
        p(
          "Before testing: docker compose up -d --build && docker compose exec api python -m scripts.reset_dhanu_demo"
        ),
        p(
          "The reset puts billables back to pending, removes draft invoices, cancels pending approvals, " +
            "retires leftover test memory and restores every user's access. Run it before each full walkthrough. " +
            "Use a New chat for each person."
        ),
        p(
          "A correct block always names the area, e.g. “That’s restricted to the Finance area, which your account " +
            "doesn’t have access to — an admin can grant it.” “No record”, a guess, or an offer to create it is a FAIL."
        ),

        h1("1. Accounts for THIS demo only (use these 7)"),
        p(
          "Sign out between people. Use exactly the email in the table — do not mix with other Admin users."
        ),
        table(
          ["Person", "Email (login)", "Job in the story", "Department access", "UI badge (permission)"],
          [
            [
              "Fiona Finance",
              "finance@acme.example.com",
              "Accountant",
              "finance",
              "OPERATOR",
            ],
            [
              "Oscar Ops",
              "ops@acme.example.com",
              "Operations coordinator",
              "ops",
              "OPERATOR",
            ],
            [
              "Hannah HR",
              "hr@acme.example.com",
              "HR recruiter",
              "hr + salary",
              "OPERATOR",
            ],
            [
              "Eddie Employee",
              "employee@acme.example.com",
              "Employee",
              "general only",
              "VIEWER",
            ],
            [
              "Lara Lead",
              "lead@acme.example.com",
              "Team lead",
              "ops",
              "DRAFTER",
            ],
            [
              "Elena Exec",
              "exec@acme.example.com",
              "Executive (approves)",
              "exec, finance, legal, hr, ops",
              "APPROVER",
            ],
            [
              "Ada Admin",
              "admin@acme.example.com",
              "Admin (config only)",
              "all areas",
              "ADMIN",
            ],
          ],
          [1600, 2800, 2000, 2200, 2200]
        ),
        p(" ", { after: 60 }),
        h3("Important — avoid account confusion"),
        bullet(
          "OPERATOR on the badge is a permission level (can run tools). It is NOT “operations department.” Fiona = accountant; Oscar = ops. Both may show OPERATOR."
        ),
        bullet(
          "Department is decided by Restricted data access (finance / ops / hr…), not by the badge name."
        ),
        bullet(
          "Each scenario names exactly one login. Do not substitute “or Omar / Avery / Vera.”"
        ),

        h3("Do NOT use these for the Dhanu walkthrough"),
        p(
          "They still exist in Admin for older RBAC tests. Using them will look like duplicate finance/approver people."
        ),
        table(
          ["Skip this account", "Email", "Why skip"],
          [
            ["Omar Operator", "operator@acme.example.com", "Same finance access as Fiona — duplicate accountant"],
            ["Avery Approver", "approver@acme.example.com", "Overlaps Elena Exec — use Elena only to approve"],
            ["Vera Viewer", "viewer@acme.example.com", "Use Eddie Employee instead"],
            ["Dana Drafter", "drafter@acme.example.com", "Use Lara Lead instead"],
          ],
          [2200, 3600, 5000]
        ),
        p(" ", { after: 80 }),

        h1("2. Scenario A — Department buckets (finance vs ops vs HR)"),
        p(
          "Dhanu: accountant needs finance, not ops/HR. Information sits in buckets."
        ),
        ...scenarioBlock(
          "A1. Accountant asks about pricing",
          "finance@acme.example.com (Fiona Finance only)",
          "Story: Fiona prepares a quote.",
          [
            {
              do: "Type: What is our discount / pricing policy? → Send",
              expect: "Pricing discount = 15% (finance bucket).",
            },
            {
              do: "Type: What are our operating hours? → Send",
              expect: "“Restricted to the Operations area … an admin can grant it.” No hours shown.",
              pass: "Fiona = finance only.",
            },
          ]
        ),
        ...scenarioBlock(
          "A2. Ops cannot see finance",
          "ops@acme.example.com (Oscar Ops only)",
          "Story: Oscar schedules coverage.",
          [
            {
              do: "Type: What is our discount policy? → Send",
              expect: "“Restricted to the Finance area.” No 15%, no offer to create a policy.",
            },
            {
              do: "Type: What are our operating hours? → Send",
              expect: "Mon–Fri 09:00–18:00 ET.",
              pass: "Oscar = ops only. Badge may still say OPERATOR — that is OK.",
            },
          ]
        ),
        ...scenarioBlock(
          "A3. HR sees salary; accountant does not",
          "First hr@… (Hannah), then finance@… (Fiona)",
          "Story: Hiring file vs accountant.",
          [
            {
              do: "As Hannah: What salary band is Engineer II? → Send",
              expect: "$120k–$140k.",
            },
            {
              do: "Sign out. Sign in as Fiona only. Same question.",
              expect: "“Restricted to the Salary / compensation area.” No figures.",
              pass: "HR bucket stays with Hannah.",
            },
          ]
        ),

        h1("3. Scenario B — Configure access (don’t hardcode)"),
        ...scenarioBlock(
          "B1. Admin grants a scope without code",
          "admin@acme.example.com (Ada) → Admin → Users",
          "Story: Eddie needs temporary ops access.",
          [
            {
              do: "Find Eddie Employee only. Turn ON ops under Restricted data access.",
              expect: "ops on Eddie’s scopes.",
            },
            {
              do: "Sign in as employee@… Ask: How do we run monthly payroll from attendance?",
              expect: "Sees the 5-step payroll SOP.",
            },
            {
              do: "As Ada: turn ops OFF on Eddie. As Eddie (New chat): same question.",
              expect: "“Restricted to the Operations area.” No steps shown.",
              pass: "Config from Admin UI — no code, takes effect on the next message.",
            },
          ]
        ),

        h1("4. Scenario C — Approvals (one finance + one exec)"),
        p("Do not use Avery Approver. Elena Exec is the only approver in this script."),
        ...scenarioBlock(
          "C1. Draft invoice waits for Elena",
          "finance@… (Fiona) → Chat; then exec@… (Elena) → Approvals",
          "Story: Invoice for Acme Logistics needs a human.",
          [
            {
              do: "As Fiona: List pending billables for Acme Logistics → Send",
              expect: "Aug $4,500 + Sept $1,200 (~$5,700).",
            },
            {
              do: "As Fiona: Draft an invoice for Acme Logistics from those pending items → Send",
              expect: "Queued for approval (not finished).",
            },
            {
              do: "Sign in as Elena Exec only. Approvals → Approve the invoice action.",
              expect: "Card says “Requested by Fiona Finance (OPERATOR)”. Approved → draft invoice DRAFT-… $5,700 created; Audit shows Elena.",
              pass: "One accountant proposes; one exec approves.",
            },
          ]
        ),
        h3("Four modes (plain words)"),
        bullet("ADVISE — talk only."),
        bullet("DRAFT — drafts inside workspace."),
        bullet("WAIT_FOR_APPROVAL — queue first (demo default)."),
        bullet("AUTO_WITHIN_SCOPE — only inside pre-approved scope."),

        h1("5. Scenario D — Payroll process memory"),
        p("Use Oscar Ops only here (not Lara). Lara is reserved for Scenario G as “lead.”"),
        ...scenarioBlock(
          "D1. Ops reuses last month’s process",
          "ops@acme.example.com (Oscar Ops only)",
          "Story: Month-end attendance again.",
          [
            {
              do: "Type: We uploaded this month’s attendance. Help me create payslips the same way we did last month. → Send",
              expect:
                "SOP “Monthly payroll from attendance”: upload → exceptions → pay rules → draft payslips for Approver → archive.",
              pass: "Process remembered; not a silent bank payout.",
            },
          ]
        ),

        h1("6. Scenario E — HR resume match"),
        ...scenarioBlock(
          "E1. Hannah matches Jordan Lee",
          "hr@… then employee@… (no other HR/finance users)",
          "Story: Job opening already recorded; resume in memory.",
          [
            {
              do: "As Hannah: Does Jordan Lee fit the Operations Coordinator job opening? Compare the resume. → Send",
              expect: "Likely fit — 4/4 must-haves met; missing SQL basics (nice-to-have); human decides hire.",
            },
            {
              do: "As Eddie only: same question.",
              expect: "“Restricted to the HR area.” No resume details.",
              pass: "Hannah only for hiring assist.",
            },
          ]
        ),

        h1("7. Scenario F — Invoice access wall"),
        ...scenarioBlock(
          "F1. Fiona after Elena approved (no double billing)",
          "finance@acme.example.com only",
          "Run after Scenario C. The billables are now on the draft invoice.",
          [
            {
              do: "Show pending billables for Acme Logistics and draft an invoice.",
              expect: "“No pending billables — nothing to invoice”, points to the existing $5,700 draft. Nothing new in Approvals.",
            },
            {
              do: "Show invoices for Acme Logistics",
              expect: "DRAFT-… draft, $5,700.",
              pass: "Finance path works and cannot queue an empty or duplicate invoice.",
            },
          ]
        ),
        ...scenarioBlock(
          "F2. Oscar is blocked",
          "ops@acme.example.com only",
          "Story: Ops must not see billing.",
          [
            {
              do: "List pending billables for Acme Logistics / generate their invoice.",
              expect: "“Requires Finance access.” No amounts, no search for them another way.",
              pass: "No second finance account needed to prove the wall.",
            },
          ]
        ),

        h1("8. Scenario G — Employee / lead / exec (three fixed logins)"),
        p("Exactly three accounts — no substitutes."),
        ...scenarioBlock(
          "G1. Eddie → Lara → Elena",
          "employee@… → lead@… → exec@…",
          "Story: Same company, different altitude.",
          [
            {
              do: "As Eddie: What is my scope of work? Also the full Acme Logistics master agreement.",
              expect: "Scope of work shown (attendance by 3rd business day); MSA “restricted to the Legal area”.",
            },
            {
              do: "As Lara: How do we run monthly payroll from attendance?",
              expect: "Ops payroll SOP shown.",
            },
            {
              do: "As Lara: What did the board decide for Q3 hiring?",
              expect: "“Restricted to the Executive / board area.”",
            },
            {
              do: "As Elena: What did the board decide for Q3 hiring?",
              expect: "Hiring freeze for non-revenue roles.",
              pass: "Three levels, three logins only.",
            },
          ]
        ),

        h1("9. Scorecard"),
        table(
          ["#", "Check", "Pass?"],
          [
            ["1", "Only the 7 accounts above used (no Omar/Avery/Vera/Dana)", ""],
            ["2", "Fiona ≠ Oscar (finance vs ops buckets)", ""],
            ["3", "Admin toggles Eddie’s ops scope", ""],
            ["4", "Fiona drafts invoice; Elena alone approves", ""],
            ["5", "Oscar replays payroll SOP", ""],
            ["6", "Hannah match; Eddie blocked", ""],
            ["7", "Oscar blocked on Acme Logistics billables", ""],
            ["8", "Eddie / Lara / Elena different info", ""],
          ],
          [800, 8000, 2000]
        ),
        p(" ", { after: 80 }),
        p("Tester: __________________    Date: __________    Demo for: Venkat / client  ☐"),

        h1("10. Troubleshooting"),
        bullet("Wrong person in Admin list → ignore Omar/Avery/Vera/Dana for this doc."),
        bullet("People missing, or data left over from an earlier run → docker compose exec api python -m scripts.reset_dhanu_demo"),
        bullet("An answer uses an earlier person’s context → start a New chat for each person."),
        bullet("Fiona badge says OPERATOR → expected (permission). Her department is still finance."),
        bullet("Rebuild UI/API → docker compose up -d --build api ui"),
      ],
    },
  ],
});

const out = path.join(__dirname, "Dhanu_master_platform_real_world_test.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf);
  console.log("Wrote", out);
});
