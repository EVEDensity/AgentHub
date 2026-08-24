use crate::protocol::{RuntimeReadiness, RuntimeSnapshot, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use url::Url;

const SIDECAR_FILE_NAME: &str = "agenthub-runtime.exe";
const RUNTIME_HEALTH_ENDPOINT: &str = "http://127.0.0.1:18097/readyz";
const HEALTH_CONNECT_TIMEOUT: Duration = Duration::from_millis(250);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_HEALTH_RESPONSE_BYTES: u64 = 8 * 1024;

#[derive(Clone, Debug)]
pub struct RuntimeLaunchSpec {
    executable: PathBuf,
    health_endpoint: Url,
    #[cfg(test)]
    override_args: Option<Vec<String>>,
}

impl RuntimeLaunchSpec {
    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self {
            executable: resource_dir.join(SIDECAR_FILE_NAME),
            health_endpoint: Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("valid health endpoint"),
            #[cfg(test)]
            override_args: None,
        }
    }

    fn resolved_launch_args(
        &self,
        artifact_directory: Option<&str>,
    ) -> (Vec<String>, bool) {
        #[cfg(test)]
        if let Some(args) = &self.override_args {
            return (args.clone(), false);
        }

        let mut args = vec![
            "--health-endpoint".into(),
            self.health_endpoint.to_string(),
        ];
        let require_artifact_root =
            if let Some(path) = artifact_directory.filter(|value| !value.is_empty()) {
                args.push("--artifact-root".into());
                args.push(path.to_owned());
                true
            } else {
                false
            };
        (args, require_artifact_root)
    }

    #[cfg(test)]
    fn with_test_args(
        executable: PathBuf,
        override_args: Vec<String>,
        health_endpoint: Url,
    ) -> Self {
        Self {
            executable,
            health_endpoint,
            override_args: Some(override_args),
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    protocol_version: u16,
    status: String,
    artifact_root_status: Option<String>,
}

struct RuntimeState {
    child: Option<Child>,
    failure: Option<String>,
    started_at: Option<Instant>,
    require_artifact_root: bool,
}

enum ProbeOutcome {
    Ready,
    Probing,
    Unhealthy,
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
                started_at: None,
                require_artifact_root: false,
            }),
            launch_spec: None,
        }
    }

    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self {
            state: Mutex::new(RuntimeState {
                child: None,
                failure: None,
                started_at: None,
                require_artifact_root: false,
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
                let (readiness, detail) = self.probe_readiness();
                if readiness != RuntimeReadiness::Ready
                    && state
                        .started_at
                        .is_some_and(|started_at| started_at.elapsed() >= STARTUP_TIMEOUT)
                {
                    let mut process = state.child.take().expect("runtime child exists");
                    let _ = process.kill();
                    let _ = process.wait();
                    let detail =
                        "Local Runtime did not become ready within 15 seconds and was stopped."
                            .to_owned();
                    state.failure = Some(detail.clone());
                    state.started_at = None;
                    return Self::failed_snapshot(&detail);
                }
                RuntimeSnapshot {
                    protocol_version: RUNTIME_PROTOCOL_VERSION,
                    status: if readiness == RuntimeReadiness::Ready {
                        RuntimeStatus::Running
                    } else if state.started_at.is_some() {
                        RuntimeStatus::Starting
                    } else {
                        RuntimeStatus::Running
                    },
                    readiness,
                    process_id: Some(process_id),
                    exit_code: None,
                    detail,
                }
            }
            Ok(Some(exit_status)) => {
                state.child = None;
                state.started_at = None;
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

    pub fn start(
        &self,
        configuration_ready: bool,
        artifact_directory: Option<&str>,
    ) -> RuntimeSnapshot {
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
        if !is_port_available(&spec.health_endpoint) {
            let port = spec
                .health_endpoint
                .port_or_known_default()
                .unwrap_or_default();
            return self.record_failure(&format!(
                "Local Runtime port {port} is already in use. Stop the existing process before starting AgentHub."
            ));
        }
        let (args, require_artifact_root) = spec.resolved_launch_args(artifact_directory);
        match Command::new(&spec.executable).args(&args).spawn() {
            Ok(child) => {
                let process_id = child.id();
                let mut state = self.state.lock().expect("local Runtime lock poisoned");
                state.child = Some(child);
                state.failure = None;
                state.started_at = Some(Instant::now());
                state.require_artifact_root = require_artifact_root;
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
            state.started_at = None;
            return Self::stopped_snapshot();
        };
        state.failure = None;
        state.started_at = None;
        state.require_artifact_root = false;
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

        let require_artifact_root = self
            .state
            .lock()
            .expect("local Runtime lock poisoned")
            .require_artifact_root;

        match probe_endpoint(&spec.health_endpoint, require_artifact_root) {
            ProbeOutcome::Ready => (
                RuntimeReadiness::Ready,
                if require_artifact_root {
                    "Local Runtime bootstrap and artifact root are ready for lifecycle supervision."
                        .into()
                } else {
                    "Local Runtime bootstrap is ready for lifecycle supervision.".into()
                },
            ),
            ProbeOutcome::Probing => (
                RuntimeReadiness::Probing,
                "Local Runtime process is active; readiness probe is pending.".into(),
            ),
            ProbeOutcome::Unhealthy => (
                RuntimeReadiness::Unhealthy,
                if require_artifact_root {
                    "Local Runtime readiness or artifact root status is invalid.".into()
                } else {
                    "Local Runtime readiness response is invalid.".into()
                },
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
                started_at: None,
                require_artifact_root: false,
            }),
            launch_spec: None,
        }
    }
}

fn probe_endpoint(endpoint: &Url, require_artifact_root: bool) -> ProbeOutcome {
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
    parse_health_response(&response, require_artifact_root)
}

fn is_port_available(endpoint: &Url) -> bool {
    let Some(port) = endpoint.port_or_known_default() else {
        return false;
    };
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn parse_health_response(response: &[u8], require_artifact_root: bool) -> ProbeOutcome {
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
        "ready" => {
            if require_artifact_root
                && health.artifact_root_status.as_deref() != Some("ready")
            {
                return ProbeOutcome::Unhealthy;
            }
            ProbeOutcome::Ready
        }
        "starting" => ProbeOutcome::Probing,
        _ => ProbeOutcome::Unhealthy,
    }
}

impl Default for LocalRuntime {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for LocalRuntime {
    fn drop(&mut self) {
        let Ok(state) = self.state.get_mut() else {
            return;
        };
        let Some(mut process) = state.child.take() else {
            return;
        };
        if process.try_wait().ok().flatten().is_none() {
            let _ = process.kill();
        }
        let _ = process.wait();
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
                started_at: None,
                require_artifact_root: false,
            }),
            launch_spec: Some(spec),
        }
    }

    #[test]
    fn unconfigured_start_fails_without_claiming_runtime_is_running() {
        let runtime = LocalRuntime::new();
        let snapshot = runtime.start(false, None);
        assert_eq!(snapshot.protocol_version, RUNTIME_PROTOCOL_VERSION);
        assert_eq!(snapshot.status, RuntimeStatus::ConfigurationRequired);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unknown);
        assert_eq!(runtime.snapshot().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn configured_start_fails_closed_when_sidecar_is_missing() {
        let runtime = runtime(RuntimeLaunchSpec::with_test_args(
            std::env::temp_dir().join("agenthub-missing-runtime.exe"),
            Vec::new(),
            Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("health endpoint"),
        ));
        let snapshot = runtime.start(true, None);
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
        let runtime = runtime(RuntimeLaunchSpec::with_test_args(
            executable,
            vec!["/C".into(), "ping -n 3 127.0.0.1 > NUL".into()],
            Url::parse(RUNTIME_HEALTH_ENDPOINT).expect("health endpoint"),
        ));
        let started = runtime.start(true, None);
        assert_eq!(started.status, RuntimeStatus::Starting);
        assert!(started.process_id.is_some());
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn configured_start_fails_when_runtime_port_is_occupied() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("health port binds");
        let port = listener.local_addr().expect("listener address").port();
        let executable = std::env::var_os("COMSPEC")
            .map(PathBuf::from)
            .expect("Windows command processor path");
        let runtime = runtime(RuntimeLaunchSpec::with_test_args(
            executable,
            vec!["/C".into(), "exit 0".into()],
            Url::parse(&format!("http://127.0.0.1:{port}/readyz")).expect("health endpoint"),
        ));
        let snapshot = runtime.start(true, None);
        assert_eq!(snapshot.status, RuntimeStatus::Failed);
        assert!(snapshot.detail.contains("already in use"));
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
            super::parse_health_response(response.as_bytes(), false),
            super::ProbeOutcome::Ready
        ));

        let incompatible = response.replace("\"protocolVersion\":1", "\"protocolVersion\":2");
        assert!(matches!(
            super::parse_health_response(incompatible.as_bytes(), false),
            super::ProbeOutcome::Unhealthy
        ));
    }

    #[test]
    fn ready_health_response_requires_artifact_root_when_configured() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: application/json\r\n",
            "\r\n",
            "{\"protocolVersion\":1,\"status\":\"ready\",\"artifactRootStatus\":\"unavailable\"}"
        );
        assert!(matches!(
            super::parse_health_response(response.as_bytes(), true),
            super::ProbeOutcome::Unhealthy
        ));
        assert!(matches!(
            super::parse_health_response(response.as_bytes(), false),
            super::ProbeOutcome::Ready
        ));

        let ready = response.replace("\"unavailable\"", "\"ready\"");
        assert!(matches!(
            super::parse_health_response(ready.as_bytes(), true),
            super::ProbeOutcome::Ready
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
            super::probe_endpoint(&endpoint, false),
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

    #[test]
    fn dropping_runtime_reaps_a_running_child() {
        let child = Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 > NUL"])
            .spawn()
            .expect("test child starts");
        let process_id = child.id();
        {
            let runtime = LocalRuntime::with_child(child);
            assert_eq!(runtime.snapshot().status, RuntimeStatus::Running);
        }
        let probe = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {process_id}"), "/NH"])
            .output()
            .expect("tasklist runs");
        let output = String::from_utf8_lossy(&probe.stdout);
        assert!(!output.contains(&process_id.to_string()));
    }
}
