import { sql } from "drizzle-orm";
import { db } from "./db";

let initPromise: Promise<void> | null = null;

async function ensureStrideTipResultsTable() {
  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS stride_tip_results (
      id serial PRIMARY KEY,
      race_date text,
      tip_date text,
      track text NOT NULL,
      race_number integer NOT NULL,
      race_id text,
      horse_name text,
      tipped_horse_id text,
      tipped_horse_name text,
      actual_winner_id text,
      actual_winner_name text,
      result text NOT NULL,
      tip_type text,
      tip_rank integer,
      tipped_odds numeric(8,2),
      tipped_win_pct numeric(6,2),
      tipped_edge_pct numeric(6,2),
      confidence text,
      actual_position integer,
      field_size integer,
      api_sp numeric(8,2),
      profit_loss numeric(10,2),
      distance text,
      race_class text,
      going text,
      tipped_horse_sp real,
      winner_sp real,
      edge_pct real,
      created_at timestamp DEFAULT now(),
      collected_at timestamptz DEFAULT now()
    )
  `);

  const alterStatements = [
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_date text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_date text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_id text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS horse_name text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_id text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_name text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_winner_id text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_winner_name text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_type text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_rank integer`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_odds numeric(8,2)`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_win_pct numeric(6,2)`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_edge_pct numeric(6,2)`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS confidence text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_position integer`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS field_size integer`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS api_sp numeric(8,2)`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS profit_loss numeric(10,2)`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS distance text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_class text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS going text`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_sp real`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS winner_sp real`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS edge_pct real`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS created_at timestamp DEFAULT now()`,
    `ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS collected_at timestamptz DEFAULT now()`,
  ];

  for (const statement of alterStatements) {
    await db.execute(sql.raw(statement));
  }

  await db.execute(sql`
    UPDATE stride_tip_results
    SET race_date = COALESCE(race_date, tip_date),
        tip_date = COALESCE(tip_date, race_date),
        horse_name = COALESCE(horse_name, tipped_horse_name),
        tipped_horse_name = COALESCE(tipped_horse_name, horse_name),
        api_sp = COALESCE(api_sp, tipped_horse_sp),
        tipped_horse_sp = COALESCE(tipped_horse_sp, api_sp::real),
        tipped_edge_pct = COALESCE(tipped_edge_pct, edge_pct::numeric(6,2)),
        edge_pct = COALESCE(edge_pct, tipped_edge_pct::real),
        collected_at = COALESCE(collected_at, created_at, now())
    WHERE race_date IS NULL
       OR tip_date IS NULL
       OR horse_name IS NULL
       OR tipped_horse_name IS NULL
       OR api_sp IS NULL
       OR tipped_horse_sp IS NULL
       OR tipped_edge_pct IS NULL
       OR edge_pct IS NULL
       OR collected_at IS NULL
  `);

  await db.execute(sql`
    ALTER TABLE stride_tip_results
    DROP CONSTRAINT IF EXISTS stride_tip_results_result_check
  `);

  await db.execute(sql`
    UPDATE stride_tip_results
    SET result = 'LOSS'
    WHERE result NOT IN ('WIN', 'PLACE', 'LOSS', 'SCRATCHED')
  `);

  await db.execute(sql`
    ALTER TABLE stride_tip_results
    ADD CONSTRAINT stride_tip_results_result_check
    CHECK (result IN ('WIN', 'PLACE', 'LOSS', 'SCRATCHED'))
  `);

  await db.execute(sql`
    CREATE INDEX IF NOT EXISTS stride_tip_results_tip_date_track_race_idx
    ON stride_tip_results (tip_date, track, race_number)
  `);

  await db.execute(sql`
    CREATE INDEX IF NOT EXISTS stride_tip_results_race_id_idx
    ON stride_tip_results (race_id)
  `);

  await db.execute(sql`
    CREATE INDEX IF NOT EXISTS idx_stride_tip_results_date
    ON stride_tip_results (race_date)
  `);

  await db.execute(sql`
    CREATE INDEX IF NOT EXISTS idx_stride_tip_results_result
    ON stride_tip_results (result)
  `);

  await db.execute(sql`
    CREATE UNIQUE INDEX IF NOT EXISTS stride_tip_results_race_horse_tip_idx
    ON stride_tip_results (race_date, track, race_number, horse_name, tip_type)
  `);
}

export async function initializeStrideTipResults() {
  if (!initPromise) {
    initPromise = ensureStrideTipResultsTable().catch((error) => {
      initPromise = null;
      throw error;
    });
  }

  await initPromise;
}
