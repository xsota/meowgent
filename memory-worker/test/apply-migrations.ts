import { applyD1Migrations } from "cloudflare:test";
import { env } from "cloudflare:workers";

await applyD1Migrations(env.MEMORY_DB, env.TEST_MIGRATIONS);
