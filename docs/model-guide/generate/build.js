const fs = require('fs');
const H = require('./build_part1.js');
const { d, facts, groups, plain, W, INK, ACCENT, GREY, RULE, BAND,
        pct, P, Rich, H1, H2, Bullet, Num, table, image, Caption, Callout, Spacer } = H;
const { Document, Packer, Paragraph, TextRun, AlignmentType, BorderStyle,
        PageBreak, LevelFormat, Footer, PageNumber, AlignmentType: AT } = d;

const A = facts.artifact;
const gs = groups.group_shares;
const ranked = facts.ranked_nonzero;
const BR = () => new Paragraph({ children: [new PageBreak()] });

// ---------------------------------------------------------------- cover ----
const cover = [
  Spacer(2200),
  new Paragraph({ alignment: AT.LEFT, spacing: { after: 60 },
    children: [new TextRun({ text: 'STRIDE', size: 72, bold: true, color: ACCENT, font: 'Calibri' })] }),
  new Paragraph({ spacing: { after: 320 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: ACCENT, space: 10 } },
    children: [new TextRun({ text: 'How the model makes up its mind', size: 34, color: INK, font: 'Calibri' })] }),
  P('A plain-English guide to what the system weighs when it rates a horse, and how a rating turns into a bet.',
    { size: 24, color: GREY, after: 700 }),
  P('Prepared 10 September 2026', { size: 20, color: GREY, after: 60 }),
  P(`Describes model version ${A.version}, trained ${A.trained_at.slice(0, 10)}`, { size: 20, color: GREY, after: 60 }),
  P('No prior knowledge of racing analytics or statistics is assumed.', { size: 20, color: GREY, italics: true }),
  BR(),
];

// ------------------------------------------------------------- section 1 ---
const s1 = [
  H1('1. What the system is trying to do'),
  P('STRIDE rates every runner in Australian thoroughbred racing. It is not trying to pick winners. It is trying to find horses whose real chance of winning is better than the price being offered about them.'),
  P('Those are different jobs. A horse can be the most likely winner in its race and still be a poor bet, because the price is too short to be worth taking. An outsider at long odds can be a good bet, if its true chance is better than the price implies.'),
  P('So the question the system asks is not "which horse wins?" It is "where is the price wrong?"'),
  Spacer(60),
  Callout('The one idea everything rests on',
    'If the system believes a horse has a 25% chance, and the price on offer implies only 18%, that seven-point gap is the whole business. STRIDE calls it the edge. Where there is no gap, there is no bet, no matter how good the horse looks.'),
  Spacer(160),
  H2('The nine steps, start to finish'),
  Num('Gather the form for every runner in the race.'),
  Num('Simulate the race many times over to get a first estimate of each horse\'s chance.'),
  Num('Ask three separate prediction models the same question, and average their answers into a second estimate.'),
  Num('Blend those two estimates into one figure.'),
  Num('Anchor that figure against the market price, so the model is never allowed to drift too far from what the money says.'),
  Num('Work out the edge: the system\'s estimate minus the market\'s own implied chance.'),
  Num('Score and rank every runner in the race.'),
  Num('Check two outside opinions: what independent tipsters are saying, and which way the money is moving.'),
  Num('Decide. Most runners get no bet at all.'),
  Spacer(120),
  P('Sections 3 and 4 cover what goes into steps 1 to 3. Sections 5 to 7 cover steps 4 to 6. Sections 8 to 11 cover steps 7 to 9.', { italics: true, color: GREY }),
  BR(),
];

// ------------------------------------------------------------- section 2 ---
const themeRows = Object.entries(gs).map(([g, v]) =>
  [g, pct(v), String(groups.groups[g].length)]);
themeRows.push(['Total', '100.00%', String(ranked.length)]);

const s2 = [
  H1('2. What the model actually looks at'),
  P(`When the model was trained it was shown ${A.n_trained_columns} separate pieces of information about each runner. It then worked out for itself which of them were worth paying attention to.`),
  Rich([
    { text: 'It ignored ' }, { text: `${A.n_zero_importance} of them entirely`, bold: true },
    { text: `. Those were given no weight at all. Only ` },
    { text: `${ranked.length} pieces of information`, bold: true },
    { text: ' carry any weight in the finished model.' },
  ]),
  P('Every percentage in this document is that weight: how much of the model\'s thinking each piece of information accounts for. The figures add up to 100%.'),
  Spacer(60),
  Callout('How to read these percentages',
    'A weight of 20% does not mean a factor is right 20% of the time, or that it wins you 20% more. It means the model leaned on that piece of information for about a fifth of the work it did in separating winners from losers. It is a measure of how much the model used something, not proof that the thing causes horses to win. Section 12 returns to this point, because it matters.'),
  BR(),
  H2('The ten themes'),
  P('The 56 factors that matter sort naturally into ten themes. Grouped that way, the shape of the model is easy to see.'),
  image('chart_themes.png', 590),
  Spacer(60),
  table([5000, 1600, 2426],
    ['Theme', 'Share of the model', 'Factors in it'],
    themeRows,
    { align: [undefined, AT.RIGHT, AT.RIGHT], boldFirstCol: false }),
  Caption('Shares are the combined weight of every factor in that theme.'),
  BR(),
];

// ------------------------------------------------------------- section 3 ---
const top = ranked.slice(0, 15);
const s3 = [
  H1('3. The single biggest factor is the price itself'),
  Rich([
    { text: 'The betting market\'s own price on a horse accounts for ' },
    { text: pct(ranked[0][1]), bold: true },
    { text: ' of the model, on its own. It is far and away the most powerful single thing the model sees. The next strongest factor is worth about a quarter as much.' },
  ]),
  P('That surprises people, so it is worth being clear about why it happens and why it is not a problem.'),
  P('The betting public, taken as a whole, prices horses very well. Thousands of people with money at stake, many of them professionals, have already done an enormous amount of work by the time a price settles. Any model that ignored that would be throwing away the best single piece of evidence available.'),
  P('So STRIDE starts from the market price and looks for the places the market has it wrong. The other 55 factors exist to find those places. This is the standard approach in serious racing analytics, and the system\'s own research notes cite the published literature behind it.'),
  Spacer(60),
  Callout('The catch, stated plainly',
    'Because the price is the strongest input, the model will usually agree with the market. That is expected. The value is in the minority of runners where it disagrees, and the rest of the system exists to work out which of those disagreements are worth acting on.'),
  BR(),
  H2('The fifteen heaviest factors'),
  image('chart_top15.png', 590),
  Rich([
    { text: 'These fifteen account for ' }, { text: pct(facts.top15_share), bold: true },
    { text: ' of the model between them. The top ten alone account for ' },
    { text: pct(facts.top10_share), bold: true },
    { text: '. The remaining 41 factors share what is left.' },
  ]),
  BR(),
];

// ------------------------------------------------------------- section 4 ---
const allRows = ranked.map(([k, v], i) => [String(i + 1), plain[k], pct(v), k]);
const s4 = [
  H1('4. Every factor that carries weight'),
  P('The complete list, heaviest first. The last column gives the name the factor goes by inside the system, which is useful if you are ever reading its output.'),
  Spacer(80),
  table([620, 4300, 1000, 3106],
    ['#', 'What it is', 'Weight', 'Name in the system'],
    allRows,
    { align: [AT.CENTER, undefined, AT.RIGHT, undefined] }),
  Spacer(120),
  P(`The ${A.n_zero_importance} factors not listed here were fed to the model during training and given a weight of exactly zero. Most of them were never actually filled in with real data, so the model had nothing to learn from. They are being cleaned out of the system.`,
    { italics: true, color: GREY }),
  BR(),
];
module.exports = { cover, s1, s2, s3, s4, BR };
