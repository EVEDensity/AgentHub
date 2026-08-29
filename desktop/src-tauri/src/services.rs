use serde::Serialize;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs, TcpListener};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use url::Url;

const PORT_POOL_START: u16 = 28_000;
const PORT_POOL_END: u16 = 28_999;
const PORTS_PER_INSTANCE: u16 = 5;

#[derive(Clone, Debug)]
pub struct ServiceSpec { pub name: &'static str, pub executable: PathBuf, pub args: Vec<String>, pub environment: Vec<(String, String)>, pub health_endpoint: Url }
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceStatus { Missing, Stopped, Starting, Ready, Failed }
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceSnapshot { pub name: String, pub status: ServiceStatus, pub process_id: Option<u32>, pub detail: String }

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StackManifest { pub schema_version: u32, pub version: String, pub commit: String, pub generated_at: String }

/// What the shell reports about the local service stack: the manifest of the
/// stack actually in use (bundled or a persisted fallback), which source won,
/// and every stack version cached on this machine.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StackInfo {
    pub manifest: Option<StackManifest>,
    pub source: String,
    pub persisted: Vec<StackManifest>,
    pub pinned: Option<String>,
}

const PIN_FILE: &str = ".pinned";

fn version_dir_name_of(version: &str, commit: &str) -> String {
    let raw = if commit.is_empty() { version.to_owned() } else { format!("{version}-{commit}") };
    raw.chars().map(|c| if c.is_alphanumeric() || matches!(c, '.' | '_' | '-') { c } else { '_' }).collect()
}

fn version_dir_name(manifest: &StackManifest) -> String {
    version_dir_name_of(&manifest.version, &manifest.commit)
}

fn read_stack_manifest(path: &PathBuf) -> Option<StackManifest> {
    let raw = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    Some(StackManifest {
        schema_version: value.get("schemaVersion")?.as_u64()? as u32,
        version: value.get("version")?.as_str()?.to_owned(),
        commit: value.get("commit")?.as_str().unwrap_or_default().to_owned(),
        generated_at: value.get("generatedAt")?.as_str()?.to_owned(),
    })
}

fn copy_dir_recursive(src: &PathBuf, dst: &PathBuf) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let target = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() { copy_dir_recursive(&entry.path(), &target)?; }
        else if entry.file_type()?.is_file() { std::fs::copy(entry.path(), &target)?; }
    }
    Ok(())
}

/// Persisted stack manifests under `<data>/stacks/<version>/local-services`,
/// newest generation first.
fn list_persisted_stacks(stacks_dir: &PathBuf) -> Vec<StackManifest> {
    let mut stacks = Vec::new();
    let Ok(entries) = std::fs::read_dir(stacks_dir) else { return stacks };
    for entry in entries.flatten() {
        if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            if let Some(manifest) = read_stack_manifest(&entry.path().join("local-services").join("stack-manifest.json")) { stacks.push(manifest); }
        }
    }
    stacks.sort_by(|a, b| b.generated_at.cmp(&a.generated_at));
    stacks
}

/// Newest persisted copy that carries the requested service binary.
fn find_persisted_exe(stacks_dir: &PathBuf, relative: &PathBuf) -> Option<(PathBuf, StackManifest)> {
    for stack in list_persisted_stacks(stacks_dir) {
        let candidate = stacks_dir.join(version_dir_name(&stack)).join("local-services").join(relative);
        if candidate.is_file() { return Some((candidate, stack)); }
    }
    None
}

fn read_stack_pin(stacks_dir: &PathBuf) -> Option<String> {
    let raw = std::fs::read_to_string(stacks_dir.join(PIN_FILE)).ok()?;
    let name = raw.trim().to_owned();
    (!name.is_empty()).then_some(name)
}

fn pinned_manifest(stacks_dir: &PathBuf, pinned: Option<&str>) -> Option<StackManifest> {
    let name = pinned?;
    read_stack_manifest(&stacks_dir.join(name).join("local-services").join("stack-manifest.json"))
}
struct ServiceProcess { spec: ServiceSpec, child: Option<Child>, status: ServiceStatus, detail: String, restart_count: u8 }

pub struct ServiceSupervisor { processes: Mutex<HashMap<String, ServiceProcess>>, ports: Option<PortLease>, effective: Option<StackManifest>, source: &'static str, persisted: Vec<StackManifest>, stacks_dir: Option<PathBuf>, pinned: Option<String> }
struct PortLease { base: u16, files: Vec<PathBuf> }
impl Drop for PortLease { fn drop(&mut self) { for path in &self.files { let _ = std::fs::remove_file(path); } } }

impl ServiceSupervisor {
    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        Self::from_resource_dir_with_workspace_root(resource_dir, None)
    }

    /// Desktop entry point: the persisted workspace binding (if any) is
    /// injected into mission-control via `AGENTHUB_DESKTOP_WORKSPACE_ROOT`.
    pub fn from_resource_dir_with_workspace_root(
        resource_dir: PathBuf,
        workspace_root: Option<String>,
    ) -> Self {
        let data_root = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
        Self::from_resource_dir_with_data_dir(resource_dir, data_root.join("AgentHub"), workspace_root)
    }

    pub fn from_resource_dir_with_data_dir(resource_dir: PathBuf, data_dir: PathBuf, workspace_root: Option<String>) -> Self {
        let service_dir = resource_dir.join("local-services");
        let db_path = data_dir.join("data").join("agenthub.db");
        let _ = std::fs::create_dir_all(db_path.parent().unwrap_or(&data_dir));
        let _ = std::fs::OpenOptions::new().create(true).append(true).open(&db_path);
        let ports = allocate_ports(&data_dir);
        let base = ports.as_ref().map(|lease| lease.base).unwrap_or(0);
        let definitions = [("mission-control", "agenthub-mission-control.exe"), ("gateway", "agenthub-gateway.exe"), ("mcp-gateway", "agenthub-mcp-gateway.exe"), ("frontend", "frontend\\node.exe")];
        let mut processes = HashMap::new();
        let stack = read_stack_manifest(&service_dir.join("stack-manifest.json"));
        // Persist each bundled stack once per version under <data>/stacks so a
        // broken upgrade can fall back to the last copy that shipped a working
        // service binary. Best-effort: failures never block startup.
        let stacks_dir = data_dir.join("stacks");
        if let Some(manifest) = &stack {
            let snapshot = stacks_dir.join(version_dir_name(manifest)).join("local-services");
            if !snapshot.join("stack-manifest.json").is_file() {
                let _ = copy_dir_recursive(&service_dir, &snapshot);
            }
        }
        let persisted = list_persisted_stacks(&stacks_dir);
        let pinned = read_stack_pin(&stacks_dir);
        let mut source = if stack.is_some() { "bundled" } else { "unversioned" };
        let mut effective = stack.clone();
        for (index, (name, file)) in definitions.into_iter().enumerate() {
            let port = if name == "frontend" { base.saturating_add(4) } else { base.saturating_add(index as u16) };
            let relative = PathBuf::from(file);
            let executable = {
                // Manual pin wins over the bundle (that is its purpose), then
                // the bundled binary, then the newest persisted fallback.
                let pinned_candidate = pinned.as_ref().and_then(|name| {
                    let path = stacks_dir.join(name).join("local-services").join(&relative);
                    path.is_file().then_some(path)
                });
                match pinned_candidate {
                    Some(path) => { source = "pinned"; effective = pinned_manifest(&stacks_dir, pinned.as_deref()); path }
                    None => match [service_dir.join(&relative), resource_dir.join(&relative)].into_iter().find(|path| path.is_file()) {
                        Some(path) => path,
                        None => match find_persisted_exe(&stacks_dir, &relative) {
                            Some((path, manifest)) => { source = "persisted"; effective = Some(manifest); path }
                            None => service_dir.join(&relative),
                        },
                    },
                }
            };
            let path = match name { "mission-control" => "/api/health", "frontend" => "/admin", _ => "/healthz" };
            let endpoint = Url::parse(&format!("http://127.0.0.1:{port}{path}")).expect("health URL");
            let environment = service_environment(name, port, base, &data_dir, &db_path, workspace_root.as_deref());
            let args = if name == "frontend" {
                vec![executable.parent().unwrap_or(&service_dir).join("server.js").to_string_lossy().into_owned()]
            } else { Vec::new() };
            let detail = if base == 0 { "no free AgentHub port group in 28000-28999" } else { "service resource is not bundled" };
            processes.insert(name.to_owned(), ServiceProcess { spec: ServiceSpec { name, executable, args, environment, health_endpoint: endpoint }, child: None, status: if base == 0 { ServiceStatus::Failed } else { ServiceStatus::Missing }, detail: detail.into(), restart_count: 0 });
        }
        Self { processes: Mutex::new(processes), ports, effective, source, persisted, stacks_dir: Some(stacks_dir), pinned }
    }

    pub fn stack_info(&self) -> StackInfo { StackInfo { manifest: self.effective.clone(), source: self.source.to_owned(), persisted: self.persisted.clone(), pinned: self.pinned.clone() } }

    /// Pin the local service stack to a cached version. Takes effect on the
    /// next desktop start; the running processes keep their binaries.
    pub fn pin_stack(&self, version: &str, commit: &str) -> Result<String, String> {
        let Some(dir) = &self.stacks_dir else { return Err("stack cache is unavailable".to_owned()) };
        let name = version_dir_name_of(version, commit);
        if !dir.join(&name).join("local-services").join("stack-manifest.json").is_file() {
            return Err(format!("stack {version}@{commit} is not cached on this machine"));
        }
        std::fs::write(dir.join(PIN_FILE), &name).map_err(|error| error.to_string())?;
        Ok(format!("已钉住 {version}@{commit}，重启桌面应用后生效。"))
    }

    pub fn clear_stack_pin(&self) -> Result<String, String> {
        let Some(dir) = &self.stacks_dir else { return Err("stack cache is unavailable".to_owned()) };
        match std::fs::remove_file(dir.join(PIN_FILE)) {
            Ok(()) => Ok("已取消钉住，重启桌面应用后生效。".to_owned()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok("当前没有钉住的栈。".to_owned()),
            Err(error) => Err(error.to_string()),
        }
    }

    pub fn mission_control_endpoint(&self) -> Option<String> { self.ports.as_ref().map(|lease| format!("http://127.0.0.1:{}", lease.base)) }
    pub fn frontend_endpoint(&self) -> Option<String> { self.ports.as_ref().map(|lease| format!("http://127.0.0.1:{}/admin", lease.base + 4)) }
    pub fn runtime_port(&self) -> Option<u16> { self.ports.as_ref().map(|lease| lease.base + 3) }
    pub fn start_all(&self) -> Vec<ServiceSnapshot> { self.start_all_with_secrets(None) }
    pub fn start_all_with_secrets(&self, model_api_key: Option<&str>) -> Vec<ServiceSnapshot> { let mut state = self.processes.lock().expect("service supervisor lock"); for process in state.values_mut() { if process.child.is_some() { continue; } if !process.spec.executable.is_file() { process.status = ServiceStatus::Missing; continue; } let mut command = Command::new(&process.spec.executable); command.args(&process.spec.args); for (key, value) in &process.spec.environment { command.env(key, value); } if process.spec.name == "mission-control" { if let Some(key) = model_api_key.filter(|value| !value.trim().is_empty()) { command.env("AGENTHUB_DESKTOP_MODEL_API_KEY", key); } } crate::hide_window(&mut command); match command.spawn() { Ok(child) => { process.status = ServiceStatus::Starting; process.detail = "service process started; health check pending".into(); process.child = Some(child); }, Err(error) => { process.status = ServiceStatus::Failed; process.detail = format!("unable to start service: {error}"); } } } Self::collect_snapshots(&mut state) }
    pub fn snapshots(&self) -> Vec<ServiceSnapshot> { let mut state = self.processes.lock().expect("service supervisor lock"); Self::collect_snapshots(&mut state) }
    fn collect_snapshots(state: &mut HashMap<String, ServiceProcess>) -> Vec<ServiceSnapshot> { state.values_mut().map(|process| { let mut exited = None; let pid = if let Some(child) = process.child.as_mut() { match child.try_wait() { Ok(None) => Some(child.id()), Ok(Some(status)) => { exited = Some(format!("service exited with {status}")); None }, Err(error) => { exited = Some(format!("unable to inspect service: {error}")); None } } } else { None }; if let Some(detail) = exited { process.child = None; if process.restart_count < 3 && process.spec.executable.is_file() { let mut command = Command::new(&process.spec.executable); command.args(&process.spec.args); for (key, value) in &process.spec.environment { command.env(key, value); } crate::hide_window(&mut command); match command.spawn() { Ok(child) => { process.restart_count += 1; process.child = Some(child); process.status = ServiceStatus::Starting; process.detail = format!("{detail}; automatically restarted ({}/3)", process.restart_count); }, Err(error) => { process.status = ServiceStatus::Failed; process.detail = format!("{detail}; restart failed: {error}"); } } } else { process.status = ServiceStatus::Failed; process.detail = format!("{detail}; restart limit reached"); } } if pid.is_some() && probe(&process.spec.health_endpoint) { process.status = ServiceStatus::Ready; process.detail = "service health endpoint is ready".into(); } ServiceSnapshot { name: process.spec.name.into(), status: process.status.clone(), process_id: pid, detail: process.detail.clone() } }).collect() }
    pub fn stop_all(&self) { let mut state = self.processes.lock().expect("service supervisor lock"); for process in state.values_mut() { if let Some(mut child) = process.child.take() { let _ = child.kill(); let _ = child.wait(); } process.status = ServiceStatus::Stopped; process.detail = "service stopped".into(); } }
}

fn service_environment(name: &str, port: u16, base: u16, data_dir: &PathBuf, db_path: &PathBuf, workspace_root: Option<&str>) -> Vec<(String, String)> {
    let address = format!("127.0.0.1:{port}");
    match name {
        "mission-control" => {
            let mut vars = vec![("AGENTHUB_DB_BACKEND".into(), "sqlite".into()), ("AGENTHUB_SQLITE_PATH".into(), db_path.to_string_lossy().into()), ("AGENTHUB_LOCAL_DATA".into(), data_dir.to_string_lossy().into()), ("HOST".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string()), ("AGENTHUB_DESKTOP_LOCAL_RUNNER".into(), "1".into()), ("AGENTHUB_DESKTOP_ADMIN_NAME".into(), "admin".into()), ("AGENTHUB_DESKTOP_ADMIN_PASSWORD".into(), "admin123".into())];
            if let Some(root) = workspace_root.map(str::trim).filter(|value| !value.is_empty()) {
                vars.push(("AGENTHUB_DESKTOP_WORKSPACE_ROOT".into(), root.into()));
            }
            vars
        }
        "gateway" => vec![("GATEWAY_LOCAL_MODE".into(), "true".into()), ("GATEWAY_ADDR".into(), address.clone()), ("HOST".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string())],
        "mcp-gateway" => vec![("MCP_LOCAL_MODE".into(), "true".into()), ("MCP_ADDR".into(), address), ("GATEWAY_URL".into(), format!("http://127.0.0.1:{}", base.saturating_add(1))), ("AGENTHUB_LOCAL_DATA".into(), data_dir.to_string_lossy().into())],
        "frontend" => vec![("HOSTNAME".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string()), ("API_BACKEND".into(), "legacy".into()), ("API_BACKEND_URL".into(), format!("http://127.0.0.1:{}", base)), ("GO_GATEWAY_URL".into(), format!("http://127.0.0.1:{}", base.saturating_add(1)))],
        _ => Vec::new(),
    }
}

fn allocate_ports(data_dir: &PathBuf) -> Option<PortLease> {
    let preferred = data_dir.join("data").join("ports");
    let lock_dir = if std::fs::create_dir_all(&preferred).is_ok() { preferred } else {
        let fallback = std::env::temp_dir().join("AgentHub").join("ports");
        std::fs::create_dir_all(&fallback).ok()?;
        fallback
    };
    for base in (PORT_POOL_START..=PORT_POOL_END - PORTS_PER_INSTANCE + 1).step_by(PORTS_PER_INSTANCE as usize) {
        let files: Vec<PathBuf> = (0..PORTS_PER_INSTANCE).map(|offset| lock_dir.join(format!("{}.lock", base + offset))).collect(); let mut created = Vec::new(); let mut ok = true;
        for path in &files { if std::fs::OpenOptions::new().write(true).create_new(true).open(path).is_ok() { created.push(path.clone()); } else { ok = false; break; } }
        if ok && (0..PORTS_PER_INSTANCE).all(|offset| TcpListener::bind(("127.0.0.1", base + offset)).is_ok()) { return Some(PortLease { base, files }); }
        for path in created { let _ = std::fs::remove_file(path); }
    }
    None
}

fn probe(endpoint: &Url) -> bool { let Some(port) = endpoint.port_or_known_default() else { return false; }; let address = format!("127.0.0.1:{port}"); let Some(socket) = address.to_socket_addrs().ok().and_then(|mut a| a.next()) else { return false; }; let Ok(mut stream) = TcpStream::connect_timeout(&socket, Duration::from_millis(250)) else { return false; }; let _ = stream.set_read_timeout(Some(Duration::from_millis(500))); if write!(stream, "GET {} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n", endpoint.path()).is_err() { return false; } let mut response = String::new(); if stream.read_to_string(&mut response).is_err() { return false; } response.starts_with("HTTP/1.1 2") || response.starts_with("HTTP/1.0 2") }
impl Drop for ServiceSupervisor { fn drop(&mut self) { self.stop_all(); } }

#[cfg(test)]
mod tests { use super::*; #[test] fn missing_bundled_services_fail_closed() { let supervisor = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services")); let snapshots = supervisor.start_all(); assert!(snapshots.iter().all(|item| matches!(item.status, ServiceStatus::Missing | ServiceStatus::Failed))); } #[test] fn stack_is_unversioned_without_manifest() { let data = std::env::temp_dir().join("agenthub-test-data-unversioned"); let supervisor = ServiceSupervisor::from_resource_dir_with_data_dir(std::env::temp_dir().join("missing-agenthub-services-manifest"), data, None); let info = supervisor.stack_info(); assert_eq!(info.source, "unversioned"); assert!(info.manifest.is_none()); assert!(info.persisted.is_empty()); } #[test] fn bundled_stack_is_snapshotted_per_version() { let root = std::env::temp_dir().join("agenthub-stack-snapshot-test"); let data = std::env::temp_dir().join("agenthub-stack-snapshot-data"); let _ = std::fs::remove_dir_all(&root); let _ = std::fs::remove_dir_all(&data); let service_dir = root.join("local-services"); std::fs::create_dir_all(&service_dir).expect("create service dir"); std::fs::write(service_dir.join("stack-manifest.json"), r#"{"schemaVersion":1,"version":"0.2.0","commit":"abc1234","generatedAt":"2026-08-27T12:00:00Z"}"#).expect("write manifest"); std::fs::write(service_dir.join("agenthub-gateway.exe"), b"stub").expect("write exe"); let supervisor = ServiceSupervisor::from_resource_dir_with_data_dir(root.clone(), data.clone(), None); let info = supervisor.stack_info(); assert_eq!(info.source, "bundled"); assert_eq!(info.manifest.as_ref().expect("manifest").version, "0.2.0"); let snapshot_manifest = data.join("stacks").join("0.2.0-abc1234").join("local-services").join("stack-manifest.json"); assert!(snapshot_manifest.is_file(), "versioned snapshot must exist"); assert_eq!(info.persisted.len(), 1); assert_eq!(info.persisted[0].commit, "abc1234"); std::fs::remove_dir_all(root).ok(); std::fs::remove_dir_all(data).ok(); } #[test] fn missing_bundled_exe_falls_back_to_persisted_stack() { let root = std::env::temp_dir().join("agenthub-stack-fallback-test"); let data = std::env::temp_dir().join("agenthub-stack-fallback-data"); let _ = std::fs::remove_dir_all(&root); let _ = std::fs::remove_dir_all(&data); let service_dir = root.join("local-services"); std::fs::create_dir_all(&service_dir).expect("create service dir"); std::fs::write(service_dir.join("stack-manifest.json"), r#"{"schemaVersion":1,"version":"0.3.0","commit":"def5678","generatedAt":"2026-08-27T13:00:00Z"}"#).expect("write manifest"); let persisted_dir = data.join("stacks").join("0.2.0-abc1234").join("local-services"); std::fs::create_dir_all(&persisted_dir).expect("create persisted dir"); std::fs::write(persisted_dir.join("stack-manifest.json"), r#"{"schemaVersion":1,"version":"0.2.0","commit":"abc1234","generatedAt":"2026-08-27T12:00:00Z"}"#).expect("write persisted manifest"); std::fs::write(persisted_dir.join("agenthub-gateway.exe"), b"stub").expect("write persisted exe"); let supervisor = ServiceSupervisor::from_resource_dir_with_data_dir(root, data, None); let info = supervisor.stack_info(); assert_eq!(info.source, "persisted"); assert_eq!(info.manifest.as_ref().expect("effective manifest").version, "0.2.0"); } #[test] fn pin_overrides_the_bundled_stack() { let root = std::env::temp_dir().join("agenthub-stack-pin-test"); let data = std::env::temp_dir().join("agenthub-stack-pin-data"); let _ = std::fs::remove_dir_all(&root); let _ = std::fs::remove_dir_all(&data); let service_dir = root.join("local-services"); std::fs::create_dir_all(&service_dir).expect("create service dir"); std::fs::write(service_dir.join("stack-manifest.json"), r#"{"schemaVersion":1,"version":"0.3.0","commit":"def5678","generatedAt":"2026-08-27T13:00:00Z"}"#).expect("write manifest"); let persisted_dir = data.join("stacks").join("0.2.0-abc1234").join("local-services"); std::fs::create_dir_all(&persisted_dir).expect("create persisted dir"); std::fs::write(persisted_dir.join("stack-manifest.json"), r#"{"schemaVersion":1,"version":"0.2.0","commit":"abc1234","generatedAt":"2026-08-27T12:00:00Z"}"#).expect("write persisted manifest"); std::fs::write(persisted_dir.join("agenthub-gateway.exe"), b"stub").expect("write persisted exe"); let supervisor = ServiceSupervisor::from_resource_dir_with_data_dir(root.clone(), data.clone(), None); let message = supervisor.pin_stack("0.2.0", "abc1234").expect("pin succeeds"); assert!(message.contains("重启")); assert_eq!(data.join("stacks").join(".pinned").is_file(), true); let pinned = ServiceSupervisor::from_resource_dir_with_data_dir(root, data, None); let info = pinned.stack_info(); assert_eq!(info.source, "pinned"); assert_eq!(info.manifest.as_ref().expect("pinned manifest").version, "0.2.0"); assert_eq!(info.pinned.as_deref(), Some("0.2.0-abc1234")); assert!(pinned.clear_stack_pin().is_ok()); } #[test] fn separate_supervisors_get_separate_port_groups() { let first = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services-a")); let second = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services-b")); assert_ne!(first.mission_control_endpoint(), second.mission_control_endpoint()); }
#[test] fn mission_control_injects_persisted_workspace_root_only_when_set() { let data = PathBuf::from("test-data"); let db = data.join("agenthub.db"); let bound = service_environment("mission-control", 28000, 28000, &data, &db, Some(r"D:\proj")); assert!(bound.iter().any(|(k, v)| k == "AGENTHUB_DESKTOP_WORKSPACE_ROOT" && v == r"D:\proj")); let unbound = service_environment("mission-control", 28000, 28000, &data, &db, None); assert!(!unbound.iter().any(|(k, _)| k == "AGENTHUB_DESKTOP_WORKSPACE_ROOT")); let blank = service_environment("mission-control", 28000, 28000, &data, &db, Some("   ")); assert!(!blank.iter().any(|(k, _)| k == "AGENTHUB_DESKTOP_WORKSPACE_ROOT")); assert!(!service_environment("gateway", 28001, 28000, &data, &db, Some(r"D:\proj")).iter().any(|(k, _)| k == "AGENTHUB_DESKTOP_WORKSPACE_ROOT")); } }
