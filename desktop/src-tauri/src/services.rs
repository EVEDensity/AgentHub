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
struct ServiceProcess { spec: ServiceSpec, child: Option<Child>, status: ServiceStatus, detail: String, restart_count: u8 }

pub struct ServiceSupervisor { processes: Mutex<HashMap<String, ServiceProcess>>, ports: Option<PortLease> }
struct PortLease { base: u16, files: Vec<PathBuf> }
impl Drop for PortLease { fn drop(&mut self) { for path in &self.files { let _ = std::fs::remove_file(path); } } }

impl ServiceSupervisor {
    pub fn from_resource_dir(resource_dir: PathBuf) -> Self {
        let service_dir = resource_dir.join("local-services");
        let data_root = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
        let data_dir = data_root.join("AgentHub");
        let db_path = data_dir.join("data").join("agenthub.db");
        let _ = std::fs::create_dir_all(db_path.parent().unwrap_or(&data_dir));
        let _ = std::fs::OpenOptions::new().create(true).append(true).open(&db_path);
        let ports = allocate_ports(&data_dir);
        let base = ports.as_ref().map(|lease| lease.base).unwrap_or(0);
        let definitions = [("mission-control", "agenthub-mission-control.exe"), ("gateway", "agenthub-gateway.exe"), ("mcp-gateway", "agenthub-mcp-gateway.exe"), ("frontend", "frontend\\node.exe")];
        let mut processes = HashMap::new();
        for (index, (name, file)) in definitions.into_iter().enumerate() {
            let port = if name == "frontend" { base.saturating_add(4) } else { base.saturating_add(index as u16) };
            let executable = [service_dir.join(file), resource_dir.join(file)].into_iter().find(|path| path.is_file()).unwrap_or_else(|| service_dir.join(file));
            let path = match name { "mission-control" => "/api/health", "frontend" => "/admin", _ => "/healthz" };
            let endpoint = Url::parse(&format!("http://127.0.0.1:{port}{path}")).expect("health URL");
            let environment = service_environment(name, port, base, &data_dir, &db_path);
            let args = if name == "frontend" {
                vec![executable.parent().unwrap_or(&service_dir).join("server.js").to_string_lossy().into_owned()]
            } else { Vec::new() };
            let detail = if base == 0 { "no free AgentHub port group in 28000-28999" } else { "service resource is not bundled" };
            processes.insert(name.to_owned(), ServiceProcess { spec: ServiceSpec { name, executable, args, environment, health_endpoint: endpoint }, child: None, status: if base == 0 { ServiceStatus::Failed } else { ServiceStatus::Missing }, detail: detail.into(), restart_count: 0 });
        }
        Self { processes: Mutex::new(processes), ports }
    }

    pub fn mission_control_endpoint(&self) -> Option<String> { self.ports.as_ref().map(|lease| format!("http://127.0.0.1:{}", lease.base)) }
    pub fn frontend_endpoint(&self) -> Option<String> { self.ports.as_ref().map(|lease| format!("http://127.0.0.1:{}/admin", lease.base + 4)) }
    pub fn runtime_port(&self) -> Option<u16> { self.ports.as_ref().map(|lease| lease.base + 3) }
    pub fn start_all(&self) -> Vec<ServiceSnapshot> { let mut state = self.processes.lock().expect("service supervisor lock"); for process in state.values_mut() { if process.child.is_some() { continue; } if !process.spec.executable.is_file() { process.status = ServiceStatus::Missing; continue; } let mut command = Command::new(&process.spec.executable); command.args(&process.spec.args); for (key, value) in &process.spec.environment { command.env(key, value); } match command.spawn() { Ok(child) => { process.status = ServiceStatus::Starting; process.detail = "service process started; health check pending".into(); process.child = Some(child); }, Err(error) => { process.status = ServiceStatus::Failed; process.detail = format!("unable to start service: {error}"); } } } Self::collect_snapshots(&mut state) }
    pub fn snapshots(&self) -> Vec<ServiceSnapshot> { let mut state = self.processes.lock().expect("service supervisor lock"); Self::collect_snapshots(&mut state) }
    fn collect_snapshots(state: &mut HashMap<String, ServiceProcess>) -> Vec<ServiceSnapshot> { state.values_mut().map(|process| { let mut exited = None; let pid = if let Some(child) = process.child.as_mut() { match child.try_wait() { Ok(None) => Some(child.id()), Ok(Some(status)) => { exited = Some(format!("service exited with {status}")); None }, Err(error) => { exited = Some(format!("unable to inspect service: {error}")); None } } } else { None }; if let Some(detail) = exited { process.child = None; if process.restart_count < 3 && process.spec.executable.is_file() { let mut command = Command::new(&process.spec.executable); command.args(&process.spec.args); for (key, value) in &process.spec.environment { command.env(key, value); } match command.spawn() { Ok(child) => { process.restart_count += 1; process.child = Some(child); process.status = ServiceStatus::Starting; process.detail = format!("{detail}; automatically restarted ({}/3)", process.restart_count); }, Err(error) => { process.status = ServiceStatus::Failed; process.detail = format!("{detail}; restart failed: {error}"); } } } else { process.status = ServiceStatus::Failed; process.detail = format!("{detail}; restart limit reached"); } } if pid.is_some() && probe(&process.spec.health_endpoint) { process.status = ServiceStatus::Ready; process.detail = "service health endpoint is ready".into(); } ServiceSnapshot { name: process.spec.name.into(), status: process.status.clone(), process_id: pid, detail: process.detail.clone() } }).collect() }
    pub fn stop_all(&self) { let mut state = self.processes.lock().expect("service supervisor lock"); for process in state.values_mut() { if let Some(mut child) = process.child.take() { let _ = child.kill(); let _ = child.wait(); } process.status = ServiceStatus::Stopped; process.detail = "service stopped".into(); } }
}

fn service_environment(name: &str, port: u16, base: u16, data_dir: &PathBuf, db_path: &PathBuf) -> Vec<(String, String)> {
    let address = format!("127.0.0.1:{port}");
    match name {
        "mission-control" => vec![("AGENTHUB_DB_BACKEND".into(), "sqlite".into()), ("AGENTHUB_SQLITE_PATH".into(), db_path.to_string_lossy().into()), ("AGENTHUB_LOCAL_DATA".into(), data_dir.to_string_lossy().into()), ("HOST".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string())],
        "gateway" => vec![("GATEWAY_LOCAL_MODE".into(), "true".into()), ("GATEWAY_ADDR".into(), address.clone()), ("HOST".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string())],
        "mcp-gateway" => vec![("MCP_LOCAL_MODE".into(), "true".into()), ("MCP_ADDR".into(), address), ("GATEWAY_URL".into(), format!("http://127.0.0.1:{}", base.saturating_add(1))), ("AGENTHUB_LOCAL_DATA".into(), data_dir.to_string_lossy().into())],
        "frontend" => vec![("HOSTNAME".into(), "127.0.0.1".into()), ("PORT".into(), port.to_string()), ("API_BACKEND".into(), "legacy".into()), ("API_BACKEND_URL".into(), format!("http://127.0.0.1:{}", base))],
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
mod tests { use super::*; #[test] fn missing_bundled_services_fail_closed() { let supervisor = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services")); let snapshots = supervisor.start_all(); assert!(snapshots.iter().all(|item| matches!(item.status, ServiceStatus::Missing | ServiceStatus::Failed))); } #[test] fn separate_supervisors_get_separate_port_groups() { let first = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services-a")); let second = ServiceSupervisor::from_resource_dir(std::env::temp_dir().join("missing-agenthub-services-b")); assert_ne!(first.mission_control_endpoint(), second.mission_control_endpoint()); } }
