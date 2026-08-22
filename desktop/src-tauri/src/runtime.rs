use serde::Serialize;
use std::process::Child;
use std::sync::Mutex;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeSnapshot {
    pub status: RuntimeStatus,
    pub detail: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeStatus {
    Stopped,
    Running,
    ConfigurationRequired,
}

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
                status: RuntimeStatus::Stopped,
                detail: "Local Runtime has not been started.".into(),
            };
        };

        match process.try_wait() {
            Ok(None) => RuntimeSnapshot {
                status: RuntimeStatus::Running,
                detail: "Local Runtime process is active.".into(),
            },
            Ok(Some(exit_status)) => {
                *child = None;
                RuntimeSnapshot {
                    status: RuntimeStatus::Stopped,
                    detail: format!("Local Runtime exited with {exit_status}."),
                }
            }
            Err(error) => RuntimeSnapshot {
                status: RuntimeStatus::Stopped,
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
            status: RuntimeStatus::ConfigurationRequired,
            detail: "Runtime onboarding is required before AgentHub can start local services."
                .into(),
        }
    }

    pub fn stop(&self) -> RuntimeSnapshot {
        let mut child = self.child.lock().expect("local Runtime lock poisoned");
        let Some(mut process) = child.take() else {
            return RuntimeSnapshot {
                status: RuntimeStatus::Stopped,
                detail: "Local Runtime is already stopped.".into(),
            };
        };

        match process.kill().and_then(|_| process.wait()) {
            Ok(_) => RuntimeSnapshot {
                status: RuntimeStatus::Stopped,
                detail: "Local Runtime stopped.".into(),
            },
            Err(error) => RuntimeSnapshot {
                status: RuntimeStatus::Stopped,
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
    use super::{LocalRuntime, RuntimeStatus};
    use std::process::Command;

    #[test]
    fn unconfigured_start_fails_without_claiming_runtime_is_running() {
        let runtime = LocalRuntime::new();

        assert_eq!(runtime.start().status, RuntimeStatus::ConfigurationRequired);
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

        assert_eq!(runtime.snapshot().status, RuntimeStatus::Running);
        assert_eq!(runtime.stop().status, RuntimeStatus::Stopped);
    }
}
