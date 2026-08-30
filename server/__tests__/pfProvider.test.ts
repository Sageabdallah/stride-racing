/**
 * pfProvider formatting — verified against the provider's golden racecard
 * fixture (server/python/providers/fixtures/racecard_golden_2026-08-01.json),
 * the same contract the pipeline's PF provider emits. No network, no spawn.
 */

import fs from "fs";
import path from "path";
import { describe, expect, it } from "vitest";
import { formatLiveRaceCard } from "../pfProvider";

const goldenPath = path.resolve(
  __dirname,
  "../python/providers/fixtures/racecard_golden_2026-08-01.json",
);

describe("formatLiveRaceCard", () => {
  const meets = JSON.parse(fs.readFileSync(goldenPath, "utf-8")) as Record<string, any>[];

  it("renders the golden meet with header, races and runner lines", () => {
    const card = formatLiveRaceCard(meets[0], "2026-08-01");
    expect(card).toContain(`LIVE RACE CARD — ${meets[0].course}, 2026-08-01`);
    expect(card).toContain("Data: Punting Form");
    for (const race of meets[0].races) {
      expect(card).toContain(`Race ${race.race_number}`);
      for (const runner of race.runners.filter((r: any) => !r.scratched)) {
        expect(card).toContain(runner.horse);
      }
    }
  });

  it("skips scratched runners and marks empty fields", () => {
    const meet = {
      course: "Testville",
      races: [
        { race_number: 1, race_name: "Empty", distance: "1200m", runners: [] },
        {
          race_number: 2,
          race_name: "One out",
          distance: "1400m",
          runners: [
            { horse: "Scratchy", scratched: true },
            { horse: "Runner Up", number: 4, draw: 2, jockey: "A Jockey", form: "1x2" },
          ],
        },
      ],
    };
    const card = formatLiveRaceCard(meet, "2026-08-01");
    expect(card).toContain("(no runners declared yet)");
    expect(card).not.toContain("Scratchy");
    expect(card).toContain("4. Runner Up B2 | A Jockey");
    expect(card).toContain("Form: 1x2");
  });
});
