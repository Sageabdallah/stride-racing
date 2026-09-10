import json, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

facts = json.load(open('facts.json'))
groups = json.load(open('groups.json'))
imp = dict(facts['ranked_nonzero'])

INK, ACCENT, MUTED = "#1a1a1a", "#1f4e79", "#9aa5b1"

PLAIN = {
 "market_odds":"The betting market's price",
 "closer_advantage":"Race shape favours closers",
 "barrier_draw":"Barrier (gate) number",
 "weighted_form_score":"Recent form score",
 "trainer_trial_pattern":"Trainer's record after a trial",
 "field_size":"Number of runners",
 "pace_pressure_score":"How much early speed in the race",
 "field_size_context":"Field size, scaled",
 "td_upset_rate":"How often this track/trip upsets",
 "leader_advantage":"Race shape favours front-runners",
 "days_since_run":"Days since last start",
 "td_pace_bias":"Pace bias of this track/trip",
 "z_800m":"Speed at the 800m mark",
 "weight_kg":"Weight carried",
 "trial_recency":"Days since last barrier trial",
}

def barh(labels, values, title, path, colors=None, xmax=None):
    h = max(2.6, 0.42 * len(labels) + 1.1)
    fig, ax = plt.subplots(figsize=(9.2, h), dpi=200)
    y = range(len(labels))[::-1]
    bars = ax.barh(list(y), values, color=colors or ACCENT, height=0.68)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, fontsize=10.5, color=INK)
    top = xmax or max(values) * 1.20
    ax.set_xlim(0, top)
    for b, v in zip(bars, values):
        ax.text(b.get_width() + top*0.012, b.get_y() + b.get_height()/2,
                f"{v:.1f}%", va="center", fontsize=10, color=INK, fontweight="600")
    ax.set_title(title, fontsize=12.5, color=INK, pad=12, loc="left", fontweight="600")
    ax.xaxis.set_visible(False)
    for s in ("top","right","bottom","left"): ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight", facecolor="white"); plt.close(fig)

# Chart 1 — themes
gs = groups['group_shares']
labels = list(gs.keys()); vals = [gs[k]*100 for k in labels]
barh(labels, vals, "Share of the model's decision, by theme", "chart_themes.png")

# Chart 2 — top 15 individual factors
top = facts['ranked_nonzero'][:15]
l2 = [PLAIN[k] for k, _ in top]; v2 = [v*100 for _, v in top]
cols = [ACCENT] + [MUTED]*14
barh(l2, v2, "The fifteen factors that carry the most weight", "chart_top15.png", colors=cols)
print("charts written")
for k,_ in top:
    assert k in PLAIN, k
print("all top-15 labels verified present")
