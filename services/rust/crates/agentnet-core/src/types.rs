//! AgentNet data types — shared structures for DAG orchestration, task management,
//! agent capabilities, and spawn tracking.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

// ── Agent Capability ───────────────────────────────────────────────────

/// Self-declared capability manifest published by each agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCapability {
    pub agent_id: String,
    pub display_name: String,
    pub capabilities: Vec<String>,
    pub preferred_tools: Vec<String>,
    pub quality_score: f64,
    pub current_load: i32,
    pub max_concurrent: i32,
    pub cost_per_task: f64,
    pub status: AgentStatus,
    pub last_heartbeat: DateTime<Utc>,
    pub registered_at: DateTime<Utc>,
}

impl AgentCapability {
    /// Create a new capability manifest with defaults.
    pub fn new(agent_id: String, display_name: String, capabilities: Vec<String>) -> Self {
        let now = Utc::now();
        Self {
            agent_id,
            display_name,
            capabilities,
            preferred_tools: vec![],
            quality_score: 0.8,
            current_load: 0,
            max_concurrent: 5,
            cost_per_task: 0.01,
            status: AgentStatus::Idle,
            last_heartbeat: now,
            registered_at: now,
        }
    }

    /// Current load as a ratio (0.0–1.0).
    pub fn load_ratio(&self) -> f64 {
        if self.max_concurrent == 0 {
            return 1.0;
        }
        self.current_load as f64 / self.max_concurrent as f64
    }

    /// Cost-effectiveness score: quality / cost (higher is better).
    pub fn cost_effectiveness(&self) -> f64 {
        self.quality_score / (self.cost_per_task.max(0.0001))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AgentStatus {
    Idle,
    Busy,
    Overloaded,
    Offline,
}

impl std::fmt::Display for AgentStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AgentStatus::Idle => write!(f, "idle"),
            AgentStatus::Busy => write!(f, "busy"),
            AgentStatus::Overloaded => write!(f, "overloaded"),
            AgentStatus::Offline => write!(f, "offline"),
        }
    }
}

// ── DAG Types ─────────────────────────────────────────────────────────

/// A node in the dynamic task DAG.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DagNode {
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    pub description: String,
    #[serde(default)]
    pub required_capability: String,
    #[serde(default)]
    pub dependencies: Vec<String>,
    pub status: TaskStatus,
    #[serde(default)]
    pub priority: i32,
    #[serde(default)]
    pub estimated_seconds: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
}

impl DagNode {
    pub fn new(id: String, description: String) -> Self {
        Self {
            id,
            task_id: None,
            agent_id: None,
            description,
            required_capability: String::new(),
            dependencies: vec![],
            status: TaskStatus::Pending,
            priority: 0,
            estimated_seconds: 0,
            result: None,
            error: None,
            started_at: None,
            completed_at: None,
        }
    }
}

/// A directed edge between two DAG nodes.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DagEdge {
    pub from: String,
    pub to: String,
    #[serde(default)]
    pub label: String,
    #[serde(default = "default_edge_weight")]
    pub weight: f64,
}

fn default_edge_weight() -> f64 {
    1.0
}

/// A dynamic task graph (DAG).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Dag {
    pub dag_id: String,
    pub name: String,
    #[serde(default)]
    pub tenant_id: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub nodes: Vec<DagNode>,
    #[serde(default)]
    pub edges: Vec<DagEdge>,
    pub status: DagStatus,
    pub strategy: AssignmentStrategy,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl Dag {
    pub fn new(dag_id: String, name: String) -> Self {
        let now = Utc::now();
        Self {
            dag_id,
            name,
            tenant_id: String::new(),
            session_id: String::new(),
            nodes: vec![],
            edges: vec![],
            status: DagStatus::Created,
            strategy: AssignmentStrategy::CapabilityMatch,
            created_at: now,
            updated_at: now,
        }
    }

    /// Find a node by ID.
    pub fn find_node(&self, node_id: &str) -> Option<&DagNode> {
        self.nodes.iter().find(|n| n.id == node_id)
    }

    /// Find a node by ID (mutable).
    pub fn find_node_mut(&mut self, node_id: &str) -> Option<&mut DagNode> {
        self.nodes.iter_mut().find(|n| n.id == node_id)
    }

    /// Check if adding an edge would create a cycle.
    pub fn would_create_cycle(&self, from: &str, to: &str) -> bool {
        // DFS from 'to' to see if we can reach 'from' (which would mean a cycle)
        let mut visited = std::collections::HashSet::new();
        let mut stack = vec![to.to_string()];
        while let Some(current) = stack.pop() {
            if current == from {
                return true;
            }
            if !visited.insert(current.clone()) {
                continue;
            }
            for edge in &self.edges {
                if edge.from == current {
                    stack.push(edge.to.clone());
                }
            }
        }
        false
    }

    /// Get all edges originating from a node.
    pub fn outgoing_edges(&self, node_id: &str) -> Vec<&DagEdge> {
        self.edges.iter().filter(|e| e.from == node_id).collect()
    }

    /// Get all edges pointing to a node.
    pub fn incoming_edges(&self, node_id: &str) -> Vec<&DagEdge> {
        self.edges.iter().filter(|e| e.to == node_id).collect()
    }

    /// Check that all node dependencies reference valid node IDs.
    pub fn validate_dependencies(&self) -> Result<(), String> {
        let node_ids: std::collections::HashSet<&str> =
            self.nodes.iter().map(|n| n.id.as_str()).collect();
        for node in &self.nodes {
            for dep in &node.dependencies {
                if !node_ids.contains(dep.as_str()) {
                    return Err(format!(
                        "node '{}' depends on unknown node '{}'",
                        node.id, dep
                    ));
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DagStatus {
    Created,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl std::fmt::Display for DagStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DagStatus::Created => write!(f, "created"),
            DagStatus::Running => write!(f, "running"),
            DagStatus::Completed => write!(f, "completed"),
            DagStatus::Failed => write!(f, "failed"),
            DagStatus::Cancelled => write!(f, "cancelled"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AssignmentStrategy {
    RoundRobin,
    LeastLoaded,
    CapabilityMatch,
    CostOptimized,
}

impl std::fmt::Display for AssignmentStrategy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AssignmentStrategy::RoundRobin => write!(f, "round-robin"),
            AssignmentStrategy::LeastLoaded => write!(f, "least-loaded"),
            AssignmentStrategy::CapabilityMatch => write!(f, "capability-match"),
            AssignmentStrategy::CostOptimized => write!(f, "cost-optimized"),
        }
    }
}

// ── Task Types ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Pending,
    Ready,
    Assigned,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl std::fmt::Display for TaskStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TaskStatus::Pending => write!(f, "pending"),
            TaskStatus::Ready => write!(f, "ready"),
            TaskStatus::Assigned => write!(f, "assigned"),
            TaskStatus::Running => write!(f, "running"),
            TaskStatus::Completed => write!(f, "completed"),
            TaskStatus::Failed => write!(f, "failed"),
            TaskStatus::Cancelled => write!(f, "cancelled"),
        }
    }
}

/// A task dispatched through the agent network.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub task_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dag_id: Option<String>,
    pub correlation_id: String,
    #[serde(default)]
    pub category: String,
    pub description: String,
    #[serde(default)]
    pub required_capability: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub assigned_agent: Option<String>,
    pub status: TaskStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<TaskResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub created_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub assigned_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
}

impl Task {
    pub fn new(task_id: String, description: String, required_capability: String) -> Self {
        Self {
            task_id,
            parent_task_id: None,
            dag_id: None,
            correlation_id: Uuid::new_v4().to_string(),
            category: String::new(),
            description,
            required_capability,
            assigned_agent: None,
            status: TaskStatus::Pending,
            input: None,
            result: None,
            error: None,
            created_at: Utc::now(),
            assigned_at: None,
            completed_at: None,
        }
    }
}

/// Result of a completed task.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskResult {
    pub agent_id: String,
    pub output: serde_json::Value,
    #[serde(default)]
    pub metrics: TaskMetrics,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TaskMetrics {
    pub wall_time_ms: u64,
    pub tokens_used: u64,
    pub tool_calls: u32,
}

// ── Agent Spawn ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSpawn {
    pub spawn_id: String,
    pub parent_id: String,
    pub child_id: String,
    pub child_name: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    pub status: SpawnStatus,
    pub created_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
    pub ttl_seconds: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SpawnStatus {
    Created,
    Running,
    Completed,
    Destroyed,
}

// ── Shared Memory ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SharedMemoryEntry {
    pub id: String,
    pub agent_id: String,
    pub content: String,
    #[serde(default)]
    pub intent: String,
    #[serde(default)]
    pub target: String,
    pub timestamp: DateTime<Utc>,
}

// ── Stats ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AgentNetStats {
    pub total_agents: u32,
    pub active_agents: u32,
    pub agents_by_status: std::collections::HashMap<String, u32>,
    pub total_tasks: u32,
    pub tasks_by_status: std::collections::HashMap<String, u32>,
    pub active_dags: u32,
    pub active_spawns: u32,
    pub memory_entries: u32,
    pub avg_quality_score: f64,
}

// ── Config ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentNetConfig {
    /// Maximum number of concurrent DAGs the engine manages.
    pub max_concurrent_dags: usize,
    /// Maximum nodes per DAG (safety limit).
    pub max_nodes_per_dag: usize,
    /// Default task assignment strategy.
    pub default_strategy: AssignmentStrategy,
    /// How often the engine refreshes agent capability cache (seconds).
    pub capability_refresh_secs: u64,
    /// How often stats are emitted (seconds).
    pub stats_tick_secs: u64,
}

impl Default for AgentNetConfig {
    fn default() -> Self {
        Self {
            max_concurrent_dags: 100,
            max_nodes_per_dag: 500,
            default_strategy: AssignmentStrategy::CapabilityMatch,
            capability_refresh_secs: 30,
            stats_tick_secs: 30,
        }
    }
}
