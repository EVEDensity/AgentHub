//! NATS adapter for AgentNet — subscribes to agentnet subjects and routes events
//! to the DAG engine and agent registry.
//!
//! Subjects:
//! - `agenthub.agentnet.capabilities` — agent capability announcements
//! - `agenthub.agentnet.tasks` — task publish/complete events
//! - `agenthub.agentnet.results` — task result reporting
//! - `agenthub.agentnet.spawn` — agent spawn requests/completions
//! - `agenthub.agentnet.memory` — shared memory messages
//! - `agenthub.agentnet.heartbeat` — agent heartbeats

use std::sync::Arc;
use std::time::Duration;

use async_nats::{self, jetstream};
use futures::StreamExt;
use platform_events::EventEnvelope;
use tracing;

use crate::core::{AgentRegistry, DagEngine};
use crate::types::{AgentCapability, AgentStatus};

// ── NATS Subject Constants ─────────────────────────────────────────────

pub const SUBJECT_CAPABILITIES: &str = "agenthub.agentnet.capabilities";
pub const SUBJECT_TASKS: &str = "agenthub.agentnet.tasks";
pub const SUBJECT_RESULTS: &str = "agenthub.agentnet.results";
pub const SUBJECT_SPAWN: &str = "agenthub.agentnet.spawn";
pub const SUBJECT_MEMORY: &str = "agenthub.agentnet.memory";
pub const SUBJECT_HEARTBEAT: &str = "agenthub.agentnet.heartbeat";

/// NATS adapter that bridges AgentNet events to the DAG engine and agent registry.
pub struct NatsAgentNetAdapter {
    client: Option<async_nats::Client>,
    jetstream: Option<jetstream::Context>,
}

impl NatsAgentNetAdapter {
    pub fn new() -> Self {
        Self {
            client: None,
            jetstream: None,
        }
    }

    /// Connect to NATS and initialize JetStream context.
    pub async fn connect(&mut self, url: &str) -> Result<(), Box<dyn std::error::Error>> {
        let client = async_nats::connect(url).await?;
        let js = jetstream::new(client.clone());
        self.client = Some(client);
        self.jetstream = Some(js);
        tracing::info!(url = %url, "NatsAgentNetAdapter connected");
        Ok(())
    }

    /// Check if NATS is connected.
    pub fn is_connected(&self) -> bool {
        self.client.is_some()
    }

    /// Publish an envelope to a NATS subject.
    pub async fn publish(
        &self,
        subject: &str,
        envelope: &EventEnvelope,
    ) -> Result<(), Box<dyn std::error::Error>> {
        if let Some(client) = &self.client {
            let payload = serde_json::to_vec(envelope)?;
            client.publish(subject.to_string(), payload.into()).await?;
            tracing::debug!(subject = %subject, event_id = %envelope.event_id, "published");
        }
        Ok(())
    }

    /// Subscribe to agent capability announcements and update the registry.
    pub async fn spawn_capability_subscription(
        self: Arc<Self>,
        registry: Arc<AgentRegistry>,
    ) -> Result<tokio::task::JoinHandle<()>, Box<dyn std::error::Error>> {
        let client = self
            .client
            .clone()
            .ok_or("NATS not connected")?;

        let handle = tokio::spawn(async move {
            let mut sub = match client.subscribe(SUBJECT_CAPABILITIES.to_string()).await {
                Ok(s) => s,
                Err(e) => {
                    tracing::error!(error = %e, "failed to subscribe to capabilities");
                    return;
                }
            };

            tracing::info!(subject = SUBJECT_CAPABILITIES, "listening for capability announcements");

            while let Some(msg) = sub.next().await {
                match serde_json::from_slice::<EventEnvelope>(&msg.payload) {
                    Ok(envelope) => {
                        if let Some(payload) = envelope.payload {
                            if let Ok(cap) = serde_json::from_value::<AgentCapability>(payload) {
                                tracing::debug!(
                                    agent_id = %cap.agent_id,
                                    capabilities = ?cap.capabilities,
                                    "capability received"
                                );
                                registry.upsert(cap).await;
                            }
                        }
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "failed to deserialize capability envelope");
                    }
                }
            }
        });

        Ok(handle)
    }

    /// Subscribe to heartbeat messages and update agent liveness.
    pub async fn spawn_heartbeat_subscription(
        self: Arc<Self>,
        registry: Arc<AgentRegistry>,
    ) -> Result<tokio::task::JoinHandle<()>, Box<dyn std::error::Error>> {
        let client = self
            .client
            .clone()
            .ok_or("NATS not connected")?;

        let handle = tokio::spawn(async move {
            let mut sub = match client.subscribe(SUBJECT_HEARTBEAT.to_string()).await {
                Ok(s) => s,
                Err(e) => {
                    tracing::error!(error = %e, "failed to subscribe to heartbeat");
                    return;
                }
            };

            while let Some(msg) = sub.next().await {
                match serde_json::from_slice::<EventEnvelope>(&msg.payload) {
                    Ok(envelope) => {
                        if let Some(payload) = envelope.payload {
                            let agent_id = payload
                                .get("agent_id")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            let status_str = payload
                                .get("status")
                                .and_then(|v| v.as_str())
                                .unwrap_or("idle");
                            let load = payload
                                .get("current_load")
                                .and_then(|v| v.as_i64())
                                .unwrap_or(0) as i32;

                            let status = match status_str {
                                "busy" => AgentStatus::Busy,
                                "overloaded" => AgentStatus::Overloaded,
                                "offline" => AgentStatus::Offline,
                                _ => AgentStatus::Idle,
                            };

                            registry.heartbeat(agent_id, load, status).await;
                        }
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "failed to deserialize heartbeat envelope");
                    }
                }
            }
        });

        Ok(handle)
    }

    /// Subscribe to shared memory messages (emergent agent communication).
    pub async fn spawn_memory_subscription(
        self: Arc<Self>,
    ) -> Result<tokio::task::JoinHandle<()>, Box<dyn std::error::Error>> {
        let client = self
            .client
            .clone()
            .ok_or("NATS not connected")?;

        let handle = tokio::spawn(async move {
            let mut sub = match client.subscribe(SUBJECT_MEMORY.to_string()).await {
                Ok(s) => s,
                Err(e) => {
                    tracing::error!(error = %e, "failed to subscribe to memory");
                    return;
                }
            };

            tracing::info!(subject = SUBJECT_MEMORY, "listening for shared memory messages");

            while let Some(msg) = sub.next().await {
                match serde_json::from_slice::<EventEnvelope>(&msg.payload) {
                    Ok(envelope) => {
                        if let Some(payload) = &envelope.payload {
                            let agent_id = payload
                                .get("agent_id")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let intent = payload
                                .get("intent")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            tracing::debug!(
                                agent_id = %agent_id,
                                intent = %intent,
                                "shared memory message"
                            );
                        }
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "failed to deserialize memory envelope");
                    }
                }
            }
        });

        Ok(handle)
    }
}

impl Default for NatsAgentNetAdapter {
    fn default() -> Self {
        Self::new()
    }
}

/// Retry NATS connection with backoff.
pub async fn retry_connect_nats(
    url: &str,
    max_attempts: u32,
    delay: Duration,
) -> Result<NatsAgentNetAdapter, Box<dyn std::error::Error>> {
    let mut last_err: Option<Box<dyn std::error::Error>> = None;
    for attempt in 1..=max_attempts {
        let mut adapter = NatsAgentNetAdapter::new();
        match adapter.connect(url).await {
            Ok(()) => {
                tracing::info!(attempt, "connected to NATS");
                return Ok(adapter);
            }
            Err(e) => {
                tracing::warn!(attempt, error = %e, "NATS connect failed, retrying");
                last_err = Some(e);
                tokio::time::sleep(delay).await;
            }
        }
    }
    Err(last_err.unwrap_or_else(|| "unknown NATS connect error".into()))
}
