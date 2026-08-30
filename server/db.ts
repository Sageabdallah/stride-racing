import "dotenv/config";
import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import dns from "dns";
import * as schema from "@shared/schema";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL must be set");
}

const dbUrl = new URL(process.env.DATABASE_URL);
const dbHostname = dbUrl.hostname;

type PoolConfigWithLookup = pg.PoolConfig & {
  lookup: (
    hostname: string,
    options: dns.LookupOneOptions,
    callback: (error: NodeJS.ErrnoException | null, address: string, family: number) => void,
  ) => void;
};

const poolConfig: PoolConfigWithLookup = {
  host: dbHostname,
  port: parseInt(dbUrl.port || "5432"),
  database: dbUrl.pathname.slice(1),
  user: decodeURIComponent(dbUrl.username),
  password: decodeURIComponent(dbUrl.password),
  ssl: {
    rejectUnauthorized: false,
    servername: dbHostname,
  },
  connectionTimeoutMillis: 15000,
  // Force IPv4 lookup on Windows without using top-level await.
  lookup(hostname, options, callback) {
    return dns.lookup(
      hostname,
      { ...options, family: 4, all: false },
      (error, address, family) => {
        if (!error && address) {
          console.log(`[db] Resolved ${hostname} -> ${address}`);
        }
        callback(error, address, family);
      },
    );
  },
};

const pool = new pg.Pool(poolConfig);
pool.on("error", (error) => {
  console.error("[db] Pool error:", error.message);
});

export const db = drizzle(pool, { schema });
