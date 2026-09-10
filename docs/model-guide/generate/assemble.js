const fs = require('fs');
const d = require('docx');
const { Document, Packer, Paragraph, TextRun, AlignmentType, Footer,
        PageNumber, LevelFormat, convertInchesToTwip, BorderStyle } = d;
const A = require('./build.js');
const B = require('./build2.js');
const C = require('./build3.js');

const ACCENT = '1F4E79', GREY = '5A6673';

const doc = new Document({
  creator: 'STRIDE',
  title: 'STRIDE — How the model makes up its mind',
  description: 'A plain-English guide to the factor weights and decision logic of the STRIDE racing prediction system.',
  styles: {
    default: { document: { run: { font: 'Calibri', size: 21, color: '1A1A1A' } } },
  },
  numbering: {
    config: [
      { reference: 'dot', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.3), hanging: convertInchesToTwip(0.18) } } } }] },
      { reference: 'steps', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.35), hanging: convertInchesToTwip(0.23) } } } }] },
    ],
  },
  sections: [{
    properties: {
      page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } },
      titlePage: true,
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        border: { top: { style: BorderStyle.SINGLE, size: 4, color: 'D5DBE1', space: 8 } },
        children: [
          new TextRun({ text: 'STRIDE — How the model makes up its mind', size: 16, color: GREY, font: 'Calibri' }),
          new TextRun({ text: '          ', size: 16 }),
          new TextRun({ children: [PageNumber.CURRENT], size: 16, color: GREY, font: 'Calibri' }),
        ] })] }),
      first: new Footer({ children: [new Paragraph({ children: [] })] }),
    },
    children: [
      ...A.cover, ...A.s1, ...A.s2, ...A.s3, ...A.s4,
      ...B.s5, ...B.s6, ...B.s7, ...B.s8, ...B.s9,
      ...C.s10, ...C.s11, ...C.s12, ...C.appendix,
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(require('path').join(__dirname, '..', 'STRIDE-How-the-model-makes-up-its-mind.docx'), buf);
  console.log('written', buf.length, 'bytes');
});
