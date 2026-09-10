const fs = require('fs');
const d = require('docx');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType,
  ImageRun, PageBreak, LevelFormat, convertInchesToTwip, Footer, PageNumber,
} = d;

const facts  = JSON.parse(fs.readFileSync('facts.json'));
const groups = JSON.parse(fs.readFileSync('groups.json'));
const plain  = JSON.parse(fs.readFileSync('plain.json'));

const W = 9026;                      // A4 content width in DXA at 1" margins
const INK = '1A1A1A', ACCENT = '1F4E79', GREY = '5A6673', RULE = 'D5DBE1', BAND = 'F2F5F8';
const pct = v => (v * 100).toFixed(2) + '%';

const P = (text, o = {}) => new Paragraph({
  spacing: { after: o.after ?? 140, line: o.line ?? 300 },
  alignment: o.align,
  indent: o.indent,
  children: [new TextRun({ text, size: o.size ?? 21, color: o.color ?? INK,
                           bold: o.bold, italics: o.italics, font: 'Calibri' })],
});

const Rich = (runs, o = {}) => new Paragraph({
  spacing: { after: o.after ?? 140, line: 300 },
  children: runs.map(r => new TextRun({ ...r, size: r.size ?? 21, font: 'Calibri',
                                        color: r.color ?? INK })),
});

const H1 = text => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 340, after: 180 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 6 } },
  children: [new TextRun({ text, size: 30, bold: true, color: ACCENT, font: 'Calibri' })],
});

const H2 = text => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 260, after: 120 },
  children: [new TextRun({ text, size: 24, bold: true, color: INK, font: 'Calibri' })],
});

const Bullet = (text, level = 0) => new Paragraph({
  numbering: { reference: 'dot', level },
  spacing: { after: 90, line: 300 },
  children: [new TextRun({ text, size: 21, color: INK, font: 'Calibri' })],
});

const Num = text => new Paragraph({
  numbering: { reference: 'steps', level: 0 },
  spacing: { after: 100, line: 300 },
  children: [new TextRun({ text, size: 21, color: INK, font: 'Calibri' })],
});

const cell = (text, w, o = {}) => new TableCell({
  width: { size: w, type: WidthType.DXA },
  margins: { top: 70, bottom: 70, left: 110, right: 110 },
  shading: o.shade ? { type: ShadingType.CLEAR, fill: o.shade, color: 'auto' } : undefined,
  verticalAlign: 'center',
  children: [new Paragraph({
    alignment: o.align,
    spacing: { after: 0, line: 260 },
    children: [new TextRun({ text: String(text), size: o.size ?? 19, bold: o.bold,
                             color: o.color ?? INK, font: 'Calibri' })],
  })],
});

const table = (widths, header, rows, opts = {}) => new Table({
  columnWidths: widths,
  width: { size: W, type: WidthType.DXA },
  borders: {
    top:    { style: BorderStyle.SINGLE, size: 4, color: RULE },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: RULE },
    left:   { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' },
    right:  { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' },
    insideHorizontal: { style: BorderStyle.SINGLE, size: 2, color: RULE },
    insideVertical:   { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' },
  },
  rows: [
    new TableRow({
      tableHeader: true,
      cantSplit: true,
      children: header.map((h, i) => cell(h, widths[i], {
        bold: true, shade: ACCENT, color: 'FFFFFF', size: 19,
        align: opts.align && opts.align[i],
      })),
    }),
    ...rows.map((r, ri) => new TableRow({
      cantSplit: true,
      children: r.map((c, i) => cell(c, widths[i], {
        align: opts.align && opts.align[i],
        shade: ri % 2 === 1 ? BAND : undefined,
        bold: opts.boldFirstCol && i === 0,
      })),
    })),
  ],
});

const png = p => {
  const b = fs.readFileSync(p);
  return { data: b, w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
};
const image = (path, maxW = 600) => {
  const im = png(path);
  const w = Math.min(maxW, im.w), h = Math.round(im.h * (w / im.w));
  return new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { before: 140, after: 160 },
    children: [new ImageRun({ type: 'png', data: im.data, transformation: { width: w, height: h } })],
  });
};

const Caption = text => new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { after: 240 },
  children: [new TextRun({ text, size: 17, italics: true, color: GREY, font: 'Calibri' })],
});

const Callout = (title, body) => new Table({
  columnWidths: [W], width: { size: W, type: WidthType.DXA },
  borders: {
    top: { style: BorderStyle.SINGLE, size: 4, color: RULE },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: RULE },
    left: { style: BorderStyle.SINGLE, size: 18, color: ACCENT },
    right: { style: BorderStyle.SINGLE, size: 4, color: RULE },
    insideHorizontal: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' },
    insideVertical: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' },
  },
  rows: [new TableRow({ children: [new TableCell({
    width: { size: W, type: WidthType.DXA },
    shading: { type: ShadingType.CLEAR, fill: BAND, color: 'auto' },
    margins: { top: 150, bottom: 150, left: 200, right: 200 },
    children: [
      new Paragraph({ spacing: { after: 70 },
        children: [new TextRun({ text: title, bold: true, size: 21, color: ACCENT, font: 'Calibri' })] }),
      new Paragraph({ spacing: { after: 0, line: 300 },
        children: [new TextRun({ text: body, size: 20, color: INK, font: 'Calibri' })] }),
    ],
  })] })],
});

const Spacer = (h = 120) => new Paragraph({ spacing: { after: h }, children: [] });

module.exports = { d, facts, groups, plain, W, INK, ACCENT, GREY, RULE, BAND,
                   pct, P, Rich, H1, H2, Bullet, Num, cell, table, image,
                   Caption, Callout, Spacer };
