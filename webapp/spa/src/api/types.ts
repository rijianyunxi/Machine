/* 后端 API 的返回结构（按面板实际用法精简定义） */

export interface Camera {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  connected: boolean;
  thread_alive: boolean;
  rules: number[];
  frames_captured?: number | null;
  frame_age?: number | null;
}

export interface ParamSpec {
  name: string;
  type: "classes" | "list" | "float" | "int" | string;
  default?: unknown;
  desc?: string;
  min?: number;
  max?: number;
  from_model?: boolean;
}

export interface TemplateSpec {
  label: string;
  logic: string;
  params: ParamSpec[];
}

/* ---------- 可视化规则画布（契约 docs/RULE_GRAPH_DESIGN.md §2/§5） ---------- */

/** 图节点：id 为图内唯一标识；params 按节点类型 schema 取值 */
export interface GraphNode {
  id: string;
  type: string;
  params: Record<string, unknown>;
}

/** 有向边 from → to（无端口索引，v1 每节点至多一个输出点） */
export interface GraphEdge {
  from: string;
  to: string;
}

/** 画布数据（rules.yaml 的 graph 字段） */
export interface RuleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** 节点参数 schema 项（用于前端自动生成表单） */
export interface GraphParamSpec {
  name: string;
  type: string; // "classes" | "zones" | "float" | "int" | ...
  default?: unknown;
  desc?: string;
  min?: number;
  max?: number;
}

/** 节点类型注册表项（GET /api/rules/node-types） */
export interface GraphNodeTypeSpec {
  label: string; // 中文名，如「类别在场」
  category: string; // 中文分类：目标/空间/时间/逻辑/输出
  inputs: number;
  outputs: number;
  params: GraphParamSpec[];
}

export interface NodeTypesResponse {
  node_types: Record<string, GraphNodeTypeSpec>;
}

export interface RuleEntry {
  id: number;
  name: string;
  description: string;
  template: string;
  models: string[];
  params: Record<string, unknown>;
  severity: number;
  enabled: boolean;
  cameras: string[];
  warnings?: string[];
  graph?: RuleGraph | null; // template === "graph" 时存在
}

export interface ModelInstance {
  name: string;
  path: string;
  file_exists: boolean;
  config_enabled: boolean;
  loaded?: boolean;
  device?: string | null;
  confidence?: number | null;
  iou?: number | null;
  img_size?: number | null;
  classes: Record<string, string>;
  confidence_override?: number | null;
}

export interface ModelFile {
  file: string;
  size_mb: number;
  validation: { status: string; classes?: Record<string, string>; error?: string };
  registered_as?: string | null;
}

export interface ModelsResponse {
  models: ModelInstance[];
  files: ModelFile[];
}

export type AlertStatus = "new" | "confirmed" | "false_positive" | "resolved";

export interface AlertItem {
  id: number;
  camera_id: string;
  rule_id: number;
  rule_name: string;
  confidence: number;
  status: AlertStatus;
  timestamp: number;
  snapshot_path?: string | null;
  note?: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
}

export interface TrendDay {
  day: string;
  total: number;
  confirmed: number;
  pending: number;
  false_positive: number;
}

export interface SystemStats {
  standalone?: boolean;
  uptime?: number;
  frames_processed?: number;
  avg_fps?: number | string;
}

export interface StorageUsage {
  snapshots_total_mb: number;
  disk_used_pct: number;
  disk_free_gb: number;
  watermark: "ok" | "yellow" | "red" | string;
}

export interface DatasetInfo {
  name: string;
  classes: string[];
  images: number;
  labeled: number;
}

export interface PrelabelStatus {
  running: boolean;
  done: number;
  total: number;
  error?: string | null;
}

export interface SnapshotDate {
  date: string;
  count: number;
  size_mb: number;
}

export interface SnapshotFile {
  name: string;
  size_kb: number;
  thumb: string;
  url: string;
  camera: string;
  rule_dir: string;
  date: string;
  mtime?: number;
}

export interface SnapshotPage {
  dates: SnapshotDate[];
  files: SnapshotFile[];
  total: number;
  total_size_mb: number;
  offset: number;
  limit: number;
}

export interface SettingKey {
  key: string;
  type: "bool" | "int" | "float" | "str" | string;
  value: unknown;
  desc: string;
}

export interface SettingsResponse {
  sections: Record<
    string,
    { label: string; restart_required: boolean; keys: SettingKey[] }
  >;
  pending_restart: Record<string, string[]>;
}

export interface TrainStatus {
  state: "idle" | "running" | "completed" | "failed" | string;
  name?: string | null;
  epoch?: number | null;
  epochs_total?: number | null;
  mAP50?: number | null;
  mAP50_95?: number | null;
  best_path?: string | null;
  log_tail?: string[];
}

export interface TrainRun {
  name: string;
  best: string;
  size_mb: number;
}

export interface DetectResult {
  latency_ms: number;
  models: string[];
  detections: Array<Record<string, unknown>>;
  annotated_url?: string;
}

export interface DetectHistoryItem {
  time: string;
  detections: Array<Record<string, unknown>>;
  latency_ms: number;
  annotated_url: string;
}

export interface CameraTestResult {
  ok: boolean;
  width?: number;
  height?: number;
  fps?: number;
  latency_ms?: number;
  error?: string;
}
