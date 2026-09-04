import type { D1Migration } from "@cloudflare/vitest-plugin";

declare global {
  namespace Cloudflare {
    interface Env {
      MEMORY_DB: D1Database;
      MEMORY_API_TOKEN: string;
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}

export {};
