use serde::Serialize;

pub const RUNTIME_PROTOCOL_VERSION: u16 = 1;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[allow(dead_code)]
pub enum RuntimeStatus {
    Stopped,
    Starting,
    Running,
    ConfigurationRequired,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[allow(dead_code)]
pub enum RuntimeReadiness {
    Unknown,
    Probing,
    Ready,
    Unhealthy,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeSnapshot {
    pub protocol_version: u16,
    pub status: RuntimeStatus,
    pub readiness: RuntimeReadiness,
    pub process_id: Option<u32>,
    pub exit_code: Option<i32>,
    pub detail: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ControlPlaneReachability {
    NotConfigured,
    Unreachable,
    Unauthorized,
    Unhealthy,
    Reachable,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ControlPlaneSnapshot {
    pub reachability: ControlPlaneReachability,
    pub endpoint_configured: bool,
    pub detail: String,
}
