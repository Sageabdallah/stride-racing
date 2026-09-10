const fs = require('fs');
const H = require('./build_part1.js');
const { d, facts, plain, GREY, INK, ACCENT, pct,
        P, Rich, H1, H2, Bullet, Num, table, Caption, Callout, Spacer } = H;
const { Paragraph, TextRun, PageBreak, AlignmentType: AT } = d;
const BR = () => new Paragraph({ children: [new PageBreak()] });
const zas = JSON.parse(fs.readFileSync('zas.json'));
const A = facts.artifact;

const s10 = [
  H1('10. How the crowd and the money move a score'),
  P('Once a runner has a rating, the two outside opinions can push it up or down. Neither can create a bet on its own, but both can turn a marginal one into a real one, or kill it.'),
  H2('What the tipsters do to a score'),
  P('The tipster panel is scored out of 100 for each runner. That score converts into points added to or taken off the rating.'),
  Spacer(80),
  table([2700, 1800, 4526],
    ['Tipster score', 'Base points', 'What it means'],
    [
      ['80 or above', '+12.0', 'Strong agreement across the panel'],
      ['65 to 79',    '+7.0',  'Clear support'],
      ['50 to 64',    '+3.0',  'Mild support'],
      ['35 to 49',    '0',     'No meaningful signal either way'],
      ['20 to 34',    '−3.0', 'The panel is actively avoiding it'],
      ['Below 20',    '−8.0', 'The panel is against it'],
    ],
    { align: [undefined, AT.CENTER, undefined], boldFirstCol: true }),
  Spacer(140),
  P('Those base points are then adjusted three times before they are applied, because who is saying something matters as much as what is being said.'),
  Bullet('Spread across the panel. A tip supported by several different kinds of source counts for more than the same number of mentions from one corner. This can halve the points or raise them by half again.'),
  Bullet('Independence. Sources that are genuinely independent count for more than commercial ones. A panel that is mostly commercial can cut the points to about two thirds.'),
  Bullet('Reasoning. If the reasons the tipsters give line up with what the model saw, the points go up by a fifth. If they directly contradict it, the points are halved.'),
  P('After all of that, the result is capped. The most the tipsters can add is 12 points, and the most they can take away is 8.'),
  BR(),
  H2('What the money does to a score'),
  P('The second outside opinion is the market itself, watched over time rather than at a single moment. A horse being backed is telling you something; a horse drifting is telling you something too.'),
  Spacer(80),
  table([3000, 1800, 4226],
    ['Movement', 'Points', 'What it means'],
    [
      ['Steam',        '+8.0', 'Heavy, fast support. Serious money has arrived.'],
      ['Firming',      '+4.0', 'Steadily shortening in price.'],
      ['Stable',       '0',    'No meaningful movement, or no data.'],
      ['Drift',        '−3.0', 'Easing in the market. Support is not there.'],
      ['Strong drift', '−5.0', 'Drifting badly. Often the stable knows something.'],
    ],
    { align: [undefined, AT.CENTER, undefined], boldFirstCol: true }),
  BR(),
];

const s11 = [
  H1('11. Confidence, and how much goes on'),
  P('Every runner that survives to this point is graded into one of three confidence levels.'),
  Spacer(80),
  table([2000, 4600, 2426],
    ['Grade', 'Requirement', 'Stake'],
    [
      ['High',   'Positive expected value, and an edge of more than one percentage point.', '1 unit'],
      ['Medium', 'Positive expected value, but a smaller edge.', '1 unit'],
      ['Low',    'No edge, or negative expected value.', 'Nothing'],
    ],
    { align: [undefined, undefined, AT.CENTER], boldFirstCol: true }),
  Spacer(140),
  P('There is also a hard block: any horse longer than $30 is automatically graded low and never backed, whatever else the system thinks of it. At those prices the system\'s probability estimates are known to be unreliable, so it does not pretend otherwise.'),
  Spacer(60),
  Callout('Why high and medium stake the same amount',
    'They did not always. The system used to stake two units on high confidence and one on medium. That ladder has been switched off and everything now goes on flat, at one unit, until the system has proven a positive return with enough certainty to justify betting more on its best ideas. The note in the code describes this as a risk cut rather than an experiment. It is the cautious choice, and it means a run of bad luck on the strongest picks cannot do outsized damage.'),
  BR(),
];

const zasTop = zas.rows.filter(r => r[1] > 0).slice(0, 6)
  .map(([k, v]) => [plain[k], pct(v)]);

const s12 = [
  H1('12. What this document cannot tell you'),
  P('Four things belong here, because a description of a system that leaves out its weak points is not a description worth having.'),
  H2('A quarter of the model is not reaching the racetrack'),
  Rich([
    { text: 'An audit run on ' }, { text: zas.audit_date },
    { text: ' found that ' }, { text: `${zas.count} of the factors`, bold: true },
    { text: ' described in this document are calculated when the model is being trained, but are not calculated on race day. Between them they account for ' },
    { text: pct(zas.sum), bold: true },
    { text: ' of the model’s weight.' },
  ]),
  P('When race day comes and those numbers are missing, the system fills them in with zero. That is worse than leaving them blank, because for several of them zero is a real and meaningful value rather than a marker for "unknown". A horse with no sectional timing history is handed a figure that reads as exactly average. The model was taught these things matter, and is then quietly told something false about them, every race, every day.'),
  Spacer(60),
  table([6600, 2426],
    ['The heaviest of the affected factors', 'Weight'],
    zasTop,
    { align: [undefined, AT.RIGHT] }),
  Spacer(120),
  P('The fix does not require retraining anything. The information is already available at race time, and in several cases the system already looks it up for display purposes, just too late in the process to be used. The work has been written and sits behind a switch that is currently off while its effect is measured. It is a known problem with a known remedy, not a hidden one.'),
  H2('These weights describe one particular model'),
  P(`Everything in sections 2, 3 and 4 describes the model trained on ${A.trained_at.slice(0, 10)}. Retraining produces different weights. The ordering tends to be stable, and the market price has been the dominant factor throughout, but the exact figures should be read as a snapshot rather than a permanent property of the system.`),
  H2('Weight is not proof'),
  P('It is tempting to read the table in section 4 as a ranking of what wins races. It is not. It measures how heavily the model leaned on each piece of information, which is a different claim.'),
  P('The system\'s own research makes the point better than an explanation can. In an experiment in July, three factors derived from the betting price ranked third, sixth and eleventh out of 113 by weight. When they were removed altogether and the model retrained, its accuracy changed by an amount too small to matter. They looked important, and they were doing almost nothing, because they were restating a price the model could already see.'),
  H2('There are no profit figures in this document'),
  P('That is not an oversight. The trained model file and the recorded performance baselines are deliberately kept out of the code repository this document was written from, and the file that would hold backtest results records the baseline as not yet measured.'),
  P('Anything quoted here as a return, a strike rate or a profit figure would have been invented, so nothing is. What the system can honestly tell you today is how it makes its decisions, which is what this document sets out. What those decisions are worth is a separate question, and answering it needs the measurement work the system has not finished.'),
  BR(),
];

const appendix = [
  H1('Appendix. Where each number came from'),
  P('Every figure in this document was read out of the system\'s own code and records rather than estimated. If you want to check one, this is where it lives.'),
  Spacer(80),
  table([4200, 4826],
    ['What', 'Source'],
    [
      ['Factor weights, and the count of factors used and ignored', 'docs/research/feature_liveness_report.json, read from the trained model file'],
      ['Which factors are missing on race day, and what they are worth', 'The same file, cross-checked against docs/research/FEATURE_PROVENANCE.md'],
      ['The share each of the three models gets', 'server/python/ml_model.py, RacingMLModel'],
      ['Race type by distance', 'server/python/ml_model.py'],
      ['The two blends, and the price ladder', 'server/python/run_tips_pipeline.py'],
      ['Fair price after removing the margin', 'server/python/market_prob.py'],
      ['Expected value and confidence grades', 'server/python/run_tips_pipeline.py'],
      ['Stakes', 'server/python/run_tips_pipeline.py'],
      ['The three opinions, their weights and their bars', 'server/python/consensus_blender.py'],
      ['The five verdicts', 'server/python/consensus_blender.py'],
      ['Tipster and market point adjustments', 'server/python/consensus_blender.py'],
      ['The experiment on weight versus real effect', 'docs/analysis/ACADEMIC_FINDINGS.md, recording a run on 2026-07-13'],
      ['The absence of a measured baseline', 'docs/analysis/RESULTS.md'],
    ],
    { align: [undefined, undefined] }),
  Spacer(200),
  P('The percentages were not copied by hand. They were extracted straight from the system\'s records by script, grouped into themes by a second script that checks every factor is counted once and only once, and the theme totals were confirmed to add to 100%.',
    { italics: true, color: GREY }),
];
module.exports = { s10, s11, s12, appendix };
