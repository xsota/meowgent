import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import worker, { type Env } from "../src/index";

const testEnv = env as unknown as Env;

const authHeaders = {
  Authorization: "Bearer test-token",
  "Content-Type": "application/json",
};

async function invoke(path: string, init: RequestInit = {}, currentEnv: Env = testEnv): Promise<Response> {
  const context = createExecutionContext();
  const response = await worker.fetch(
    new Request(`https://memory.example${path}`, init),
    currentEnv,
    context,
  );
  await waitOnExecutionContext(context);
  return response;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(authHeaders);
  for (const [key, value] of new Headers(init.headers)) {
    headers.set(key, value);
  }

  return invoke(path, {
    ...init,
    headers,
  });
}

async function responseJson(response: Response): Promise<any> {
  return response.json();
}

describe("memory API", () => {
  beforeEach(async () => {
    await env.MEMORY_DB.prepare("DELETE FROM memory_sources").run();
    await env.MEMORY_DB.prepare("DELETE FROM memories").run();
  });

  it("requires the API token", async () => {
    const response = await invoke("/memories", { method: "GET" }, {
      ...testEnv,
      MEMORY_API_TOKEN: undefined,
    });

    expect(response.status).toBe(401);
  });

  it("creates, reads, patches, and tracks memory sources", async () => {
    const createdResponse = await request("/memories", {
      method: "POST",
      body: JSON.stringify({
        kind: "episode",
        content: "sota bought a PCD1000.",
        subject_user_id: "user-1",
        guild_id: "guild-1",
        channel_id: "channel-1",
        happened_at: "2026-08-25T12:00:00+09:00",
        importance: 0.8,
        confidence: 0.98,
        access_scope: "GUILD",
        source_message_ids: ["message-1", "message-2"],
      }),
    });
    expect(createdResponse.status).toBe(201);
    const created = await responseJson(createdResponse);
    expect(created.id).toEqual(expect.any(String));
    expect(created.sources).toHaveLength(2);
    expect(created.sources.map((source: any) => source.source_id).sort()).toEqual([
      "message-1",
      "message-2",
    ]);

    const readResponse = await request(
      `/memories/${created.id}?guild_id=guild-1&channel_id=channel-2`,
    );
    expect(readResponse.status).toBe(200);
    expect((await responseJson(readResponse)).content).toBe("sota bought a PCD1000.");

    const patchedResponse = await request(
      `/memories/${created.id}?guild_id=guild-1&channel_id=channel-1`,
      {
        method: "PATCH",
        body: JSON.stringify({
          importance: 0.9,
          archived_at: "2026-09-01T00:00:00Z",
        }),
      },
    );
    expect(patchedResponse.status).toBe(200);
    const patched = await responseJson(patchedResponse);
    expect(patched.importance).toBe(0.9);
    expect(patched.archived_at).toBe("2026-09-01T00:00:00.000Z");
  });

  it("filters private memories before returning search results", async () => {
    const privateResponse = await request("/memories", {
      method: "POST",
      body: JSON.stringify({
        kind: "note",
        content: "private character preference",
        subject_user_id: "user-1",
        channel_id: "dm-1",
        access_scope: "USER_PRIVATE",
        owner_user_id: "user-1",
      }),
    });
    expect(privateResponse.status).toBe(201);

    const guildSearchResponse = await request("/memories/search", {
      method: "POST",
      body: JSON.stringify({
        query: "character preference",
        requester_user_id: "user-1",
        guild_id: "guild-1",
        channel_id: "channel-1",
      }),
    });
    expect(guildSearchResponse.status).toBe(200);
    expect((await responseJson(guildSearchResponse)).memories).toHaveLength(0);

    const guildGetResponse = await request(
      `/memories/${(await responseJson(privateResponse)).id}?guild_id=guild-1&channel_id=channel-1`,
    );
    expect(guildGetResponse.status).toBe(404);

    const dmSearchResponse = await request("/memories/search", {
      method: "POST",
      body: JSON.stringify({
        query: "character preference",
        requester_user_id: "user-1",
        context_type: "dm",
        channel_id: "dm-1",
      }),
    });
    expect(dmSearchResponse.status).toBe(200);
    const dmSearch = await responseJson(dmSearchResponse);
    expect(dmSearch.memories).toHaveLength(1);

    const dmGetResponse = await request(
      `/memories/${dmSearch.memories[0].id}?requester_user_id=user-1&context_type=dm&channel_id=dm-1`,
    );
    expect(dmGetResponse.status).toBe(200);

    const otherUserSearchResponse = await request("/memories/search", {
      method: "POST",
      body: JSON.stringify({
        query: "character preference",
        requester_user_id: "user-2",
        context_type: "dm",
        channel_id: "dm-1",
      }),
    });
    expect(otherUserSearchResponse.status).toBe(200);
    expect((await responseJson(otherUserSearchResponse)).memories).toHaveLength(0);
  });

  it("does not return archived memories by default", async () => {
    const createdResponse = await request("/memories", {
      method: "POST",
      body: JSON.stringify({
        kind: "episode",
        content: "old event",
        access_scope: "PUBLIC",
        importance: 0.2,
      }),
    });
    const created = await responseJson(createdResponse);

    await request(`/memories/${created.id}`, {
      method: "PATCH",
      body: JSON.stringify({ archived_at: "2026-09-01T00:00:00Z" }),
    });

    const searchResponse = await request("/memories/search", {
      method: "POST",
      body: JSON.stringify({ query: "old event" }),
    });
    expect((await responseJson(searchResponse)).memories).toHaveLength(0);

    const archivedSearchResponse = await request("/memories/search", {
      method: "POST",
      body: JSON.stringify({ query: "old event", include_archived: true }),
    });
    expect((await responseJson(archivedSearchResponse)).memories).toHaveLength(1);
  });

  it("rejects impossible and non-ISO timestamps", async () => {
    for (const happenedAt of ["2026-02-30", "August 25, 2026"]) {
      const response = await request("/memories", {
        method: "POST",
        body: JSON.stringify({
          kind: "episode",
          content: "timestamp validation",
          happened_at: happenedAt,
          access_scope: "PUBLIC",
        }),
      });

      expect(response.status).toBe(400);
    }

    const createdResponse = await request("/memories", {
      method: "POST",
      body: JSON.stringify({
        kind: "episode",
        content: "timestamp patch validation",
        access_scope: "PUBLIC",
      }),
    });
    const created = await responseJson(createdResponse);

    for (const field of ["happened_at", "last_recalled_at", "archived_at"]) {
      const response = await request(`/memories/${created.id}`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: "2026-02-30T00:00:00Z" }),
      });

      expect(response.status).toBe(400);
    }
  });
});
