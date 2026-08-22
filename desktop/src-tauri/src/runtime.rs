use crate::protocol::{RuntimeReadiness, RuntimeSnapshot, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
use std::process::Child;
use std::sync::Mutex;

pub struct LocalRuntime {
    child: Mutex<Option<Child>>,
}

impl LocalRuntime {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
        }
    }

    pub fn snapshot(&self) -> RuntimeSnapshot {
        let mut child = self.child.lock().expect("local Runtime lock poisoned");
        let Some(process) = child.as_mut() else {
            return RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Stopped,
                readiness: RuntimeReadiness::Unknown,
                process_id: None,
                exit_code: None,
                detail: "Local Runtime has not been started.".into(),
            };
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
                *child = None;
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

    pub fn start(&self) -> RuntimeSnapshot {
        let current = self.snapshot();
        if current.status == RuntimeStatus::Running {
            return current;
        }

        RuntimeSnapshot {
            protocol_version: RUNTIME_PROTOCOL_VERSION,
            status: RuntimeStatus::ConfigurationRequired,
            readiness: RuntimeReadiness::Unknown,
            process_id: None,
            exit_code: None,
            detail: "Runtime onboarding is required before AgentHub can start local services."
                .into(),
        }
    }

    pub fn stop(&self) -> RuntimeSnapshot {
        let mut child = self.child.lock().expect("local Runtime lock poisoned");
        let Some(mut process) = child.take() else {
            return RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Stopped,
                readiness: RuntimeReadiness::Unknown,
                process_id: None,
                exit_code: None,
                detail: "Local Runtime is already stopped.".into(),
            };
        };

        match process.kill().and_then(|_| process.wait()) {
            Ok(_) => RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Stopped,
                readiness: RuntimeReadiness::Unknown,
                process_id: None,
                exit_code: None,
                detail: "Local Runtime stopped.".into(),
            },
            Err(error) => RuntimeSnapshot {
                protocol_version: RUNTIME_PROTOCOL_VERSION,
                status: RuntimeStatus::Stopped,
                readiness: RuntimeReadiness::Unhealthy,
                process_id: None,
                exit_code: None,
                detail: format!("Unable to stop Local Runtime: {error}."),
            },
        }
    }

    #[cfg(test)]
    fn with_child(child: Child) -> Self {
        Self {
            child: Mutex::new(Some(child)),
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
    use super::LocalRuntime;
    use crate::protocol::{RuntimeReadiness, RuntimeStatus, RUNTIME_PROTOCOL_VERSION};
    use std::process::Command;

    #[test]
    fn unconfigured_start_fails_without_claiming_runtime_is_running() {
        let runtime = LocalRuntime::new();

        let snapshot = runtime.start();
        assert_eq!(snapshot.protocol_version, RUNTIME_PROTOCOL_VERSION);
        assert_eq!(snapshot.status, RuntimeStatus::ConfigurationRequired);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unknown);
        assert_eq!(runtime.snapshot().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn running_child_is_reported_as_running() {
        let child = if cfg!(windows) {
            Command::new("cmd")
                .args(["/C", "ping -n 3 127.0.0.1 > NUL"])
                .spawn()
                .expect("test child starts")
        } else {
            Command::new("sh")
                .args(["-c", "sleep 1"])
                .spawn()
                .expect("test child starts")
        };
        let runtime = LocalRuntime::with_child(child);

        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.status, RuntimeStatus::Running);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Probing);
        assert!(snapshot.process_id.is_some());
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
    }

    #[test]
    fn exited_child_reports_exit_code_and_unhealthy_readiness() {
        let child = if cfg!(windows) {
            Command::new("cmd")
                .args(["/C", "exit 7"])
                .spawn()
                .expect("test child starts")
        } else {
            Command::new("sh")
                .args(["-c", "exit 7"])
                .spawn()
                .expect("test child starts")
        };
        let runtime = LocalRuntime::with_child(child);
        std::thread::sleep(std::time::Duration::from_millis(50));

        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.status, RuntimeStatus::Stopped);
        assert_eq!(snapshot.readiness, RuntimeReadiness::Unhealthy);
        assert_eq!(snapshot.exit_code, Some(7));
    }
}
