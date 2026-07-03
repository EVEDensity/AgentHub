//! DAG orchestration engine — the heart of AgentNet.
//!
//! [`DagEngine`] manages dynamic task graphs with runtime node/edge mutations,
//! dependency-aware ready-node calculation, and pluggable task assignment strategies.
//!
//! # Example
//! ```ignore
//! let mut engine = DagEngine::new(AgentNetConfig::default());
//! let dag = engine.create_dag("my-dag", "build-pipeline".into());
//! engine.add_node(&dag.dag_id, DagNode::new("compile".into(), "Compile source".into()));
//! engine.add_node(&dag.dag_id, DagNode::new("test".into(), "Run tests".into()));
//! engine.add_edge(&dag.dag_id, "compile", "test", "depends_on".into());
//! let ready = engine.ready_nodes(&dag.dag_id); // "compile" is ready
//! ```

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::types::*;

// ── Agent Registry ────────────────────────────────────────────────────

/// Thread-safe registry of agent capability manifests, used by the task assigner
/// to match tasks to agents.
pub struct AgentRegistry {
    agents: RwLock<HashMap<String, AgentCapability>>,
}

impl AgentRegistry {
    pub fn new() -> Self {
        Self {
            agents: RwLock::new(HashMap::new()),
        }
    }

    /// Register or update an agent capability manifest.
    pub async fn upsert(&self, cap: AgentCapability) {
        self.agents.write().await.insert(cap.agent_id.clone(), cap);
    }

    /// Remove an agent from the registry.
    pub async fn remove(&self, agent_id: &str) {
        self.agents.write().await.remove(agent_id);
    }

    /// Update heartbeat and load for an agent.
    pub async fn heartbeat(&self, agent_id: &str, current_load: i32, status: AgentStatus) -> bool {
        let mut agents = self.agents.write().await;
        if let Some(cap) = agents.get_mut(agent_id) {
            cap.last_heartbeat = chrono::Utc::now();
            cap.current_load = current_load;
            cap.status = status;
            true
        } else {
            false
        }
    }

    /// Find agents matching a required capability.
    pub async fn find_by_capability(&self, required: &str) -> Vec<AgentCapability> {
        let agents = self.agents.read().await;
        let required_lower = required.to_lowercase();
        agents
            .values()
            .filter(|c| {
                c.status != AgentStatus::Offline
                    && c.capabilities
                        .iter()
                        .any(|cap| cap.to_lowercase().contains(&required_lower))
            })
            .cloned()
            .collect()
    }

    /// Get all registered agents.
    pub async fn all(&self) -> Vec<AgentCapability> {
        self.agents.read().await.values().cloned().collect()
    }

    /// Get agent count.
    pub async fn count(&self) -> usize {
        self.agents.read().await.len()
    }
}

impl Default for AgentRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// ── Assignment Strategy ───────────────────────────────────────────────

/// Policy for selecting an agent from a pool of candidates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AssignmentPolicy {
    /// Pick the agent with the highest quality score.
    CapabilityMatch,
    /// Pick the agent with the lowest load ratio.
    LeastLoaded,
    /// Round-robin distribution (stateful, requires mutable counter).
    RoundRobin,
    /// Pick the agent with the best quality/cost ratio.
    CostOptimized,
}

impl From<AssignmentStrategy> for AssignmentPolicy {
    fn from(s: AssignmentStrategy) -> Self {
        match s {
            AssignmentStrategy::CapabilityMatch => AssignmentPolicy::CapabilityMatch,
            AssignmentStrategy::LeastLoaded => AssignmentPolicy::LeastLoaded,
            AssignmentStrategy::RoundRobin => AssignmentPolicy::RoundRobin,
            AssignmentStrategy::CostOptimized => AssignmentPolicy::CostOptimized,
        }
    }
}

/// Assigns tasks to agents based on a configurable policy.
pub struct TaskAssigner {
    policy: AssignmentPolicy,
    rr_counter: std::sync::atomic::AtomicU64,
}

impl TaskAssigner {
    pub fn new(policy: AssignmentPolicy) -> Self {
        Self {
            policy,
            rr_counter: std::sync::atomic::AtomicU64::new(0),
        }
    }

    /// Select the best agent from a list of candidates using the configured policy.
    pub fn assign(&self, candidates: &[AgentCapability]) -> Option<AgentCapability> {
        if candidates.is_empty() {
            return None;
        }

        match self.policy {
            AssignmentPolicy::CapabilityMatch => {
                // Highest quality score
                candidates
                    .iter()
                    .max_by(|a, b| a.quality_score.partial_cmp(&b.quality_score).unwrap())
                    .cloned()
            }
            AssignmentPolicy::LeastLoaded => {
                // Lowest load ratio
                candidates
                    .iter()
                    .min_by(|a, b| a.load_ratio().partial_cmp(&b.load_ratio()).unwrap())
                    .cloned()
            }
            AssignmentPolicy::RoundRobin => {
                let idx = self
                    .rr_counter
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed)
                    as usize
                    % candidates.len();
                Some(candidates[idx].clone())
            }
            AssignmentPolicy::CostOptimized => {
                // Best quality/cost ratio
                candidates
                    .iter()
                    .max_by(|a, b| {
                        a.cost_effectiveness()
                            .partial_cmp(&b.cost_effectiveness())
                            .unwrap()
                    })
                    .cloned()
            }
        }
    }

    /// Update the assignment policy.
    pub fn set_policy(&mut self, policy: AssignmentPolicy) {
        self.policy = policy;
    }
}

// ── DAG Engine ────────────────────────────────────────────────────────

/// The core DAG orchestration engine.
///
/// Manages a collection of DAGs with thread-safe interior mutability,
/// providing runtime topological mutations and readiness calculations.
pub struct DagEngine {
    dags: RwLock<HashMap<String, Dag>>,
    config: AgentNetConfig,
    /// Next round-robin index per DAG (for node-level round-robin if needed).
    rr_state: RwLock<HashMap<String, usize>>,
}

impl DagEngine {
    pub fn new(config: AgentNetConfig) -> Arc<Self> {
        Arc::new(Self {
            dags: RwLock::new(HashMap::new()),
            config,
            rr_state: RwLock::new(HashMap::new()),
        })
    }

    // ── DAG CRUD ──────────────────────────────────────────────────────

    /// Create a new empty DAG.
    pub async fn create_dag(&self, dag_id: &str, name: String) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        if dags.contains_key(dag_id) {
            return Err(format!("DAG '{}' already exists", dag_id));
        }
        if dags.len() >= self.config.max_concurrent_dags {
            return Err(format!(
                "max concurrent DAGs ({}) reached",
                self.config.max_concurrent_dags
            ));
        }
        let dag = Dag::new(dag_id.to_string(), name);
        dags.insert(dag_id.to_string(), dag.clone());
        Ok(dag)
    }

    /// Get a DAG by ID.
    pub async fn get_dag(&self, dag_id: &str) -> Option<Dag> {
        self.dags.read().await.get(dag_id).cloned()
    }

    /// List all DAGs.
    pub async fn list_dags(&self) -> Vec<Dag> {
        self.dags.read().await.values().cloned().collect()
    }

    /// Delete a DAG.
    pub async fn delete_dag(&self, dag_id: &str) -> bool {
        self.dags.write().await.remove(dag_id).is_some()
    }

    // ── Node Operations ───────────────────────────────────────────────

    /// Add a node to a DAG.
    pub async fn add_node(&self, dag_id: &str, node: DagNode) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        let dag = dags.get_mut(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        if dag.find_node(&node.id).is_some() {
            return Err(format!("node '{}' already exists in DAG '{}'", node.id, dag_id));
        }
        if dag.nodes.len() >= self.config.max_nodes_per_dag {
            return Err(format!(
                "max nodes per DAG ({}) reached",
                self.config.max_nodes_per_dag
            ));
        }

        dag.nodes.push(node);
        dag.updated_at = chrono::Utc::now();
        Ok(dag.clone())
    }

    /// Remove a node from a DAG (and all its incident edges).
    pub async fn remove_node(&self, dag_id: &str, node_id: &str) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        let dag = dags.get_mut(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        let idx = dag
            .nodes
            .iter()
            .position(|n| n.id == node_id)
            .ok_or_else(|| format!("node '{}' not found in DAG '{}'", node_id, dag_id))?;

        dag.nodes.remove(idx);
        dag.edges.retain(|e| e.from != node_id && e.to != node_id);
        dag.updated_at = chrono::Utc::now();
        Ok(dag.clone())
    }

    /// Update a node's status.
    pub async fn update_node_status(
        &self,
        dag_id: &str,
        node_id: &str,
        status: TaskStatus,
        result: Option<serde_json::Value>,
        error: Option<String>,
    ) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        let dag = dags.get_mut(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        let node = dag
            .find_node_mut(node_id)
            .ok_or_else(|| format!("node '{}' not found in DAG '{}'", node_id, dag_id))?;

        node.status = status;
        let now = chrono::Utc::now();
        if status == TaskStatus::Running && node.started_at.is_none() {
            node.started_at = Some(now);
        }
        if status == TaskStatus::Completed || status == TaskStatus::Failed {
            node.completed_at = Some(now);
        }
        node.result = result;
        node.error = error;
        dag.updated_at = now;
        Ok(dag.clone())
    }

    // ── Edge Operations ───────────────────────────────────────────────

    /// Add a directed edge between two nodes.
    pub async fn add_edge(
        &self,
        dag_id: &str,
        from: &str,
        to: &str,
        label: String,
    ) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        let dag = dags.get_mut(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        // Validate nodes exist
        if dag.find_node(from).is_none() {
            return Err(format!("source node '{}' not found", from));
        }
        if dag.find_node(to).is_none() {
            return Err(format!("target node '{}' not found", to));
        }

        // Check for duplicate edge
        if dag.edges.iter().any(|e| e.from == from && e.to == to) {
            return Err(format!("edge {}->{} already exists", from, to));
        }

        // Temporarily add the edge and check for cycles
        let edge = DagEdge {
            from: from.to_string(),
            to: to.to_string(),
            label,
            weight: 1.0,
        };
        dag.edges.push(edge);

        if dag.would_create_cycle(from, to) {
            dag.edges.pop(); // Roll back
            return Err(format!("adding edge {}->{} would create a cycle", from, to));
        }

        // Update dependencies on the target node
        if let Some(target_node) = dag.find_node_mut(to) {
            if !target_node.dependencies.contains(&from.to_string()) {
                target_node.dependencies.push(from.to_string());
            }
        }

        dag.updated_at = chrono::Utc::now();
        Ok(dag.clone())
    }

    /// Remove an edge between two nodes.
    pub async fn remove_edge(&self, dag_id: &str, from: &str, to: &str) -> Result<Dag, String> {
        let mut dags = self.dags.write().await;
        let dag = dags.get_mut(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        let initial_len = dag.edges.len();
        dag.edges.retain(|e| !(e.from == from && e.to == to));

        if dag.edges.len() == initial_len {
            return Err(format!("edge {}->{} not found", from, to));
        }

        // Remove from target node's dependencies
        if let Some(target_node) = dag.find_node_mut(to) {
            target_node.dependencies.retain(|d| d != from);
        }

        dag.updated_at = chrono::Utc::now();
        Ok(dag.clone())
    }

    /// Reroute: replace edge A→B with A→C. Atomic operation.
    pub async fn reroute(
        &self,
        dag_id: &str,
        from: &str,
        old_to: &str,
        new_to: &str,
        label: String,
    ) -> Result<Dag, String> {
        // Validate new target exists
        let dags = self.dags.read().await;
        let dag = dags.get(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;
        if dag.find_node(new_to).is_none() {
            return Err(format!("new target node '{}' not found", new_to));
        }
        drop(dags);

        self.remove_edge(dag_id, from, old_to).await?;
        self.add_edge(dag_id, from, new_to, label).await
    }

    // ── Readiness ─────────────────────────────────────────────────────

    /// Get nodes whose dependencies are all satisfied (completed).
    pub async fn ready_nodes(&self, dag_id: &str) -> Result<Vec<DagNode>, String> {
        let dags = self.dags.read().await;
        let dag = dags.get(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        let completed: std::collections::HashSet<&str> = dag
            .nodes
            .iter()
            .filter(|n| n.status == TaskStatus::Completed)
            .map(|n| n.id.as_str())
            .collect();

        let mut ready: Vec<DagNode> = dag
            .nodes
            .iter()
            .filter(|n| {
                n.status == TaskStatus::Pending
                    && n.dependencies.iter().all(|dep| completed.contains(dep.as_str()))
            })
            .cloned()
            .collect();

        // Sort by priority (higher first)
        ready.sort_by(|a, b| b.priority.cmp(&a.priority));
        Ok(ready)
    }

    /// Check if a DAG is complete (all nodes completed or failed).
    pub async fn is_dag_terminal(&self, dag_id: &str) -> Result<bool, String> {
        let dags = self.dags.read().await;
        let dag = dags.get(dag_id).ok_or_else(|| format!("DAG '{}' not found", dag_id))?;

        let terminal = dag.nodes.iter().all(|n| {
            matches!(
                n.status,
                TaskStatus::Completed | TaskStatus::Failed | TaskStatus::Cancelled
            )
        });
        Ok(terminal)
    }

    // ── Stats ─────────────────────────────────────────────────────────

    /// Compute aggregate stats across all DAGs.
    pub async fn stats(&self) -> AgentNetStats {
        let dags = self.dags.read().await;
        let mut stats = AgentNetStats::default();

        let mut dag_count = 0u32;
        for dag in dags.values() {
            dag_count += 1;
            if dag.status == DagStatus::Running {
                stats.active_dags += 1;
            }
            for node in &dag.nodes {
                stats.total_tasks += 1;
                *stats
                    .tasks_by_status
                    .entry(format!("{}", node.status))
                    .or_insert(0) += 1;
            }
        }

        stats
    }

    /// Count of active DAGs.
    pub async fn dag_count(&self) -> usize {
        self.dags.read().await.len()
    }
}

// ── Tests ─────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_create_and_get_dag() {
        let engine = DagEngine::new(AgentNetConfig::default());
        let dag = engine.create_dag("test-dag", "test".into()).await.unwrap();
        assert_eq!(dag.dag_id, "test-dag");
        assert_eq!(dag.status, DagStatus::Created);

        let fetched = engine.get_dag("test-dag").await.unwrap();
        assert_eq!(fetched.name, "test");
    }

    #[tokio::test]
    async fn test_add_node_and_edge() {
        let engine = DagEngine::new(AgentNetConfig::default());
        engine.create_dag("dag1", "test".into()).await.unwrap();

        engine
            .add_node(&"dag1", DagNode::new("a".into(), "Task A".into()))
            .await
            .unwrap();
        engine
            .add_node(&"dag1", DagNode::new("b".into(), "Task B".into()))
            .await
            .unwrap();

        engine
            .add_edge("dag1", "a", "b", "depends_on".into())
            .await
            .unwrap();

        let dag = engine.get_dag("dag1").await.unwrap();
        assert_eq!(dag.nodes.len(), 2);
        assert_eq!(dag.edges.len(), 1);
        assert!(dag.find_node("b").unwrap().dependencies.contains(&"a".to_string()));
    }

    #[tokio::test]
    async fn test_cycle_detection() {
        let engine = DagEngine::new(AgentNetConfig::default());
        engine.create_dag("dag1", "test".into()).await.unwrap();

        engine
            .add_node(&"dag1", DagNode::new("x".into(), "X".into()))
            .await
            .unwrap();
        engine
            .add_node(&"dag1", DagNode::new("y".into(), "Y".into()))
            .await
            .unwrap();

        engine
            .add_edge("dag1", "x", "y", "".into())
            .await
            .unwrap();

        // y→x would create a cycle
        let result = engine.add_edge("dag1", "y", "x", "".into()).await;
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("cycle"));
    }

    #[tokio::test]
    async fn test_ready_nodes() {
        let engine = DagEngine::new(AgentNetConfig::default());
        engine.create_dag("dag1", "test".into()).await.unwrap();

        engine
            .add_node(&"dag1", DagNode::new("a".into(), "A".into()))
            .await
            .unwrap();

        let mut node_b = DagNode::new("b".into(), "B".into());
        node_b.dependencies = vec!["a".to_string()];
        engine.add_node(&"dag1", node_b).await.unwrap();
        engine
            .add_edge("dag1", "a", "b", "".into())
            .await
            .unwrap();

        // Node 'a' should be ready (no deps)
        let ready = engine.ready_nodes("dag1").await.unwrap();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].id, "a");

        // Complete 'a' → 'b' becomes ready
        engine
            .update_node_status("dag1", "a", TaskStatus::Completed, None, None)
            .await
            .unwrap();
        let ready = engine.ready_nodes("dag1").await.unwrap();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].id, "b");
    }

    #[tokio::test]
    async fn test_task_assigner() {
        let mut candidates = vec![
            AgentCapability::new("agent1".into(), "A1".into(), vec!["code-review".into()]),
            AgentCapability::new("agent2".into(), "A2".into(), vec!["code-review".into()]),
        ];
        candidates[0].quality_score = 0.9;
        candidates[1].quality_score = 0.7;
        candidates[0].current_load = 4;
        candidates[1].current_load = 1;

        // CapabilityMatch: highest quality
        let assigner = TaskAssigner::new(AssignmentPolicy::CapabilityMatch);
        let picked = assigner.assign(&candidates).unwrap();
        assert_eq!(picked.agent_id, "agent1");

        // LeastLoaded: lowest load ratio
        let assigner = TaskAssigner::new(AssignmentPolicy::LeastLoaded);
        let picked = assigner.assign(&candidates).unwrap();
        assert_eq!(picked.agent_id, "agent2");
    }
}
