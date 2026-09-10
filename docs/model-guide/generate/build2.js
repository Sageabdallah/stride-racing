const H = require('./build_part1.js');
const { d, facts, groups, plain, W, INK, ACCENT, GREY, pct,
        P, Rich, H1, H2, Bullet, Num, table, image, Caption, Callout, Spacer } = H;
const { Paragraph, TextRun, PageBreak, AlignmentType: AT } = d;
const BR = () => new Paragraph({ children: [new PageBreak()] });
const bw = facts.blend_weights, cv = facts.convergence, pw = facts.pillar_weight_defaults;
const p1 = v => (v * 100).toFixed(1) + '%';

const s5 = [
  H1('5. Three models, not one'),
  P('STRIDE does not run a single prediction program. It runs three, side by side. Each one learns the same task in a slightly different way, and each is given the same information about every runner.'),
  P('The three are called XGBoost, LightGBM and CatBoost. The names are not important. What matters is that three imperfect opinions, averaged, are steadier than any one of them alone. When all three agree, that is worth something. When they disagree, the average pulls the answer back toward the middle rather than letting one program have its way.'),
  P('How much say each one gets depends on how far the race is run over.'),
  Spacer(80),
  table([2300, 2200, 1509, 1509, 1508],
    ['Race type', 'Distance', 'XGBoost', 'LightGBM', 'CatBoost'],
    [
      ['Sprint',  'Under 1200m',    p1(bw.sprint.xgb),  p1(bw.sprint.lgb),  p1(bw.sprint.cat)],
      ['Mile',    '1200m to 1600m', p1(bw.mile.xgb),    p1(bw.mile.lgb),    p1(bw.mile.cat)],
      ['Staying', 'Over 1600m',     p1(bw.staying.xgb), p1(bw.staying.lgb), p1(bw.staying.cat)],
    ],
    { align: [undefined, undefined, AT.RIGHT, AT.RIGHT, AT.RIGHT], boldFirstCol: true }),
  Caption('Each row adds to 100%. The differences between the three are small.'),
  Spacer(120),
  Callout('Worth knowing about these three shares',
    'They are not a running tally of which program has been getting it right lately. They are fixed starting figures written into the system when it was built. The system does contain a function meant to update them from actual race results, but nothing in the code ever calls that function, so the shares have never moved. Whether that matters is a fair question to ask, though the practical effect is small: three programs given identical information tend to produce very similar answers, so almost any reasonable split lands in much the same place.'),
  BR(),
];

const s6 = [
  H1('6. Where the model meets the market'),
  P('Two blends happen back to back. Both are ways of stopping any single method from having too much say.'),
  H2('Blend one: the simulation and the models'),
  P('The system runs a simulation of the race that produces one estimate of each horse\'s chance. The three prediction models produce a second. Those two get combined.'),
  P('How they combine depends on the price. For a horse at $3 or shorter, the prediction models get 20% of the say and the simulation keeps 80%. For everything longer, the models get 40%.'),
  H2('Blend two: the result meets the market'),
  P('That combined estimate is then anchored against the market\'s own view. This is the step that stops the system talking itself into a horse the money has already dismissed.'),
  P('The share the model keeps depends on the price, and it moves the way most people would not expect: the model is trusted most at short prices and least at long ones.'),
  Spacer(80),
  table([3010, 3008, 3008],
    ['Price of the horse', 'Model\'s share of the say', 'Market\'s share'],
    [
      ['$3.00 or shorter',  '80%', '20%'],
      ['$3.01 to $6.00',    '70%', '30%'],
      ['$6.01 to $10.00',   '50%', '50%'],
      ['$10.01 to $15.00',  '45%', '55%'],
      ['$15.01 to $30.00',  '40%', '60%'],
      ['Longer than $30',   '30%', '70%'],
    ],
    { align: [undefined, AT.CENTER, AT.CENTER], boldFirstCol: true }),
  Spacer(140),
  P('The reason for that shape is recorded in the system itself. A review of short-priced runners found that horses in the $1 to $3 range were winning about 41% of the time, while the system was rating them at roughly 17% once the blending was done. It was being far too pessimistic about favourites. Giving the model more of the say at the short end lets its real opinion reach the final number instead of being averaged away.'),
  P('At the other end, above $30, calibration is known to be unreliable, so the market is given most of the say and, as section 11 explains, those runners are blocked from being bet at all.'),
  BR(),
];

const s7 = [
  H1('7. Edge, and what "expected value" means'),
  P('A price of $5.00 looks like it implies a 20% chance, because one divided by five is a fifth. It does not, quite. Bookmakers build in a margin, so if you add up the implied chances of every runner in a race they come to more than 100%. A typical book might total 118%.'),
  P('Before the system can compare its own view against the market\'s, it has to strip that margin out. It does this by dividing each horse\'s raw implied chance by the book total.'),
  Spacer(60),
  Callout('A worked example',
    'A horse is $5.00 and the book totals 118%. Raw implied chance is 100 divided by 5, or 20%. Strip the margin and the fair chance is 20 divided by 1.18, which is 16.9%. If the system estimates the horse at 21%, the edge is 21 minus 16.9, or 4.1 percentage points in the system\'s favour.'),
  Spacer(200),
  H2('Expected value'),
  P('Expected value is the natural next question: for every dollar staked, how much would you expect to get back over the long run? A positive number means the bet pays in the long run, a negative number means it does not.'),
  P('The system can measure it two ways. By default it measures against the fair chance, with the bookmaker\'s margin already removed. There is a switch, currently off, that measures against the actual price you would be paid at.'),
  P('There is an honest wrinkle here, and it is documented inside the system rather than hidden. In the default setting, the expected-value test turns out to be mathematically the same test as the edge test. It cannot fail unless the edge test has already failed, so it adds nothing. The alternative setting is the one that can see the margin being charged, and the reason it has not been switched on yet is that doing so would change which bets qualify, and that change is being evaluated first.'),
  BR(),
];

const s8 = [
  H1('8. Three opinions have to agree'),
  P('A good rating is not enough on its own. Before anything is backed, three separate opinions are collected and weighed against each other.'),
  Spacer(80),
  table([3400, 1800, 3826],
    ['Opinion', 'Weight', 'What it is'],
    [
      ['The STRIDE model', p1(parseFloat(pw.stride)), 'Everything described in sections 2 to 7.'],
      ['Tipster consensus', p1(parseFloat(pw.consensus)), 'What a vetted panel of independent racing sources is saying about the race.'],
      ['Market signal', p1(parseFloat(pw.market)), 'Which way the money has been moving on the horse since the market opened.'],
    ],
    { align: [undefined, AT.CENTER, undefined], boldFirstCol: true }),
  Caption('These three weights are settings, not fixed rules. They can be changed without touching the model.'),
  Spacer(160),
  P('Each opinion is scored out of 100, and each has a bar it has to clear to count as supportive.'),
  Spacer(80),
  table([4600, 2200, 2226],
    ['Opinion', 'Bar to clear', 'Adjustable?'],
    [
      ['The STRIDE model', String(cv.STRIDE_THRESHOLD), 'Fixed in the code'],
      ['Tipster consensus', '65', 'Yes, by setting'],
      ['Market signal', '60', 'Yes, by setting'],
    ],
    { align: [undefined, AT.CENTER, undefined], boldFirstCol: true }),
  BR(),
];

const s9 = [
  H1('9. The five verdicts'),
  P('Which of the three opinions clear their bar decides the verdict. There are five, and only two of them ever result in a bet.'),
  Spacer(80),
  table([2560, 4100, 2366],
    ['Verdict', 'What it means', 'Bet placed?'],
    [
      ['LOCK', 'All three agree. The model likes it, the tipsters like it, and the money is coming for it.', 'Yes'],
      ['CONFIRM', 'The model likes it, and either the tipsters or the money back that up.', 'Yes, if the combined score reaches ' + cv.MIN_CONFIRM_CONVERGENCE_SCORE],
      ['FLAG', 'The model likes it and nothing else does.', 'No'],
      ['CROWD_OVERRIDE', 'The tipsters and the money both like it, but the model does not.', 'No, but it is recorded'],
      ['SKIP', 'Not enough agreement to act on.', 'No'],
    ],
    { align: [undefined, undefined, undefined], boldFirstCol: true }),
  Spacer(160),
  H2('Why a horse the model likes can still be refused'),
  P('The FLAG verdict is the one that needs explaining, because it looks like the system throwing away its own best work.'),
  P('It is deliberate. When the model is keen on a horse and no independent tipster and no movement in the money agrees, that silence is treated as information in its own right, not as an absence of information. It usually means the model has latched onto something the rest of the world can see is not there. The system\'s own note on this reads: absence of external confirmation is itself a signal.'),
  P(`There is one more floor underneath all of this. Whatever the verdict, a runner whose raw model score is below ${cv.MIN_MODEL_SCORE_FOR_BET} is never backed. That catches the case where the crowd and the money are enthusiastic about a horse the model genuinely cannot make a case for.`),
  Spacer(60),
  Callout('Why CROWD_OVERRIDE is tracked but never backed',
    'When the crowd and the money are both keen and the model is not, the system records what would have happened without risking anything on it. If those runners turn out to win at a decent rate over time, that is evidence the model is missing something and needs work. It is a way of learning from bets it did not take.'),
  BR(),
];
module.exports = { s5, s6, s7, s8, s9 };
