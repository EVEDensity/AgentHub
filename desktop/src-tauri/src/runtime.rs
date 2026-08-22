use crate::protocol::{RuntimeReadiness, RuntimeSnapshot, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use url::Url;

const SIDECAR_FILE_NAME: &str = "agenthub-runtime.exe";
const RUNTIME_HEALTH_ENDPOINT: &str = "http://127.0.0.1:18097/readyz";
const HEALTH_CONNECT_TIMEOUT: Duration = Duration::from_millis(250);
const MAX_HEALTH_RESPONSE_BYTES: u64 = 8 * 1024;

#[derive(Clone, Debug)]
pub struct RuntimeLaunchSpec {
    executable: PathBuf,
    args: Vec<String>,
    health_endpoint: Url,
}

impl RuntimeLaunchSpec {
    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self {
            executable: resource_dir.join(SIDECAR_FILE_NAME),
            args: vec!["--health-endpoint".into(), RUNTIME_HEALTH_ENDPOINT.into()],
            health_endpoint: Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("valid health endpoint"),
        }
    }

    #[cfg(test)]
    fn new(executable: PathBuf, args: Vec<String>, health_endpoint: Url) -> Self {
        Self {
            executable,
            args,
            health_endpoint,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    protocol_version: u16,
    status: String,
}

enum ProbeOutcome {
    Ready,
    Probing,
    Unhealthy,
}

struct RuntimeState {
    child: Option<Child>,
    failure: Option<String>,
}

pub struct LocalRuntime {
    state: Mutex<RuntimeState>,
    launch_spec: Option<RuntimeLaunchSpec>,
}

impl LocalRuntime {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(RuntimeState {
                child: None,
                failure: None,
            }),
            launch_spec: None,
        }
    }

    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self {
            state: Mutex::new(RuntimeState {
                child: None,
                failure: None,
            }),
            launch_spec: Some(RuntimeLaunchSpec::from_resource_dir(resource_dir)),
        }
    }

    pub fn snapshot(&self) -> RuntimeSnapshot {
        let mut state = self.state.lock().expect("local Runtime lock poisoned");
        let Some(process) = state.child.as_mut() else {
            return state
                .failure
                .as_deref()
                .map_or_else(Self::stopped_snapshot, Self::failed_snapshot);
        };
        let process_id = process.id();
        match process.try_wait() {
            Ok(None) => {
                drop(state);
                let (readiness, detail) = self.probe_readiness();
                RuntimeSnapshot {
                    protocol_version: RUNTIME_PROTOCOL_VERSION,
                    status: RuntimeStatus::Running,
                    readiness,
                    process_id: Some(process_id),
                    exit_code: None,
                    detail,
                }
            }
            Ok(Some(exit_status)) => {
                state.child = None;
                RuntimeSnapshot {
                    protocol_version: RUNTIME_PROTOCOL_VERSION,
                    status: RuntimeStatus::Stopped,
                    readiness: RuntimeReadiness::Unhealthy,
                    process_id: None,
                    exit_code: exit_status.code(),
                    detail: format!("Local Runtime exited with {exit_status}."),
                }
            }
            Err(error) => RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Stopped,
                readiness: RuntimeReadiness::Unhealthy,
                process_id: None,
                exit_code: None,
                detail: format!("Unable to inspect Local Runtime: {error}."),
            },
        }
    }

    pub fn start(&self, configuration_ready: bool) -> RuntimeSnapshot {
        let current = self.snapshot();
        if matches!(
            current.status,
            RuntimeStatus::Running | RuntimeStatus::Starting
        ) {
            return current;
        }
        self.clear_failure();
        if !configuration_ready {
            return Self::configuration_required_snapshot();
        }
        let Some(spec) = self.launch_spec.as_ref() else {
            return self.record_failure("Local Runtime sidecar is not available in this build.");
        };
        if !spec.executable.is_file() {
            return self.record_failure("Local Runtime sidecar is not installed.");
        }
        match Command::new(&spec.executable).args(&spec.args).spawn() {
            Ok(child) => {
                let process_id = child.id();
                let mut state = self.state.lock().expect("local Runtime lock poisoned");
                state.child = Some(child);
                state.failure = None;
                RuntimeSnapshot {
                    protocol_version: RUNTIME_PROTOCOL_VERSION,
                    status: RuntimeStatus::Starting,
                    readiness: RuntimeReadiness::Probing,
                    process_id: Some(process_id),
                    exit_code: None,
                    detail: "Local Runtime sidecar is starting; readiness is being probed.".into(),
                }
            }
            Err(error) => {
                self.record_failure(&format!("Unable to start Local Runtime sidecar: {error}."))
            }
        }
    }

    pub fn stop(&self) -> RuntimeSnapshot {
        let mut state = self.state.lock().expect("local Runtime lock poisoned");
        let Some(mut process) = state.child.take() else {
            state.failure = None;
            return Self::stopped_snapshot();
        };
        state.failure = None;
        match process.kill().and_then(|_| process.wait()) {
            Ok(_) => Self::stopped_snapshot(),
            Err(error) => RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Failed,
                readiness: RuntimeReadiness::Unhealthy,
                process_id: None,
                exit_code: None,
                detail: format!("Unable to stop Local Runtime: {error}."),
            },
        }
    }

    fn probe_readiness(&self) -> (RuntimeReadiness, String) {
        let Some(spec) = self.launch_spec.as_ref() else {
            return (
                RuntimeReadiness::Probing,
                "Local Runtime process is active; readiness is being probed.".into(),
            );
        };

        match probe_endpoint(&spec.health_endpoint) {
            ProbeOutcome::Ready => (
                RuntimeReadiness::Ready,
                "Local Runtime bootstrap is ready for lifecycle supervision.".into(),
            ),
            ProbeOutcome::Probing => (
                RuntimeReadiness::Probing,
                "Local Runtime process is active; readiness probe is pending.".into(),
            ),
            ProbeOutcome::Unhealthy => (
                RuntimeReadiness::Unhealthy,
                "Local Runtime readiness response is invalid.".into(),
            ),
        }
    }

    fn clear_failure(&self) {
        self.state
            .lock()
            .expect("local Runtime lock poisoned")
            .failure = None;
    }
    fn record_failure(&self, detail: &str) -> RuntimeSnapshot {
        self.state
            .lock()
            .expect("local Runtime lock poisoned")
            .failure = Some(detail.to_owned());
        Self::failed_snapshot(detail)
    }
    fn configuration_required_snapshot() -> RuntimeSnapshot {
        RuntimeSnapshot {
            protocol_version: RUNTIME_PROTOCOL_VERSION,
            status: RuntimeStatus::ConfigurationRequired,
            readiness: RuntimeReadiness::Unknown,
            process_id: None,
            exit_code: None,
            detail: "Runtime configuration is required before AgentHub can start local services."
                .into(),
        }
    }
    fn failed_snapshot(detail: &str) -> RuntimeSnapshot {
        RuntimeSnapshot {
            protocol_version: RUNTIME_PROTOCOL_VERSION,
            status: RuntimeStatus::Failed,
            readiness: RuntimeReadiness::Unhealthy,
            process_id: None,
            exit_code: None,
            detail: detail.to_owned(),
        }
    }
    fn stopped_snapshot() -> RuntimeSnapshot {
        RuntimeSnapshot {
            protocol_version: RUNTIME_PROTOCOL_VERSION,
            status: RuntimeStatus::Stopped,
            readiness: RuntimeReadiness::Unknown,
            process_id: None,
            exit_code: None,
            detail: "Local Runtime is stopped.".into(),
        }
    }

    #[cfg(test)]
    fn with_child(child: Child) -> Self {
        Self {
            state: Mutex::new(RuntimeState {
                child: Some(child),
                failure: None,
            }),
            launch_spec: None,
        }
    }
}

fn probe_endpoint(endpoint: &Url) -> ProbeOutcome {
    if endpoint.scheme() != "http"
        || endpoint.host_str() != Some("127.0.0.1")
        || endpoint.port_or_known_default().is_none()
        || endpoint.path() != "/readyz"
        || endpoint.query().is_some()
        || endpoint.fragment().is_some()
        || !endpoint.username().is_empty()
        || endpoint.password().is_some()
    {
        return ProbeOutcome::Unhealthy;
    }

    let port = endpoint
        .port_or_known_default()
        .expect("validated health endpoint port");
    let address = format!("127.0.0.1:{port}");
    let Some(socket_address) = address
        .to_socket_addrs()
        .ok()
        .and_then(|mut addresses| addresses.next())
    else {
        return ProbeOutcome::Probing;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&socket_address, HEALTH_CONNECT_TIMEOUT) else {
        return ProbeOutcome::Probing;
    };
    let _ = stream.set_read_timeout(Some(HEALTH_CONNECT_TIMEOUT));
    let _ = stream.set_write_timeout(Some(HEALTH_CONNECT_TIMEOUT));
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n",
        endpoint.path()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return ProbeOutcome::Probing;
    }

    let mut response = Vec::new();
    if stream
        .take(MAX_HEALTH_RESPONSE_BYTES)
        .read_to_end(&mut response)
        .is_err()
    {
        return ProbeOutcome::Probing;
    }
    parse_health_response(&response)
}

fn parse_health_response(response: &[u8]) -> ProbeOutcome {
    let Ok(response) = std::str::from_utf8(response) else {
        return ProbeOutcome::Unhealthy;
    };
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return ProbeOutcome::Unhealthy;
    };
    let Some(status_code) = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
    else {
        return ProbeOutcome::Unhealthy;
    };
    if status_code != 200 {
        return ProbeOutcome::Unhealthy;
    }
    let Ok(health) = serde_json::from_str::<HealthResponse>(body) else {
        return ProbeOutcome::Unhealthy;
    };
    if health.protocol_version != RUNTIME_PROTOCOL_VERSION {
        return ProbeOutcome::Unhealthy;
    }
    match health.status.as_str() {
        "ready" => ProbeOutcome::Ready,
        "starting" => ProbeOutcome::Probing,
        _ => ProbeOutcome::Unhealthy,
    }
}

impl Default for LocalRuntime {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::{LocalRuntime, RuntimeLaunchSpec, RuntimeState, RUNTIME_HEALTH_ENDPOINT};
    use crate::protocol::{RuntimeReadiness, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::Mutex;
    use url::Url;

    fn runtime(spec: RuntimeLaunchSpec) -> LocalRuntime {
        LocalRuntime {
            state: Mutex::new(RuntimeState {
                child: None,
                failure: None,
            }),
            launch_spec: Some(spec),
        }
    }

    #[test]
    fn unconfigured_start_fails_without_claiming_runtime_is_running() {
        let runtime = LocalRuntime::new();
        let snapshot = runtime.start(false);
        assert_eq!(snapshot.protocol_version, RUNTIME_PROTOCOL_VERSION);
        assert_eq!(snapshot.status, RuntimeStatus::ConfigurationRequired);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unknown);
        assert_eq!(runtime.snapshot().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn configured_start_fails_closed_when_sidecar_is_missing() {
        let runtime = runtime(RuntimeLaunchSpec::new(
            std::env::temp_dir().join("agenthub-missing-runtime.exe"),
            Vec::new(),
            Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("health endpoint"),
        ));
        let snapshot = runtime.start(true);
        assert_eq!(snapshot.status, RuntimeStatus::Failed);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unhealthy);
        assert!(snapshot.detail.contains("not installed"));
        assert_eq!(runtime.snapshot().status, RuntimeStatus::Failed);
    }

    #[test]
    fn configured_start_tracks_a_spawned_sidecar_until_stopped() {
        let executable = std::env::var_os("COMSPEC")
            .map(PathBuf::from)
            .expect("Windows command processor path");
        let runtime = runtime(RuntimeLaunchSpec::new(
            executable,
            vec!["/C".into(), "ping -n 3 127.0.0.1 > NUL".into()],
            Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("health endpoint"),
        ));
        let started = runtime.start(true);
        assert_eq!(started.status, RuntimeStatus::Starting);
        assert!(started.process_id.is_some());
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn ready_health_response_requires_current_protocol() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: application/json\r\n",
            "\r\n",
            "{\"protocolVersion\":1,\"status\":\"ready\"}"
        );
        assert!(matches!(
            super::parse_health_response(response.as_bytes()),
            super::ProbeOutcome::Ready
        ));

        let incompatible = response.replace("\"protocolVersion\":1", "\"protocolVersion\":2");
        assert!(matches!(
            super::parse_health_response(incompatible.as_bytes()),
            super::ProbeOutcome::Unhealthy
        ));
    }

    #[test]
    fn loopback_probe_accepts_a_ready_sidecar_response() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("health listener binds");
        let port = listener.local_addr().expect("listener address").port();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("health request arrives");
            let mut request = [0_u8; 512];
            let _ = stream.read(&mut request);
            let body = r#"{"protocolVersion":1,"status":"ready"}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("health response writes");
        });
        let endpoint =
            Url::parse(&format!("http://127.0.0.1:{port}/readyz")).expect("health endpoint");
        assert!(matches!(
            super::probe_endpoint(&endpoint),
            super::ProbeOutcome::Ready
        ));
        server.join().expect("health server joins");
    }

    #[test]
    fn running_child_is_reported_as_running() {
        let child = Command::new("cmd")
            .args(["/C", "ping -n 3 127.0.0.1 > NUL"])
            .spawn()
            .expect("test child starts");
        let runtime = LocalRuntime::with_child(child);
        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.status, RuntimeStatus::Running);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Probing);
        assert!(snapshot.process_id.is_some());
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn exited_child_reports_exit_code_and_unhealthy_readiness() {
        let child = Command::new("cmd")
            .args(["/C", "exit 7"])
            .spawn()
            .expect("test child starts");
        let runtime = LocalRuntime::with_child(child);
        std::thread::sleep(std::time::Duration::from_millis(50));
        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.status, RuntimeStatus::Stopped);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unhealthy);
        assert_eq!(snapshot.exit_code, Some(7));
    }
}
