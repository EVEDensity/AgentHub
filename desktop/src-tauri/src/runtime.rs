use crate::protocol::{RuntimeReadiness, RuntimeSnapshot, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

const SIDECAR_FILE_NAME: &str = "agenthub-runtime.exe";

#[derive(Clone, Debug)]
pub struct RuntimeLaunchSpec {
    executable: PathBuf,
    args: Vec<String>,
}

impl RuntimeLaunchSpec {
    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self {
            executable: resource_dir.join(SIDECAR_FILE_NAME),
            args: Vec::new(),
        }
    }

    #[cfg(test)]
    fn new(executable: PathBuf, args: Vec<String>) -> Self {
        Self { executable, args }
    }
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
        match process.try_wait() {
            Ok(None) => RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Running,
                readiness: RuntimeReadiness::Probing,
                process_id: Some(process.id()),
                exit_code: None,
                detail: "Local Runtime process is active; readiness is being probed.".into(),
            },
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

impl Default for LocalRuntime {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::{LocalRuntime, RuntimeLaunchSpec, RuntimeState};
    use crate::protocol::{RuntimeReadiness, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::Mutex;

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
        ));
        let started = runtime.start(true);
        assert_eq!(started.status, RuntimeStatus::Starting);
        assert!(started.process_id.is_some());
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
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
