"use strict";
var pptxgen = require("pptxgenjs");
var path    = require("path");

// ─── Brand palette ────────────────────────────────────────────────────────
var BLUE   = "2563EB";
var DARK   = "1E293B";
var NAVY   = "1E3A5F";
var LGRAY  = "F1F5F9";
var SKY    = "38BDF8";
var WHITE  = "FFFFFF";
var SLATE  = "64748B";
var MUTED  = "94A3B8";
var BFDBFE = "BFDBFE";
var GREEN  = "059669";
var AMBER  = "D97706";
var RED    = "DC2626";
var PURPLE = "7C3AED";

var SS   = "C:/Users/mikeb/OneDrive/Desktop/Axiometica/MarketDocs/ProductScreenShots/ScreenShotsCropped";
var LOGO = "C:/Users/mikeb/OneDrive/Desktop/Axiometica/MarketDocs/Branding/AxioLogo.svg";

var pres = new pptxgen();
pres.layout  = "LAYOUT_16x9";
pres.title   = "Axiometica AIR — The Path to Fully Autonomous IT Operations";
pres.author  = "Axiometica";

function sh()   { return { type:"outer", blur:10, offset:4, angle:135, color:"000000", opacity:0.18 }; }
function shSm() { return { type:"outer", blur:5,  offset:2, angle:135, color:"000000", opacity:0.12 }; }

function hdr(sl, txt) {
  sl.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.82, fill:{color:NAVY} });
  sl.addShape(pres.shapes.RECTANGLE, { x:0, y:0.82, w:10, h:0.05, fill:{color:BLUE} });
  sl.addText(txt, { x:0.4, y:0, w:9.2, h:0.82, fontSize:20, bold:true, color:WHITE, valign:"middle", margin:0 });
}

function footer(sl) {
  sl.addShape(pres.shapes.RECTANGLE, { x:0, y:5.38, w:10, h:0.245, fill:{color:DARK} });
  sl.addText("Axiometica AIR v1.2.0  |  Confidential", {
    x:0.35, y:5.38, w:7, h:0.245, fontSize:8, color:MUTED, valign:"middle", margin:0
  });
  try { sl.addImage({ path:LOGO, x:8.6, y:5.4, w:1.05, h:0.21 }); }
  catch(e) { sl.addText("AXIOMETICA", { x:8.5, y:5.38, w:1.4, h:0.245, fontSize:8, bold:true, color:SKY, valign:"middle", margin:0 }); }
}

function logo(sl, x, y, w, h) {
  try { sl.addImage({ path:LOGO, x:x, y:y, w:w, h:h }); }
  catch(e) { sl.addText("AXIOMETICA", { x:x, y:y, w:w, h:h, fontSize:14, bold:true, color:SKY, valign:"middle", margin:0 }); }
}

function ss(n) { return path.join(SS, n); }

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 1 — Title
// ═══════════════════════════════════════════════════════════════════════════
var s1 = pres.addSlide();
s1.background = { color: DARK };

s1.addShape(pres.shapes.RECTANGLE, { x:0,    y:0, w:0.14, h:5.625, fill:{color:BLUE} });
s1.addShape(pres.shapes.RECTANGLE, { x:6.55, y:0, w:3.45, h:5.625, fill:{color:NAVY} });
s1.addShape(pres.shapes.RECTANGLE, { x:6.55, y:0, w:0.04, h:5.625, fill:{color:BLUE} });

logo(s1, 0.45, 0.32, 2.7, 0.76);

s1.addText("Axiometica AIR", {
  x:0.35, y:1.25, w:5.9, h:1.0, fontSize:50, bold:true, color:WHITE, margin:0
});
s1.addText("The Path to Fully Autonomous\nIncident Resolution", {
  x:0.35, y:2.35, w:5.9, h:0.95, fontSize:18, color:SKY, margin:0
});
s1.addText("Today, AI governs risk and humans stay in control.\nTomorrow, the platform remediates everything — autonomously.\nAxisometica AIR bridges that gap.", {
  x:0.35, y:3.42, w:5.9, h:1.0, fontSize:11.5, color:MUTED, margin:0
});

// Right panel — the three stages teased
var stages1 = [
  { lbl:"Assisted",   sub:"AI recommends,\nhuman decides",    col:AMBER  },
  { lbl:"Governed",   sub:"AI acts, human\narroves risk",     col:BLUE   },
  { lbl:"Autonomous", sub:"AI resolves\neverything",          col:GREEN  }
];
stages1.forEach(function(st, i) {
  var y = 1.15 + i * 1.35;
  s1.addShape(pres.shapes.RECTANGLE, { x:6.65, y:y, w:0.06, h:1.1, fill:{color:st.col} });
  s1.addText(st.lbl, { x:6.82, y:y+0.04, w:2.9, h:0.38, fontSize:13, bold:true, color:WHITE, margin:0 });
  s1.addText(st.sub, { x:6.82, y:y+0.44, w:2.9, h:0.58, fontSize:9,  color:MUTED,  margin:0 });
  // Arrow pointing to "Governed" (current state)
  if (i === 1) {
    s1.addShape(pres.shapes.RECTANGLE, { x:9.55, y:y+0.22, w:0.2, h:0.05, fill:{color:BLUE} });
    s1.addText("YOU\nARE\nHERE", { x:9.52, y:y-0.12, w:0.45, h:0.4, fontSize:5.5, color:BLUE, bold:true, align:"center", margin:0 });
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 2 — The Vision: Fully Autonomous Future
// ═══════════════════════════════════════════════════════════════════════════
var s2 = pres.addSlide();
s2.background = { color: DARK };
footer(s2);

s2.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.82, fill:{color:NAVY} });
s2.addShape(pres.shapes.RECTANGLE, { x:0, y:0.82, w:10, h:0.05, fill:{color:BLUE} });
s2.addText("The Future of IT Operations Is Fully Autonomous", {
  x:0.4, y:0, w:9.2, h:0.82, fontSize:20, bold:true, color:WHITE, valign:"middle", margin:0
});

// Central bold statement
s2.addText("Imagine infrastructure that heals itself.", {
  x:0.5, y:1.0, w:9.0, h:0.6,
  fontSize:26, bold:true, color:WHITE, align:"center", margin:0
});
s2.addText("No on-call wakeups. No war rooms. No manual runbooks.\nEvery incident detected, investigated, and resolved — before anyone notices.", {
  x:1.0, y:1.65, w:8.0, h:0.7,
  fontSize:13, color:MUTED, align:"center", margin:0
});

// 3 future-state cards
var future = [
  { col:GREEN,  icon:"NO",  lbl:"No Manual Triage",    body:"Every alert is instantly classified, scored, and routed by AI — no human needed to read and categorise" },
  { col:BLUE,   icon:"NO",  lbl:"No Routine On-Call",  body:"Safe, well-understood incidents resolve themselves. Engineers sleep. The platform does not." },
  { col:PURPLE, icon:"NO",  lbl:"No Manual Reporting", body:"AI writes the executive summary, the post-mortem, and the RCA — the moment the incident closes" }
];
future.forEach(function(f, i) {
  var x = 0.3 + i * 3.23;
  s2.addShape(pres.shapes.RECTANGLE, { x:x, y:2.55, w:3.05, h:2.55, fill:{color:"162033"}, shadow:sh() });
  s2.addShape(pres.shapes.RECTANGLE, { x:x, y:2.55, w:3.05, h:0.06, fill:{color:f.col} });

  // Large "NO" circle
  s2.addShape(pres.shapes.OVAL, { x:x+1.07, y:2.72, w:0.9, h:0.9, fill:{color:f.col} });
  s2.addText(f.icon, { x:x+1.07, y:2.72, w:0.9, h:0.9, fontSize:14, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });

  s2.addText(f.lbl,  { x:x+0.1, y:3.74, w:2.85, h:0.42, fontSize:12, bold:true, color:WHITE,  align:"center", margin:0 });
  s2.addText(f.body, { x:x+0.15, y:4.22, w:2.75, h:0.82, fontSize:9.5, color:MUTED, align:"center", margin:0 });
});

s2.addText("This is not a distant dream. Axiometica AIR is the path to get there — safely.", {
  x:0.5, y:5.08, w:9.0, h:0.3, fontSize:10, color:SKY, align:"center", italic:true, margin:0
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 3 — The Problem Today (Real Stats)
// ═══════════════════════════════════════════════════════════════════════════
var s3 = pres.addSlide();
s3.background = { color: LGRAY };
hdr(s3, "Why the Status Quo Is Failing: The Real Cost of Manual IT Ops");
footer(s3);

var painStats = [
  { x:0.22, stat:"$5,600", unit:"/min", color:RED,    lbl:"Downtime Cost",        src:"Gartner",      desc:"Average cost per minute of unplanned IT outage" },
  { x:2.72, stat:"4–8h",   unit:"",     color:AMBER,  lbl:"Avg MTTR",             src:"PagerDuty",    desc:"Mean time to resolve critical production incidents" },
  { x:5.22, stat:"47%",    unit:"",     color:PURPLE, lbl:"Consider Quitting",    src:"PagerDuty 22", desc:"On-call engineers citing alert fatigue as primary driver" },
  { x:7.72, stat:"80%",    unit:"",     color:BLUE,   lbl:"Outages from Changes", src:"Gartner",      desc:"IT outages traced to application or infrastructure changes" }
];

painStats.forEach(function(c) {
  s3.addShape(pres.shapes.RECTANGLE, { x:c.x, y:1.02, w:2.3, h:3.95, fill:{color:WHITE}, shadow:sh() });
  s3.addShape(pres.shapes.RECTANGLE, { x:c.x, y:1.02, w:2.3, h:0.07, fill:{color:c.color} });
  s3.addText(c.stat, { x:c.x, y:1.22, w:2.3, h:0.88, fontSize:38, bold:true, color:c.color, align:"center", margin:0 });
  if (c.unit) { s3.addText(c.unit, { x:c.x, y:2.07, w:2.3, h:0.3, fontSize:13, color:c.color, align:"center", margin:0 }); }
  s3.addText(c.lbl, { x:c.x+0.1, y:2.44, w:2.1, h:0.44, fontSize:12, bold:true, color:DARK, align:"center", margin:0 });
  s3.addShape(pres.shapes.RECTANGLE, { x:c.x+0.5, y:3.02, w:1.3, h:0.24, fill:{color:LGRAY} });
  s3.addText(c.src, { x:c.x+0.5, y:3.02, w:1.3, h:0.24, fontSize:8, color:SLATE, align:"center", valign:"middle", italic:true, margin:0 });
  s3.addText(c.desc, { x:c.x+0.1, y:3.35, w:2.1, h:0.78, fontSize:9.5, color:SLATE, align:"center", margin:0 });
});

s3.addText("Every hour your team spends manually fighting incidents is an hour not spent preventing the next one.", {
  x:0.3, y:5.06, w:9.4, h:0.3, fontSize:10, color:SLATE, align:"center", italic:true, margin:0
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 4 — The Autonomy Journey: From Manual to Autonomous
// ═══════════════════════════════════════════════════════════════════════════
var s4 = pres.addSlide();
s4.background = { color: WHITE };
hdr(s4, "The Autonomy Journey: Where AIR Takes You");
footer(s4);

// Spectrum bar
var barY = 1.55;
var barH = 0.55;

// Zone backgrounds
s4.addShape(pres.shapes.RECTANGLE, { x:0.3,  y:barY, w:2.9, h:barH, fill:{color:"FEE2E2"} }); // red zone
s4.addShape(pres.shapes.RECTANGLE, { x:3.2,  y:barY, w:3.6, h:barH, fill:{color:BFDBFE} }); // blue zone
s4.addShape(pres.shapes.RECTANGLE, { x:6.8,  y:barY, w:2.9, h:barH, fill:{color:"D1FAE5"} }); // green zone

// Zone labels in the bar
s4.addText("Manual Operations",  { x:0.3,  y:barY, w:2.9, h:barH, fontSize:10, bold:true, color:RED,   align:"center", valign:"middle", margin:0 });
s4.addText("Governed Autonomy",  { x:3.2,  y:barY, w:3.6, h:barH, fontSize:10, bold:true, color:BLUE,  align:"center", valign:"middle", margin:0 });
s4.addText("Full Autonomy",      { x:6.8,  y:barY, w:2.9, h:barH, fontSize:10, bold:true, color:GREEN, align:"center", valign:"middle", margin:0 });

// Direction arrow
s4.addShape(pres.shapes.LINE, { x:0.3, y:barY+barH+0.08, w:9.4, h:0, line:{color:BLUE, width:2} });
s4.addShape(pres.shapes.RECTANGLE, { x:9.55, y:barY+barH+0.04, w:0.14, h:0.18, fill:{color:BLUE} });
s4.addText("Increasing Autonomy", { x:3.5, y:barY+barH+0.13, w:3, h:0.25, fontSize:9, color:BLUE, italic:true, margin:0 });

// "YOU ARE HERE" marker on Governed zone
s4.addShape(pres.shapes.RECTANGLE, { x:4.68, y:barY-0.38, w:0.64, h:0.32, fill:{color:NAVY} });
s4.addText("TODAY", { x:4.68, y:barY-0.38, w:0.64, h:0.32, fontSize:8, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
s4.addShape(pres.shapes.LINE, { x:5.0, y:barY-0.05, w:0, h:0.05, line:{color:NAVY, width:2} });

// Three explanation panels
var panels = [
  {
    x:0.3, col:RED, title:"Without AIR",
    items:[
      "Every alert read by a human",
      "Runbooks executed manually",
      "Escalation chains slow resolution",
      "Risk assessment by gut feel",
      "Post-mortems written by hand"
    ]
  },
  {
    x:3.2, col:BLUE, title:"AIR Today: Governed Autonomy",
    items:[
      "AI handles all safe incidents autonomously",
      "High-risk actions require human approval",
      "Approvals teach the AI — confidence grows",
      "Risk scored from live CMDB + history",
      "Each cycle reduces need for human input"
    ]
  },
  {
    x:6.8, col:GREEN, title:"AIR Tomorrow: Full Autonomy",
    items:[
      "Platform resolves incidents end-to-end",
      "Confidence earned through track record",
      "Humans review outcomes, not decisions",
      "Self-optimising based on results",
      "Engineering teams focus on innovation"
    ]
  }
];

panels.forEach(function(p) {
  var w = p.x === 3.2 ? 3.6 : 2.9;
  s4.addShape(pres.shapes.RECTANGLE, { x:p.x, y:2.42, w:w, h:2.7, fill:{color:LGRAY}, shadow:shSm() });
  s4.addShape(pres.shapes.RECTANGLE, { x:p.x, y:2.42, w:w, h:0.06, fill:{color:p.col} });
  s4.addText(p.title, { x:p.x+0.1, y:2.5, w:w-0.2, h:0.42, fontSize:11, bold:true, color:p.col, margin:0 });
  s4.addText(p.items.map(function(item, i) {
    return { text:item, options:{ bullet:true, breakLine: i < p.items.length-1 } };
  }), { x:p.x+0.1, y:2.98, w:w-0.2, h:2.05, fontSize:9.5, color:DARK });
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 5 — The Learning Loop: How Confidence Grows
// ═══════════════════════════════════════════════════════════════════════════
var s5 = pres.addSlide();
s5.background = { color: DARK };
footer(s5);

s5.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.82, fill:{color:NAVY} });
s5.addShape(pres.shapes.RECTANGLE, { x:0, y:0.82, w:10, h:0.05, fill:{color:BLUE} });
s5.addText("The Learning Loop: How AIR Earns the Right to Act Autonomously", {
  x:0.4, y:0, w:9.2, h:0.82, fontSize:20, bold:true, color:WHITE, valign:"middle", margin:0
});

// 4 loop boxes arranged in a rectangle
var loopBoxes = [
  { x:0.4,  y:1.1,  col:BLUE,   n:"1", title:"Incident Detected",      body:"AI instantly classifies severity,\nroutes to the right queue,\nand assesses risk level from CMDB data" },
  { x:5.6,  y:1.1,  col:AMBER,  n:"2", title:"High Risk? Human Governs",body:"Risky remediations pause for approval.\nThe operator sees full AI context\nand approves, modifies, or rejects" },
  { x:5.6,  y:3.35, col:GREEN,  n:"3", title:"AI Learns the Pattern",   body:"Approval decisions feed into\nthe knowledge base. Confidence scores\nrise for this incident type" },
  { x:0.4,  y:3.35, col:PURPLE, n:"4", title:"Next Time: Auto-Resolved", body:"When confidence exceeds the threshold,\nthe same scenario resolves autonomously.\nThe governance loop closes" }
];

loopBoxes.forEach(function(b) {
  s5.addShape(pres.shapes.RECTANGLE, { x:b.x, y:b.y, w:3.8, h:1.9, fill:{color:"162033"}, shadow:sh() });
  s5.addShape(pres.shapes.RECTANGLE, { x:b.x, y:b.y, w:3.8, h:0.06, fill:{color:b.col} });
  s5.addShape(pres.shapes.OVAL,      { x:b.x+0.14, y:b.y+0.18, w:0.52, h:0.52, fill:{color:b.col} });
  s5.addText(b.n, { x:b.x+0.14, y:b.y+0.18, w:0.52, h:0.52, fontSize:16, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
  s5.addText(b.title, { x:b.x+0.78, y:b.y+0.18, w:2.9, h:0.52, fontSize:11, bold:true, color:WHITE, valign:"middle", margin:0 });
  s5.addText(b.body,  { x:b.x+0.14, y:b.y+0.82, w:3.52, h:0.95, fontSize:9.5, color:MUTED, margin:0 });
});

// Center element
s5.addShape(pres.shapes.RECTANGLE, { x:4.35, y:1.88, w:1.3, h:1.8, fill:{color:NAVY}, shadow:shSm() });
s5.addText("CONFIDENCE\nGROWS", { x:4.35, y:1.98, w:1.3, h:1.0, fontSize:10, bold:true, color:SKY, align:"center", margin:0 });
s5.addText("with every\nresolution", { x:4.35, y:3.05, w:1.3, h:0.55, fontSize:8, color:MUTED, align:"center", margin:0 });

// Arrows between boxes (→ right, ↓ down, ← left, ↑ up)
// → from box 1 to box 2 (top)
s5.addShape(pres.shapes.LINE, { x:4.2,  y:2.05, w:1.4, h:0, line:{color:BLUE, width:1.5} });
// ↓ from box 2 to box 3 (right side)
s5.addShape(pres.shapes.LINE, { x:7.5,  y:3.0,  w:0, h:0.35, line:{color:AMBER, width:1.5} });
// ← from box 3 to box 4 (bottom)
s5.addShape(pres.shapes.LINE, { x:4.2,  y:4.3,  w:1.4, h:0, line:{color:GREEN, width:1.5} });
// ↑ from box 4 back to box 1 (left side)
s5.addShape(pres.shapes.LINE, { x:2.3,  y:3.0,  w:0, h:0.35, line:{color:PURPLE, width:1.5} });

s5.addText("The more AIR operates, the more it learns — and the less human intervention each cycle requires.", {
  x:0.3, y:5.38, w:9.4, h:0.22, fontSize:9, color:MUTED, align:"center", italic:true, margin:0
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 6 — NOC vs AIR Comparison
// ═══════════════════════════════════════════════════════════════════════════
var s6 = pres.addSlide();
s6.background = { color: WHITE };
hdr(s6, "Traditional NOC vs Axiometica AIR: What Changes");
footer(s6);

var rows = [
  ["Operator reads alert, creates ticket",           "Alert auto-qualifies and creates incident"],
  ["Operator researches runbook manually",            "RAG selects best-fit runbook from history"],
  ["Storm floods queue with individual tickets",      "Storm detection groups into one correlated incident"],
  ["Operator executes remediation steps",             "Pipeline executes autonomously for safe incidents"],
  ["Risk judgement is manual and subjective",         "Risk score quantified from CMDB and historical data"],
  ["Post-incident report written manually",           "LLM generates executive and technical summary"],
  ["Runbook library grows slowly by hand",            "AI generates runbooks from novel incidents"],
  ["Operators query dashboards to find context",      "Chat Agent answers in natural language"],
  ["Alert notifications require portal login",        "Slack bot delivers context and accepts queries in-channel"],
  ["Platform tuned by intuition",                     "Self-optimisation driven by operational data"]
];

var tableData = [
  [
    { text:"Traditional NOC", options:{ bold:true, color:WHITE, fontSize:11, fill:{color:NAVY}, margin:[5,10,5,10] } },
    { text:"Axiometica AIR",  options:{ bold:true, color:WHITE, fontSize:11, fill:{color:NAVY}, margin:[5,10,5,10] } }
  ]
];
rows.forEach(function(row, i) {
  var bg = i % 2 === 0 ? WHITE : "F8FAFC";
  tableData.push([
    { text:row[0], options:{ fontSize:10, color:DARK,  fill:{color:bg}, margin:[4,10,4,10] } },
    { text:row[1], options:{ fontSize:10, color:BLUE,  fill:{color:bg}, margin:[4,10,4,10], bold:true } }
  ]);
});

s6.addTable(tableData, {
  x:0.25, y:0.95, w:9.5, h:4.4,
  colW:[4.75, 4.75],
  border:{ type:"solid", pt:0.5, color:"E2E8F0" },
  autoPage:false
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 7 — 5-Stage Pipeline (governance highlighted)
// ═══════════════════════════════════════════════════════════════════════════
var s7 = pres.addSlide();
s7.background = { color: DARK };
footer(s7);

s7.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.82, fill:{color:NAVY} });
s7.addShape(pres.shapes.RECTANGLE, { x:0, y:0.82, w:10, h:0.05, fill:{color:BLUE} });
s7.addText("How It Works: 5 Agents, One Autonomous Pipeline", {
  x:0.4, y:0, w:9.2, h:0.82, fontSize:20, bold:true, color:WHITE, valign:"middle", margin:0
});

var stgs = [
  { n:"1", name:"Triage",       agent:"TriageAgent",       col:BLUE,   desc:"Classifies severity,\nroutes & scores\nrisk instantly" },
  { n:"2", name:"Governance",   agent:"GovernanceAgent",   col:AMBER,  desc:"Validates policies,\nflags risky actions,\nrequests approval", highlight:true },
  { n:"3", name:"Mechanics",    agent:"MechanicAgent",     col:GREEN,  desc:"AI selects best\nrunbook via 5-tier\nRAG matching" },
  { n:"4", name:"Tool Registry",agent:"ToolRegistryAgent", col:PURPLE, desc:"Dispatches tools\n& connectors,\nexecutes API calls" },
  { n:"5", name:"Validation",   agent:"ValidationAgent",   col:RED,    desc:"Confirms resolution,\nupdates CMDB,\ngenerates summary" }
];

var bW = 1.72;
stgs.forEach(function(st, i) {
  var x = 0.28 + i * 1.9;
  var fillCol = st.highlight ? "1E3354" : "162033";
  s7.addShape(pres.shapes.RECTANGLE, { x:x, y:1.02, w:bW, h:3.55, fill:{color:fillCol}, shadow:sh() });
  s7.addShape(pres.shapes.RECTANGLE, { x:x, y:1.02, w:bW, h:0.06, fill:{color:st.col} });

  if (st.highlight) {
    // "Human governs" callout box inside
    s7.addShape(pres.shapes.RECTANGLE, { x:x+0.08, y:3.0, w:bW-0.16, h:0.52, fill:{color:AMBER} });
    s7.addText("Human approval\nfor risky actions", { x:x+0.08, y:3.0, w:bW-0.16, h:0.52, fontSize:8, bold:true, color:DARK, align:"center", valign:"middle", margin:0 });
  }

  s7.addShape(pres.shapes.OVAL, { x:x+0.55, y:1.18, w:0.62, h:0.62, fill:{color:st.col} });
  s7.addText(st.n, { x:x+0.55, y:1.18, w:0.62, h:0.62, fontSize:20, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
  s7.addText(st.name,  { x:x+0.05, y:1.92, w:bW-0.1, h:0.4, fontSize:11, bold:true, color:WHITE, align:"center", margin:0 });
  s7.addText(st.agent, { x:x+0.05, y:2.35, w:bW-0.1, h:0.28, fontSize:8, italic:true, color:st.col, align:"center", margin:0 });
  s7.addText(st.desc,  { x:x+0.1,  y:2.7,  w:bW-0.2, h:0.78, fontSize:9, color:MUTED, align:"center", margin:0 });

  if (i < stgs.length-1) {
    s7.addShape(pres.shapes.LINE, { x:x+bW+0.03, y:2.3, w:0.14, h:0, line:{color:BLUE, width:1.5} });
  }
});

// Learning feedback arrow label
s7.addShape(pres.shapes.LINE, { x:0.28, y:4.68, w:9.44, h:0, line:{color:BLUE, width:1, dashType:"dash"} });
s7.addText("Every resolution feeds the confidence model — future incidents of the same type require less governance", {
  x:0.3, y:4.75, w:9.4, h:0.32, fontSize:9, color:MUTED, align:"center", italic:true, margin:0
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 8 — Dashboard
// ═══════════════════════════════════════════════════════════════════════════
var s8 = pres.addSlide();
s8.background = { color: LGRAY };
hdr(s8, "Live Operations Dashboard: Real Outcomes, Real Time");
footer(s8);
s8.addImage({ path:ss("Screenshot (1).png"), x:0.25, y:0.99, w:9.5, h:4.16, shadow:sh() });

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 9 — AI Incident Intelligence
// ═══════════════════════════════════════════════════════════════════════════
var s9 = pres.addSlide();
s9.background = { color: WHITE };
hdr(s9, "AI Incident Intelligence: Root Cause in Seconds, Not Hours");
footer(s9);

s9.addImage({ path:ss("Screenshot (4).png"), x:0.25, y:0.99, w:6.1, h:4.1, shadow:sh() });

var incF = [
  { col:BLUE,   t:"Executive AI Summary",  b:"Instant root-cause analysis and confidence score — no L2 needed" },
  { col:GREEN,  t:"Full Audit Trail",       b:"Every agent action timestamped — complete accountability for autonomous actions" },
  { col:AMBER,  t:"Governance in Context",  b:"Risky remediations surface with full AI reasoning so approvers can decide in seconds" },
  { col:PURPLE, t:"Slack ChatOps Thread",   b:"Incident context pushed to the team automatically — no portal login required" }
];
incF.forEach(function(f, i) {
  var y = 1.08 + i * 0.98;
  s9.addShape(pres.shapes.RECTANGLE, { x:6.55, y:y, w:3.2, h:0.82, fill:{color:LGRAY}, shadow:shSm() });
  s9.addShape(pres.shapes.RECTANGLE, { x:6.55, y:y, w:0.06, h:0.82, fill:{color:f.col} });
  s9.addText(f.t, { x:6.72, y:y+0.06, w:2.9, h:0.3,  fontSize:10, bold:true, color:DARK,  margin:0 });
  s9.addText(f.b, { x:6.72, y:y+0.38, w:2.9, h:0.38, fontSize:9,  color:SLATE, margin:0 });
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 10 — Storm Detection
// ═══════════════════════════════════════════════════════════════════════════
var s10 = pres.addSlide();
s10.background = { color: LGRAY };
hdr(s10, "Event Storm Detection: One Root Cause, Not 50 Tickets");
footer(s10);
s10.addImage({ path:ss("Screenshot (11).png"), x:0.25, y:0.99, w:9.5, h:4.16, shadow:sh() });

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 11 — Self-Generating Runbooks
// ═══════════════════════════════════════════════════════════════════════════
var s11 = pres.addSlide();
s11.background = { color: WHITE };
hdr(s11, "Self-Generating Runbooks: The Library That Grows Itself");
footer(s11);

s11.addImage({ path:ss("Screenshot (39).png"), x:0.25, y:0.99, w:6.1, h:4.1, shadow:sh() });

var rbF = [
  { col:BLUE,   t:"AI-Generated Runbooks",    b:"Novel incidents trigger automatic runbook creation — the knowledge base expands continuously" },
  { col:GREEN,  t:"RAG-Powered Selection",     b:"5-tier AI matching finds the best historical runbook for every new incident type" },
  { col:AMBER,  t:"Governed Before Execution", b:"Risk-scored actions pause for human approval until confidence is established" },
  { col:PURPLE, t:"Simulated Before Deployed", b:"Test Run and Simulate modes validate logic safely before live autonomous execution" }
];
rbF.forEach(function(f, i) {
  var y = 1.08 + i * 0.98;
  s11.addShape(pres.shapes.RECTANGLE, { x:6.55, y:y, w:3.2, h:0.82, fill:{color:LGRAY}, shadow:shSm() });
  s11.addShape(pres.shapes.RECTANGLE, { x:6.55, y:y, w:0.06, h:0.82, fill:{color:f.col} });
  s11.addText(f.t, { x:6.72, y:y+0.06, w:2.9, h:0.3,  fontSize:10, bold:true, color:DARK,  margin:0 });
  s11.addText(f.b, { x:6.72, y:y+0.38, w:2.9, h:0.38, fontSize:9,  color:SLATE, margin:0 });
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 12 — Business Outcomes
// ═══════════════════════════════════════════════════════════════════════════
var s12 = pres.addSlide();
s12.background = { color: DARK };
footer(s12);

s12.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.82, fill:{color:NAVY} });
s12.addShape(pres.shapes.RECTANGLE, { x:0, y:0.82, w:10, h:0.05, fill:{color:BLUE} });
s12.addText("What You Get on the Journey to Autonomous Ops", {
  x:0.4, y:0, w:9.2, h:0.82, fontSize:20, bold:true, color:WHITE, valign:"middle", margin:0
});

var outs = [
  { col:BLUE,   n:"01", t:"MTTR Drops Dramatically",    b:"Safe incidents resolve in minutes without human intervention. The backlog clears itself." },
  { col:GREEN,  n:"02", t:"SRE Time Reclaimed",          b:"Toil is automated end-to-end. Engineers focus on reliability and architecture, not runbook execution." },
  { col:AMBER,  n:"03", t:"Alert Noise Eliminated",      b:"Storm detection and AI correlation reduce ticket volume. Teams see fewer, higher-quality signals." },
  { col:PURPLE, n:"04", t:"Confidence Without Risk",     b:"Human governance ensures autonomy is earned, not assumed. Every approval improves the model." }
];

outs.forEach(function(o, i) {
  var col = i % 2;
  var row = Math.floor(i / 2);
  var x = 0.28 + col * 4.88;
  var y = 1.02 + row * 1.92;

  s12.addShape(pres.shapes.RECTANGLE, { x:x, y:y, w:4.65, h:1.75, fill:{color:"162033"}, shadow:sh() });
  s12.addShape(pres.shapes.RECTANGLE, { x:x, y:y, w:4.65, h:0.06, fill:{color:o.col} });
  s12.addShape(pres.shapes.OVAL,      { x:x+0.15, y:y+0.18, w:0.5, h:0.5, fill:{color:o.col} });
  s12.addText(o.n, { x:x+0.15, y:y+0.18, w:0.5, h:0.5, fontSize:9, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
  s12.addText(o.t, { x:x+0.78, y:y+0.18, w:3.75, h:0.45, fontSize:12, bold:true, color:WHITE, valign:"middle", margin:0 });
  s12.addText(o.b, { x:x+0.18, y:y+0.78, w:4.3,  h:0.85, fontSize:9.5, color:MUTED, margin:0 });
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 13 — Integrations
// ═══════════════════════════════════════════════════════════════════════════
var s13 = pres.addSlide();
s13.background = { color: WHITE };
hdr(s13, "Works With Your Stack: Native Connectors Out of the Box");
footer(s13);

var cats = [
  { cat:"Alerting & Incident",   col:RED,    items:["PagerDuty","Opsgenie","Prometheus","Grafana"] },
  { cat:"ITSM & Collaboration",  col:BLUE,   items:["Jira","ServiceNow","Slack","Microsoft Teams"] },
  { cat:"Cloud & Infrastructure",col:GREEN,  items:["AWS CloudWatch","Azure Monitor","Kubernetes","GitHub"] },
  { cat:"Monitoring & Logs",     col:PURPLE, items:["Datadog","Splunk","Elasticsearch","Custom REST"] }
];

cats.forEach(function(cat, ci) {
  var y = 1.02 + ci * 1.1;
  s13.addShape(pres.shapes.RECTANGLE, { x:0.25, y:y, w:2.1, h:0.82, fill:{color:cat.col} });
  s13.addText(cat.cat, { x:0.25, y:y, w:2.1, h:0.82, fontSize:9, bold:true, color:WHITE, align:"center", valign:"middle", margin:5 });
  cat.items.forEach(function(name, ti) {
    var tx = 2.5 + ti * 1.88;
    s13.addShape(pres.shapes.RECTANGLE, { x:tx, y:y, w:1.74, h:0.82, fill:{color:LGRAY}, shadow:shSm() });
    s13.addShape(pres.shapes.RECTANGLE, { x:tx, y:y, w:1.74, h:0.05, fill:{color:cat.col} });
    s13.addText(name, { x:tx, y:y+0.07, w:1.74, h:0.7, fontSize:9.5, bold:true, color:DARK, align:"center", valign:"middle", margin:0 });
  });
});

s13.addText("Open connector SDK and webhook support for custom integrations", {
  x:0.25, y:5.45, w:9.5, h:0.18, fontSize:9, color:SLATE, align:"center", italic:true, margin:0
});

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 14 — Call to Action
// ═══════════════════════════════════════════════════════════════════════════
var s14 = pres.addSlide();
s14.background = { color: DARK };

s14.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:0.14, h:5.625, fill:{color:BLUE} });
s14.addShape(pres.shapes.RECTANGLE, { x:0, y:2.6, w:10, h:0.04, fill:{color:BLUE} });

logo(s14, 0.45, 0.32, 2.6, 0.72);

s14.addText("Start Your Journey to\nAutonomous Operations", {
  x:0.35, y:1.05, w:9.3, h:1.35, fontSize:30, bold:true, color:WHITE, align:"center", margin:0
});

s14.addText("Begin with governed autonomy — safe, audited, and policy-controlled.\nEvery incident AIR resolves builds the confidence for the next one to resolve itself.", {
  x:1.0, y:2.48, w:8.0, h:0.72, fontSize:12, color:MUTED, align:"center", margin:0
});

var ctas = [
  { x:0.55,  lbl:"Book a Live Demo",   sub:"See autonomous remediation live" },
  { x:3.8,   lbl:"Start Free Trial",   sub:"Full platform, your environment" },
  { x:7.05,  lbl:"Talk to Sales",      sub:"Custom enterprise deployment" }
];
ctas.forEach(function(c) {
  s14.addShape(pres.shapes.RECTANGLE, { x:c.x, y:3.3, w:2.5, h:1.2, fill:{color:BLUE}, shadow:sh() });
  s14.addText(c.lbl, { x:c.x, y:3.4, w:2.5, h:0.5, fontSize:12, bold:true, color:WHITE, align:"center", margin:0 });
  s14.addText(c.sub, { x:c.x+0.1, y:3.88, w:2.3, h:0.4, fontSize:9, color:BFDBFE, align:"center", margin:0 });
});

s14.addText("axiometica.ai", {
  x:0, y:4.82, w:10, h:0.38, fontSize:11, color:MUTED, align:"center", margin:0
});

// ─── Write ─────────────────────────────────────────────────────────────────
var OUT = "C:/Users/mikeb/OneDrive/Desktop/Axiometica_AIR_Sales_Pitch.pptx";
pres.writeFile({ fileName: OUT })
  .then(function() { console.log("SUCCESS: " + OUT); })
  .catch(function(err) { console.error("ERROR:", err.message || err); process.exit(1); });
