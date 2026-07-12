use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Priority {
    Low,
    Normal,
    High,
    Critical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Producer {
    pub service: String,
    pub instance: String,
    pub region: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Routing {
    pub channel: Option<String>,
    pub partition_key: Option<String>,
    pub priority: Option<Priority>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventEnvelope {
    pub event_id: String,
    pub event_type: String,
    pub event_version: i32,
    pub occurred_at: DateTime<Utc>,
    pub trace_id: String,
    pub tenant_id: String,
    pub session_id: String,
    pub message_id: Option<String>,
    pub actor_id: Option<String>,
    pub producer: Producer,
    pub routing: Option<Routing>,
    pub payload: BTreeMap<String, Value>,
}
