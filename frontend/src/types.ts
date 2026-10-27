export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_input"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "blocked"
  | "needs_input"
  | "cancelled";

export const TERMINAL: RunStatus[] = ["completed", "failed", "blocked", "needs_input", "cancelled"];

export interface Health {
  status: string;
  gemini_configured: boolean;
  model: string;
  max_iterations: number;
  max_result_rows: number;
  local_paths_enabled: boolean;
  auth_required: boolean;
}

export interface Sample {
  id: string;
  title: string;
  description: string;
  prompts: string[];
}

export interface Source {
  id: string;
  name: string;
  kind: "sample" | "sqlite" | "excel" | "csv" | "server";
  dialect: string;
  target: string;
  editable: boolean;
  origin?: "path" | "upload";
  file?: string;
  sheets?: { table: string; sheet: string; rows: number }[];
  created_at: string;
}

export interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  note: string | null;
}

export interface TableInfo {
  name: string;
  kind: string;
  row_count: number | null;
  note: string | null;
  columns: ColumnInfo[];
  foreign_keys: { columns: string[]; referred_table: string; referred_columns: string[] }[];
}

export interface Schema {
  dialect: string;
  tables: TableInfo[];
}

export interface AgentEvent {
  seq: number;
  ts: string;
  iteration: number;
  phase: string;
  level: "info" | "success" | "warning" | "error";
  message: string;
  detail: Record<string, unknown> | null;
}

export interface Attempt {
  iteration: number;
  sql: string | null;
  params: Record<string, unknown>;
  outcome: string;
  error: string;
  feedback: string;
}

export interface CellChange {
  row: number;
  column: string;
  header: string;
  col_letter: string;
  old: unknown;
  new: unknown;
}

export interface ChangePreview {
  id: string;
  table: string;
  sheet: string;
  statement_type: string;
  sql: string;
  affected_rows: number;
  updated_cells: number;
  new_columns: { name: string; header: string; col_letter: string }[];
  updates: CellChange[];
  inserted_rows: Record<string, unknown>[];
  deleted_rows: Record<string, unknown>[];
  truncated_preview: boolean;
  warnings: string[];
}

export type WriteMode = "copy" | "in_place" | "browser";

export interface Pending {
  type: "source" | "clarification" | "sql_approval" | "change_approval";
  message?: string;
  problem?: string | null;
  question?: string;
  sql?: string;
  params?: Record<string, unknown>;
  plan?: string[];
  error?: string | null;
  change?: ChangePreview;
  default_mode?: WriteMode;
  can_in_place?: boolean;
  file?: string;
}

export interface ExportFile {
  id: string;
  filename: string;
  rows: number;
  url: string;
  format: "xlsx" | "csv";
  kind?: string;
}

export interface Run {
  id: string;
  request: string;
  created_at: string;
  finished_at: string | null;
  status: RunStatus;
  phase: string;
  message: string;
  iteration: number;
  max_iterations: number;
  options: { source_id: string | null; require_approval: boolean; write_mode: WriteMode };
  source: Source | null;
  goal: {
    interpreted_goal: string;
    operation: "read" | "modify";
    success_criteria: string[];
    requested_columns: string[];
  } | null;
  plan: string[];
  sql: string | null;
  params: Record<string, unknown>;
  validation: { ok: boolean; errors: string[]; warnings: string[]; tables: string[] } | null;
  errors: string[];
  attempts: Attempt[];
  result: { columns: string[]; row_count: number; truncated: boolean; elapsed_ms: number; row_limit: number } | null;
  evaluation: { satisfied: boolean; issues: string[]; summary: string } | null;
  change_preview: ChangePreview | null;
  apply_result: {
    mode: string;
    write_back: WriteMode;
    output_name: string;
    output_path?: string;
    backup_path: string | null;
    verified_cells: number;
    affected_rows: number;
  } | null;
  exports: ExportFile[];
  export_status: string;
  summary: string | null;
  pending: Pending | null;
  llm: string | null;
  events: AgentEvent[];
  event_count: number;
}

export interface RunBrief {
  id: string;
  request: string;
  status: RunStatus;
  phase: string;
  created_at: string;
  finished_at: string | null;
  iteration: number;
  row_count: number | null;
  source: string | null;
}

export interface RowsPage {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  page_size: number;
  all_rows: number;
}
