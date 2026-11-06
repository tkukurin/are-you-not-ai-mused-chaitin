export type Actor = {
  actor_id: string;
  display_name: string;
  actor_type: "human" | "ai" | string;
  color: string;
};

export type Conversation = {
  conversation_id: string;
  title: string;
  created_at?: string | null;
  member_ids: string[];
  message_count?: number;
  members?: Actor[];
};

export type Message = {
  message_id: string;
  conversation_id?: string;
  actor_id: string;
  display_name: string;
  color: string;
  content: string;
  message_type?: string;
  trace_id?: string | null;
  created_at?: string | null;
  metadata?: Record<string, any>;
};

export type UINode = {
  id: string;
  parent_id?: string | null;
  type: string;
  props: Record<string, any>;
  layout: Record<string, any>;
  children?: UINode[];
  order_index?: number;
};

export type Workspace = {
  workspace_id: string;
  owner_actor_id: string;
  title: string;
  nodes: UINode[];
  flat_nodes: UINode[];
  head?: string | null;
  is_historical?: boolean;
};

export type Commit = {
  commit_hash: string;
  message: string;
  date?: string;
  committer?: string;
  email?: string;
};

export type TraceSummary = {
  trace_id: string;
  triggering_message_id: string;
  response_message_id?: string | null;
  actor_id: string;
  actor_name: string;
  workspace_id: string;
  model_name: string;
  provider: string;
  status: string;
  duration_ms: number;
  created_at?: string;
};

export type Patch = {
  op: "insert" | "remove" | "move" | "set" | string;
  parent_id?: string | null;
  id?: string;
  node?: UINode;
};
