export type MemoryKind = "episode" | "note";
export type AccessScope = "PUBLIC" | "GUILD" | "CHANNEL" | "DM" | "USER_PRIVATE";

export interface Env {
  MEMORY_DB: D1Database;
  MEMORY_API_TOKEN?: string;
}

export interface RequestContext {
  userId: string | null;
  guildId: string | null;
  channelId: string | null;
  contextType: "guild" | "dm" | null;
}

export interface MemorySource {
  id: string;
  memory_id: string;
  source_type: string;
  source_id: string;
  created_at: string;
}

export interface MemoryView {
  id: string;
  kind: MemoryKind;
  content: string;
  subject_user_id: string | null;
  guild_id: string | null;
  channel_id: string | null;
  happened_at: string | null;
  created_at: string;
  updated_at: string;
  importance: number;
  confidence: number;
  access_scope: AccessScope;
  owner_user_id: string | null;
  last_recalled_at: string | null;
  recall_count: number;
  archived_at: string | null;
  sources: MemorySource[];
}

interface MemoryRow {
  id: string;
  kind: MemoryKind;
  content: string;
  subject_user_id: string | null;
  guild_id: string | null;
  channel_id: string | null;
  happened_at: string | null;
  created_at: string;
  updated_at: string;
  importance: number;
  confidence: number;
  access_scope: AccessScope;
  owner_user_id: string | null;
  last_recalled_at: string | null;
  recall_count: number;
  archived_at: string | null;
}

interface SourceRow {
  id: string;
  memory_id: string;
  source_type: string;
  source_id: string;
  created_at: string;
}

interface NormalizedSource {
  source_type: string;
  source_id: string;
}

interface CreateMemoryInput {
  kind: MemoryKind;
  content: string;
  subjectUserId: string | null;
  guildId: string | null;
  channelId: string | null;
  happenedAt: string | null;
  importance: number;
  confidence: number;
  accessScope: AccessScope;
  ownerUserId: string | null;
  sources: NormalizedSource[];
}

interface ListOptions {
  query?: string;
  kind?: MemoryKind;
  accessScope?: AccessScope;
  subjectUserId?: string;
  includeArchived: boolean;
  limit: number;
}

const MEMORY_COLUMNS = `
  id,
  kind,
  content,
  subject_user_id,
  guild_id,
  channel_id,
  happened_at,
  created_at,
  updated_at,
  importance,
  confidence,
  access_scope,
  owner_user_id,
  last_recalled_at,
  recall_count,
  archived_at
`;

const MAX_CONTENT_LENGTH = 10_000;
const MAX_IDENTIFIER_LENGTH = 128;
const MAX_SOURCE_COUNT = 50;
const MAX_LIMIT = 50;
const ISO_8601_TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/;

class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

function badRequest(message: string): ApiError {
  return new ApiError(400, "invalid_request", message);
}

function notFound(): ApiError {
  return new ApiError(404, "not_found", "Memory not found.");
}

function json(data: unknown, status = 200, extraHeaders: HeadersInit = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
  });
}

function errorResponse(error: unknown): Response {
  if (error instanceof ApiError) {
    return json({
      error: {
        code: error.code,
        message: error.message,
      },
    }, error.status);
  }

  console.error("Memory API request failed", error);
  return json({
    error: {
      code: "internal_error",
      message: "An internal error occurred.",
    },
  }, 500);
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const length = Math.max(leftBytes.length, rightBytes.length);
  let difference = leftBytes.length ^ rightBytes.length;

  for (let index = 0; index < length; index += 1) {
    difference |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }

  return difference === 0;
}

function isAuthorized(request: Request, env: Env): boolean {
  const authorization = request.headers.get("authorization") ?? "";
  const match = authorization.match(/^Bearer\s+(.+)$/i);
  const suppliedToken = match?.[1]?.trim();
  const expectedToken = env.MEMORY_API_TOKEN?.trim();

  if (!suppliedToken || !expectedToken) {
    return false;
  }

  return constantTimeEqual(suppliedToken, expectedToken);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readJson(request: Request): Promise<Record<string, unknown>> {
  let value: unknown;
  try {
    value = await request.json();
  } catch {
    throw badRequest("Request body must be valid JSON.");
  }

  if (!isRecord(value)) {
    throw badRequest("Request body must be a JSON object.");
  }

  return value;
}

function parseIdentifier(value: unknown, field: string, nullable: false): string;
function parseIdentifier(value: unknown, field: string, nullable?: true): string | null;
function parseIdentifier(value: unknown, field: string, nullable = true): string | null {
  if (value === undefined) {
    if (nullable) {
      return null;
    }
    throw badRequest(`${field} is required.`);
  }

  if (value === null) {
    if (nullable) {
      return null;
    }
    throw badRequest(`${field} is required.`);
  }

  let result: string;
  if (typeof value === "string") {
    result = value.trim();
  } else if (typeof value === "number" && Number.isSafeInteger(value)) {
    result = String(value);
  } else {
    throw badRequest(`${field} must be a string or a safe integer.`);
  }

  if (!result || result.length > MAX_IDENTIFIER_LENGTH) {
    throw badRequest(`${field} must contain between 1 and ${MAX_IDENTIFIER_LENGTH} characters.`);
  }

  return result;
}

function parseRequiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") {
    throw badRequest(`${field} is required and must be a string.`);
  }

  const result = value.trim();
  if (!result) {
    throw badRequest(`${field} must not be empty.`);
  }
  if (result.length > maxLength) {
    throw badRequest(`${field} must be at most ${maxLength} characters.`);
  }

  return result;
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    return isLeapYear ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function parseTimestamp(value: unknown, field: string, nullable = true): string | null {
  if (value === undefined || value === null) {
    if (nullable) {
      return null;
    }
    throw badRequest(`${field} is required.`);
  }
  if (typeof value !== "string") {
    throw badRequest(`${field} must be an ISO-8601 string or null.`);
  }

  const match = ISO_8601_TIMESTAMP_PATTERN.exec(value);
  if (!match) {
    throw badRequest(`${field} must be an ISO-8601 timestamp with a timezone.`);
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const timezone = match[8];

  if (day > daysInMonth(year, month)) {
    throw badRequest(`${field} must contain a real calendar date.`);
  }
  if (hour > 23 || minute > 59 || second > 59) {
    throw badRequest(`${field} must contain a valid time.`);
  }
  if (timezone !== "Z") {
    const offsetMatch = /^[+-](\d{2}):(\d{2})$/.exec(timezone);
    if (!offsetMatch || Number(offsetMatch[1]) > 23 || Number(offsetMatch[2]) > 59) {
      throw badRequest(`${field} must contain a valid timezone offset.`);
    }
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw badRequest(`${field} must be a valid ISO-8601 timestamp.`);
  }

  return date.toISOString();
}

function parseScore(value: unknown, field: string, defaultValue: number): number {
  if (value === undefined) {
    return defaultValue;
  }
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    throw badRequest(`${field} must be a number between 0 and 1.`);
  }

  return value;
}

function parseNonNegativeInteger(value: unknown, field: string, defaultValue: number): number {
  if (value === undefined) {
    return defaultValue;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw badRequest(`${field} must be a non-negative integer.`);
  }

  return value;
}

function parseBoolean(value: unknown, field: string, defaultValue: boolean): boolean {
  if (value === undefined) {
    return defaultValue;
  }
  if (typeof value !== "boolean") {
    throw badRequest(`${field} must be a boolean.`);
  }

  return value;
}

function parseQueryBoolean(value: string | null, field: string, defaultValue: boolean): boolean {
  if (value === null) {
    return defaultValue;
  }
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }

  throw badRequest(`${field} must be true or false.`);
}

function parseLimit(value: unknown, defaultValue: number): number {
  if (value === undefined) {
    return defaultValue;
  }

  let parsed: number;
  if (typeof value === "number") {
    parsed = value;
  } else if (typeof value === "string" && /^\d+$/.test(value)) {
    parsed = Number(value);
  } else {
    throw badRequest("limit must be a positive integer.");
  }

  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > MAX_LIMIT) {
    throw badRequest(`limit must be between 1 and ${MAX_LIMIT}.`);
  }

  return parsed;
}

function parseKind(value: unknown, field = "kind"): MemoryKind {
  if (typeof value !== "string") {
    throw badRequest(`${field} must be episode or note.`);
  }

  const kind = value.trim().toLowerCase();
  if (kind !== "episode" && kind !== "note") {
    throw badRequest(`${field} must be episode or note.`);
  }

  return kind;
}

function parseAccessScope(value: unknown, field = "access_scope"): AccessScope {
  if (typeof value !== "string") {
    throw badRequest(`${field} must be PUBLIC, GUILD, CHANNEL, DM, or USER_PRIVATE.`);
  }

  const scope = value.trim().toUpperCase();
  if (![
    "PUBLIC",
    "GUILD",
    "CHANNEL",
    "DM",
    "USER_PRIVATE",
  ].includes(scope)) {
    throw badRequest(`${field} must be PUBLIC, GUILD, CHANNEL, DM, or USER_PRIVATE.`);
  }

  return scope as AccessScope;
}

function validateScopeRequirements(
  accessScope: AccessScope,
  guildId: string | null,
  channelId: string | null,
  ownerUserId: string | null,
): void {
  if (accessScope === "GUILD" && !guildId) {
    throw badRequest("guild_id is required for GUILD memories.");
  }
  if (accessScope === "CHANNEL" && !guildId) {
    throw badRequest("guild_id is required for CHANNEL memories.");
  }
  if (accessScope === "CHANNEL" && !channelId) {
    throw badRequest("channel_id is required for CHANNEL memories.");
  }
  if (accessScope === "DM" && (!channelId || guildId)) {
    throw badRequest("DM memories require channel_id and must not have guild_id.");
  }
  if (accessScope === "USER_PRIVATE" && (!ownerUserId || guildId)) {
    throw badRequest("USER_PRIVATE memories require owner_user_id and must not have guild_id.");
  }
}

function parseSourceType(value: unknown): string {
  if (typeof value !== "string") {
    throw badRequest("sources[].source_type must be a string.");
  }

  const sourceType = value.trim().toLowerCase();
  if (!/^[a-z][a-z0-9_]{1,63}$/.test(sourceType)) {
    throw badRequest("sources[].source_type must be a lowercase identifier.");
  }

  return sourceType;
}

function parseSources(record: Record<string, unknown>): NormalizedSource[] {
  const sources: NormalizedSource[] = [];
  const seen = new Set<string>();

  const addSource = (sourceType: string, sourceId: string): void => {
    const key = `${sourceType}:${sourceId}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    sources.push({ source_type: sourceType, source_id: sourceId });
  };

  if (record.sources !== undefined) {
    if (!Array.isArray(record.sources)) {
      throw badRequest("sources must be an array.");
    }

    for (const source of record.sources) {
      if (!isRecord(source)) {
        throw badRequest("Each source must be an object.");
      }
      const sourceType = parseSourceType(source.source_type);
      const sourceId = parseIdentifier(source.source_id, "sources[].source_id", false);
      addSource(sourceType, sourceId);
    }
  }

  if (record.source_message_ids !== undefined) {
    if (!Array.isArray(record.source_message_ids)) {
      throw badRequest("source_message_ids must be an array.");
    }

    for (const sourceId of record.source_message_ids) {
      addSource(
        "discord_message",
        parseIdentifier(sourceId, "source_message_ids[]", false),
      );
    }
  }

  if (sources.length > MAX_SOURCE_COUNT) {
    throw badRequest(`At most ${MAX_SOURCE_COUNT} sources may be attached to one memory.`);
  }

  return sources;
}

function parseCreateMemory(record: Record<string, unknown>): CreateMemoryInput {
  const kind = parseKind(record.kind);
  const content = parseRequiredText(record.content, "content", MAX_CONTENT_LENGTH);
  const subjectUserId = parseIdentifier(record.subject_user_id, "subject_user_id");
  const guildId = parseIdentifier(record.guild_id, "guild_id");
  const channelId = parseIdentifier(record.channel_id, "channel_id");
  const happenedAt = parseTimestamp(record.happened_at, "happened_at");
  const importance = parseScore(record.importance, "importance", 0.5);
  const confidence = parseScore(record.confidence, "confidence", 0.5);
  const accessScope = parseAccessScope(record.access_scope);
  const ownerUserId = parseIdentifier(record.owner_user_id, "owner_user_id");

  validateScopeRequirements(accessScope, guildId, channelId, ownerUserId);

  return {
    kind,
    content,
    subjectUserId,
    guildId,
    channelId,
    happenedAt,
    importance,
    confidence,
    accessScope,
    ownerUserId,
    sources: parseSources(record),
  };
}

function parseRequestContext(record: Record<string, unknown>): RequestContext {
  let context = record;
  if (record.context !== undefined) {
    if (!isRecord(record.context)) {
      throw badRequest("context must be an object.");
    }
    context = record.context;
  }

  const userId = parseIdentifier(
    context.user_id ?? record.requester_user_id,
    "requester_user_id",
  );
  const guildId = parseIdentifier(context.guild_id ?? record.guild_id, "guild_id");
  const channelId = parseIdentifier(context.channel_id ?? record.channel_id, "channel_id");
  const rawContextType = context.context_type
    ?? context.type
    ?? record.context_type;

  let contextType: RequestContext["contextType"] = null;
  if (rawContextType !== undefined && rawContextType !== null) {
    if (typeof rawContextType !== "string") {
      throw badRequest("context_type must be guild or dm.");
    }
    const normalizedContextType = rawContextType.trim().toLowerCase();
    if (normalizedContextType !== "guild" && normalizedContextType !== "dm") {
      throw badRequest("context_type must be guild or dm.");
    }
    contextType = normalizedContextType;
  } else if (guildId !== null) {
    // A supplied guild_id is enough to identify the normal Discord guild case.
    // DM access is never inferred, so a missing context_type fails closed.
    contextType = "guild";
  }

  if (contextType === "guild" && guildId === null) {
    throw badRequest("guild_id is required for a guild context.");
  }
  if (contextType === "dm" && (channelId === null || guildId !== null)) {
    throw badRequest("DM context requires channel_id and must not have guild_id.");
  }

  return { userId, guildId, channelId, contextType };
}

function contextFromUrl(url: URL): RequestContext {
  const record: Record<string, unknown> = {
    requester_user_id: url.searchParams.get("requester_user_id") ?? undefined,
    guild_id: url.searchParams.get("guild_id") ?? undefined,
    channel_id: url.searchParams.get("channel_id") ?? undefined,
    context_type: url.searchParams.get("context_type") ?? undefined,
  };
  return parseRequestContext(record);
}

function buildAccessPredicate(context: RequestContext): { sql: string; params: string[] } {
  const clauses = ["access_scope = 'PUBLIC'"];
  const params: string[] = [];

  if (context.contextType === "guild" && context.guildId !== null) {
    clauses.push("(access_scope = 'GUILD' AND guild_id = ?)");
    params.push(context.guildId);

    if (context.channelId !== null) {
      clauses.push("(access_scope = 'CHANNEL' AND channel_id = ? AND guild_id = ?)");
      params.push(context.channelId, context.guildId);
    }
  }

  if (context.contextType === "dm" && context.channelId !== null) {
    clauses.push("(access_scope = 'DM' AND channel_id = ? AND guild_id IS NULL)");
    params.push(context.channelId);

    if (context.userId !== null) {
      clauses.push("(access_scope = 'USER_PRIVATE' AND owner_user_id = ? AND guild_id IS NULL)");
      params.push(context.userId);
    }
  }

  return {
    sql: clauses.join(" OR "),
    params,
  };
}

export function canAccessMemory(memory: Pick<MemoryView, "access_scope" | "guild_id" | "channel_id" | "owner_user_id">, context: RequestContext): boolean {
  if (memory.access_scope === "PUBLIC") {
    return true;
  }
  if (context.contextType === "guild") {
    if (memory.access_scope === "GUILD") {
      return memory.guild_id !== null && memory.guild_id === context.guildId;
    }
    if (memory.access_scope === "CHANNEL") {
      return memory.channel_id !== null
        && memory.channel_id === context.channelId
        && memory.guild_id !== null
        && memory.guild_id === context.guildId;
    }
    return false;
  }
  if (context.contextType === "dm") {
    if (memory.access_scope === "DM") {
      return memory.channel_id !== null
        && memory.channel_id === context.channelId
        && memory.guild_id === null;
    }
    if (memory.access_scope === "USER_PRIVATE") {
      return memory.owner_user_id !== null
        && memory.owner_user_id === context.userId
        && memory.guild_id === null;
    }
  }
  return false;
}

function rowToMemory(row: MemoryRow, sources: MemorySource[] = []): MemoryView {
  return {
    id: row.id,
    kind: row.kind,
    content: row.content,
    subject_user_id: row.subject_user_id,
    guild_id: row.guild_id,
    channel_id: row.channel_id,
    happened_at: row.happened_at,
    created_at: row.created_at,
    updated_at: row.updated_at,
    importance: Number(row.importance),
    confidence: Number(row.confidence),
    access_scope: row.access_scope,
    owner_user_id: row.owner_user_id,
    last_recalled_at: row.last_recalled_at,
    recall_count: Number(row.recall_count),
    archived_at: row.archived_at,
    sources,
  };
}

async function loadSources(db: D1Database, memoryIds: string[]): Promise<Map<string, MemorySource[]>> {
  const result = new Map<string, MemorySource[]>();
  if (memoryIds.length === 0) {
    return result;
  }

  const placeholders = memoryIds.map(() => "?").join(", ");
  const rows = await db.prepare(`
    SELECT id, memory_id, source_type, source_id, created_at
    FROM memory_sources
    WHERE memory_id IN (${placeholders})
    ORDER BY created_at ASC, id ASC
  `).bind(...memoryIds).all<SourceRow>();

  for (const row of rows.results) {
    const sources = result.get(row.memory_id) ?? [];
    sources.push({
      id: row.id,
      memory_id: row.memory_id,
      source_type: row.source_type,
      source_id: row.source_id,
      created_at: row.created_at,
    });
    result.set(row.memory_id, sources);
  }

  return result;
}

async function loadMemory(db: D1Database, id: string): Promise<MemoryView | null> {
  const row = await db.prepare(`
    SELECT ${MEMORY_COLUMNS}
    FROM memories
    WHERE id = ?
  `).bind(id).first<MemoryRow>();
  if (!row) {
    return null;
  }

  const sources = await loadSources(db, [id]);
  return rowToMemory(row, sources.get(id) ?? []);
}

async function loadAccessibleMemory(
  db: D1Database,
  id: string,
  context: RequestContext,
): Promise<MemoryView | null> {
  const access = buildAccessPredicate(context);
  const row = await db.prepare(`
    SELECT ${MEMORY_COLUMNS}
    FROM memories
    WHERE id = ? AND (${access.sql})
  `).bind(id, ...access.params).first<MemoryRow>();
  if (!row) {
    return null;
  }

  const sources = await loadSources(db, [id]);
  return rowToMemory(row, sources.get(id) ?? []);
}

async function listMemories(
  db: D1Database,
  context: RequestContext,
  options: ListOptions,
): Promise<MemoryView[]> {
  const access = buildAccessPredicate(context);
  const clauses = [`(${access.sql})`];
  const params: (string | number)[] = [...access.params];

  if (!options.includeArchived) {
    clauses.push("archived_at IS NULL");
  }
  if (options.query !== undefined) {
    clauses.push("instr(lower(content), lower(?)) > 0");
    params.push(options.query);
  }
  if (options.kind !== undefined) {
    clauses.push("kind = ?");
    params.push(options.kind);
  }
  if (options.accessScope !== undefined) {
    clauses.push("access_scope = ?");
    params.push(options.accessScope);
  }
  if (options.subjectUserId !== undefined) {
    clauses.push("subject_user_id = ?");
    params.push(options.subjectUserId);
  }

  const rows = await db.prepare(`
    SELECT ${MEMORY_COLUMNS}
    FROM memories
    WHERE ${clauses.join(" AND ")}
    ORDER BY importance DESC, COALESCE(happened_at, created_at) DESC, created_at DESC
    LIMIT ?
  `).bind(...params, options.limit).all<MemoryRow>();

  const sources = await loadSources(db, rows.results.map((row) => row.id));
  return rows.results.map((row) => rowToMemory(row, sources.get(row.id) ?? []));
}

async function createMemory(env: Env, input: CreateMemoryInput): Promise<MemoryView> {
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  const statements: D1PreparedStatement[] = [
    env.MEMORY_DB.prepare(`
      INSERT INTO memories (
        id,
        kind,
        content,
        subject_user_id,
        guild_id,
        channel_id,
        happened_at,
        created_at,
        updated_at,
        importance,
        confidence,
        access_scope,
        owner_user_id,
        last_recalled_at,
        recall_count,
        archived_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      id,
      input.kind,
      input.content,
      input.subjectUserId,
      input.guildId,
      input.channelId,
      input.happenedAt,
      now,
      now,
      input.importance,
      input.confidence,
      input.accessScope,
      input.ownerUserId,
      null,
      0,
      null,
    ),
  ];

  for (const source of input.sources) {
    statements.push(env.MEMORY_DB.prepare(`
      INSERT INTO memory_sources (
        id,
        memory_id,
        source_type,
        source_id,
        created_at
      ) VALUES (?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      id,
      source.source_type,
      source.source_id,
      now,
    ));
  }

  await env.MEMORY_DB.batch(statements);
  const memory = await loadMemory(env.MEMORY_DB, id);
  if (!memory) {
    throw new Error("Memory was inserted but could not be loaded.");
  }

  return memory;
}

const PATCHABLE_FIELDS = new Set([
  "content",
  "subject_user_id",
  "guild_id",
  "channel_id",
  "happened_at",
  "importance",
  "confidence",
  "access_scope",
  "owner_user_id",
  "last_recalled_at",
  "recall_count",
  "archived_at",
]);

async function updateMemory(
  env: Env,
  existing: MemoryView,
  record: Record<string, unknown>,
): Promise<MemoryView> {
  const unknownFields = Object.keys(record).filter((field) => !PATCHABLE_FIELDS.has(field));
  if (unknownFields.length > 0) {
    throw badRequest(`Unsupported PATCH fields: ${unknownFields.join(", ")}.`);
  }
  if (Object.keys(record).length === 0) {
    throw badRequest("At least one PATCH field is required.");
  }

  const content = record.content === undefined
    ? existing.content
    : parseRequiredText(record.content, "content", MAX_CONTENT_LENGTH);
  const subjectUserId = record.subject_user_id === undefined
    ? existing.subject_user_id
    : parseIdentifier(record.subject_user_id, "subject_user_id");
  const guildId = record.guild_id === undefined
    ? existing.guild_id
    : parseIdentifier(record.guild_id, "guild_id");
  const channelId = record.channel_id === undefined
    ? existing.channel_id
    : parseIdentifier(record.channel_id, "channel_id");
  const happenedAt = record.happened_at === undefined
    ? existing.happened_at
    : parseTimestamp(record.happened_at, "happened_at");
  const importance = parseScore(record.importance, "importance", existing.importance);
  const confidence = parseScore(record.confidence, "confidence", existing.confidence);
  const accessScope = record.access_scope === undefined
    ? existing.access_scope
    : parseAccessScope(record.access_scope);
  const ownerUserId = record.owner_user_id === undefined
    ? existing.owner_user_id
    : parseIdentifier(record.owner_user_id, "owner_user_id");
  const lastRecalledAt = record.last_recalled_at === undefined
    ? existing.last_recalled_at
    : parseTimestamp(record.last_recalled_at, "last_recalled_at");
  const recallCount = parseNonNegativeInteger(record.recall_count, "recall_count", existing.recall_count);
  const archivedAt = record.archived_at === undefined
    ? existing.archived_at
    : parseTimestamp(record.archived_at, "archived_at");

  validateScopeRequirements(accessScope, guildId, channelId, ownerUserId);

  await env.MEMORY_DB.prepare(`
    UPDATE memories
    SET
      content = ?,
      subject_user_id = ?,
      guild_id = ?,
      channel_id = ?,
      happened_at = ?,
      updated_at = ?,
      importance = ?,
      confidence = ?,
      access_scope = ?,
      owner_user_id = ?,
      last_recalled_at = ?,
      recall_count = ?,
      archived_at = ?
    WHERE id = ?
  `).bind(
    content,
    subjectUserId,
    guildId,
    channelId,
    happenedAt,
    new Date().toISOString(),
    importance,
    confidence,
    accessScope,
    ownerUserId,
    lastRecalledAt,
    recallCount,
    archivedAt,
    existing.id,
  ).run();

  const updated = await loadMemory(env.MEMORY_DB, existing.id);
  if (!updated) {
    throw new Error("Memory was updated but could not be loaded.");
  }

  return updated;
}

async function handleCreate(request: Request, env: Env): Promise<Response> {
  const record = await readJson(request);
  const input = parseCreateMemory(record);
  return json(await createMemory(env, input), 201);
}

async function handleSearch(request: Request, env: Env): Promise<Response> {
  const record = await readJson(request);
  const query = parseRequiredText(record.query, "query", 1_000);
  const context = parseRequestContext(record);
  const subjectUserId = parseIdentifier(record.subject_user_id, "subject_user_id");
  const includeArchived = parseBoolean(record.include_archived, "include_archived", false);
  const limit = parseLimit(record.limit, 5);
  const memories = await listMemories(env.MEMORY_DB, context, {
    query,
    subjectUserId: subjectUserId ?? undefined,
    includeArchived,
    limit,
  });

  return json({ memories, query, limit });
}

async function handleList(request: Request, env: Env, url: URL): Promise<Response> {
  const context = contextFromUrl(url);
  const queryValue = url.searchParams.get("query") ?? url.searchParams.get("q");
  const query = queryValue === null ? undefined : parseRequiredText(queryValue, "query", 1_000);
  const kindValue = url.searchParams.get("kind");
  const accessScopeValue = url.searchParams.get("access_scope");
  const subjectUserIdValue = url.searchParams.get("subject_user_id");
  const kind = kindValue === null ? undefined : parseKind(kindValue);
  const accessScope = accessScopeValue === null ? undefined : parseAccessScope(accessScopeValue);
  const subjectUserId = subjectUserIdValue === null
    ? undefined
    : parseIdentifier(subjectUserIdValue, "subject_user_id", false);
  const includeArchived = parseQueryBoolean(
    url.searchParams.get("include_archived"),
    "include_archived",
    false,
  );
  const limit = parseLimit(url.searchParams.get("limit") ?? undefined, 20);
  const memories = await listMemories(env.MEMORY_DB, context, {
    query,
    kind,
    accessScope,
    subjectUserId,
    includeArchived,
    limit,
  });

  return json({ memories, limit });
}

async function handleGet(
  request: Request,
  env: Env,
  url: URL,
  id: string,
): Promise<Response> {
  const context = contextFromUrl(url);
  const memory = await loadAccessibleMemory(env.MEMORY_DB, id, context);
  if (!memory) {
    throw notFound();
  }

  return json(memory);
}

async function handlePatch(
  request: Request,
  env: Env,
  url: URL,
  id: string,
): Promise<Response> {
  const context = contextFromUrl(url);
  const existing = await loadAccessibleMemory(env.MEMORY_DB, id, context);
  if (!existing) {
    throw notFound();
  }

  const record = await readJson(request);
  return json(await updateMemory(env, existing, record));
}

function methodNotAllowed(allow: string): Response {
  return json({
    error: {
      code: "method_not_allowed",
      message: "Method not allowed.",
    },
  }, 405, { Allow: allow });
}

function decodeMemoryId(value: string): string {
  try {
    const id = decodeURIComponent(value).trim();
    if (!id || id.length > MAX_IDENTIFIER_LENGTH) {
      throw new Error("invalid id");
    }
    return id;
  } catch {
    throw badRequest("Memory id must be a valid path segment.");
  }
}

export default {
  async fetch(request: Request, env: Env, _ctx?: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const pathname = url.pathname.replace(/\/+$/, "") || "/";

    if (pathname === "/health") {
      if (request.method !== "GET") {
        return methodNotAllowed("GET");
      }
      return json({ status: "ok" });
    }

    if (pathname !== "/memories" && !pathname.startsWith("/memories/")) {
      return errorResponse(new ApiError(404, "not_found", "Route not found."));
    }
    if (!isAuthorized(request, env)) {
      return json({
        error: {
          code: "unauthorized",
          message: "A valid bearer token is required.",
        },
      }, 401, { "WWW-Authenticate": "Bearer" });
    }

    try {
      const segments = pathname.split("/").filter(Boolean);
      if (segments.length === 1) {
        if (request.method === "POST") {
          return await handleCreate(request, env);
        }
        if (request.method === "GET") {
          return await handleList(request, env, url);
        }
        return methodNotAllowed("GET, POST");
      }

      if (segments.length === 2 && segments[1] === "search") {
        if (request.method !== "POST") {
          return methodNotAllowed("POST");
        }
        return await handleSearch(request, env);
      }

      if (segments.length === 2) {
        const id = decodeMemoryId(segments[1]);
        if (request.method === "GET") {
          return await handleGet(request, env, url, id);
        }
        if (request.method === "PATCH") {
          return await handlePatch(request, env, url, id);
        }
        return methodNotAllowed("GET, PATCH");
      }

      throw new ApiError(404, "not_found", "Route not found.");
    } catch (error) {
      return errorResponse(error);
    }
  },
} satisfies ExportedHandler<Env>;
