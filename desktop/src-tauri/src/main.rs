#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod probe;
mod protocol;
mod runtime;
mod services;

use config::{
    ConfigurationDetails, ConfigurationStatus, ConfigurationStore, DesktopConfigInput, SecretInput,
    SecretKind,
};
use probe::probe_control_plane as probe_saved_control_plane;
use probe::probe_mcp_endpoint;
use protocol::{ControlPlaneSnapshot, RuntimeSnapshot};
use runtime::LocalRuntime;
use services::{ServiceSnapshot, ServiceSupervisor};
use std::process::Command;
use tauri::{Manager, State};

/// Spawn a child process without flashing a console window on the desktop.
/// The bundled services are console-subsystem binaries (PyInstaller, Go,
/// node.exe); without CREATE_NO_WINDOW each spawn pops up a console window
/// during startup and every automatic restart.
#[cfg(windows)]
pub(crate) fn hide_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
pub(crate) fn hide_window(_command: &mut Command) {}

// Health probes and process lifecycle calls block for up to a few seconds.
// They must stay off the main/UI thread: Tauri runs sync commands on the main
// thread (freezing the webview), while async commands run on the async runtime
// worker pool. Borrowed parameters (State) in async commands require a Result
// return type.
#[tauri::command]
async fn runtime_status(runtime: State<'_, LocalRuntime>) -> Result<RuntimeSnapshot, String> {
    Ok(runtime.snapshot())
}

#[tauri::command]
async fn start_runtime(
    runtime: State<'_, LocalRuntime>,
    configuration: State<'_, ConfigurationStore>,
    supervisor: State<'_, ServiceSupervisor>,
) -> Result<RuntimeSnapshot, String> {
    let model_api_key = configuration
        .secret(SecretKind::ModelApiKey)
        .map_err(|error| error.to_string())?;
    let _ = supervisor.start_all_with_secrets(model_api_key.as_deref());
    let configuration_ready = configuration
        .status()
        .map_err(|error| error.to_string())?
        .ready_for_runtime;
    let artifact_directory = configuration
        .details()
        .map_err(|error| error.to_string())?
        .artifact_directory;
    Ok(runtime.start(
        configuration_ready,
        artifact_directory.as_deref(),
    ))
}

#[tauri::command]
async fn stop_runtime(
    runtime: State<'_, LocalRuntime>,
    supervisor: State<'_, ServiceSupervisor>,
) -> Result<RuntimeSnapshot, String> {
    supervisor.stop_all();
    Ok(runtime.stop())
}

#[tauri::command]
async fn service_status(supervisor: State<'_, ServiceSupervisor>) -> Result<Vec<ServiceSnapshot>, String> {
    Ok(supervisor.snapshots())
}

#[tauri::command]
fn configuration_status(
    configuration: State<'_, ConfigurationStore>,
) -> Result<ConfigurationStatus, String> {
    configuration.status().map_err(|error| error.to_string())
}

#[tauri::command]
fn configuration_details(
    configuration: State<'_, ConfigurationStore>,
) -> Result<ConfigurationDetails, String> {
    configuration.details().map_err(|error| error.to_string())
}

#[tauri::command]
fn save_configuration(
    configuration: State<'_, ConfigurationStore>,
    input: DesktopConfigInput,
) -> Result<ConfigurationStatus, String> {
    configuration
        .save_config(input)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_configuration_secret(
    configuration: State<'_, ConfigurationStore>,
    input: SecretInput,
) -> Result<ConfigurationStatus, String> {
    configuration
        .set_secret(input)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn clear_configuration_secret(
    configuration: State<'_, ConfigurationStore>,
    kind: SecretKind,
) -> Result<ConfigurationStatus, String> {
    configuration
        .clear_secret(kind)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn local_service_endpoint(supervisor: State<'_, ServiceSupervisor>) -> Result<String, String> {
    supervisor.mission_control_endpoint().ok_or_else(|| "No free AgentHub local port group is available.".to_owned())
}

#[tauri::command]
fn frontend_endpoint(supervisor: State<'_, ServiceSupervisor>) -> Result<String, String> {
    supervisor.frontend_endpoint().ok_or_else(|| "No free AgentHub local port group is available.".to_owned())
}

#[tauri::command]
async fn probe_control_plane(
    configuration: State<'_, ConfigurationStore>,
    supervisor: State<'_, ServiceSupervisor>,
) -> Result<ControlPlaneSnapshot, String> {
    let endpoint = configuration
        .mission_control_endpoint()
        .map_err(|error| error.to_string())?;
    let token = configuration
        .secret(SecretKind::MissionControlToken)
        .map_err(|error| error.to_string())?;
    let endpoint = if endpoint.as_deref() == Some("http://127.0.0.1:8080") {
        supervisor.mission_control_endpoint().or(endpoint)
    } else { endpoint };
    Ok(probe_saved_control_plane(
        endpoint.as_deref(),
        token.as_deref(),
    ))
}

#[tauri::command]
async fn probe_mcp(
    configuration: State<'_, ConfigurationStore>,
) -> Result<ControlPlaneSnapshot, String> {
    let endpoint = configuration
        .details()
        .map_err(|error| error.to_string())?
        .mcp_endpoint;
    Ok(probe_mcp_endpoint(endpoint.as_deref()))
}

#[tauri::command]
fn stack_info(supervisor: State<'_, ServiceSupervisor>) -> services::StackInfo {
    supervisor.stack_info()
}

#[tauri::command]
fn pin_stack(supervisor: State<'_, ServiceSupervisor>, version: String, commit: String) -> Result<String, String> {
    supervisor.pin_stack(&version, &commit)
}

#[tauri::command]
fn clear_stack_pin(supervisor: State<'_, ServiceSupervisor>) -> Result<String, String> {
    supervisor.clear_stack_pin()
}

#[tauri::command]
fn open_control_plane(configuration: State<'_, ConfigurationStore>, supervisor: State<'_, ServiceSupervisor>) -> Result<(), String> {
    let configured = configuration
        .mission_control_endpoint()
        .map_err(|error| error.to_string())?;
    let endpoint = if configured.as_deref().is_none() || configured.as_deref() == Some("http://127.0.0.1:8080") {
        supervisor.frontend_endpoint().or(configured)
    } else {
        configured
    }.ok_or_else(|| "Mission Control endpoint is not configured".to_owned())?;

    let mut command = Command::new("rundll32.exe");
    command.args(["url.dll,FileProtocolHandler", endpoint.as_str()]);
    hide_window(&mut command);
    command
        .spawn()
        .map(|_| ())
        .map_err(|_| "Unable to open Mission Control in the default browser".to_owned())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let configuration = ConfigurationStore::new(config_dir);
            configuration
                .ensure_defaults()
                .map_err(|error| error.to_string())?;
            app.manage(configuration);
            let resource_dir = app.path().resource_dir()?;
            let supervisor = ServiceSupervisor::from_resource_dir(resource_dir.clone());
            let runtime = supervisor.runtime_port().map_or_else(
                || LocalRuntime::from_resource_dir(resource_dir.clone()),
                |port| LocalRuntime::from_resource_dir_with_port(resource_dir.clone(), port),
            );
            app.manage(runtime);
            app.manage(supervisor);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            start_runtime,
            stop_runtime,
            service_status,
            configuration_status,
            configuration_details,
            save_configuration,
            set_configuration_secret,
            clear_configuration_secret,
            probe_control_plane,
            probe_mcp,
            stack_info,
            pin_stack,
            clear_stack_pin,
            local_service_endpoint,
            frontend_endpoint,
            open_control_plane
        ])
        .run(tauri::generate_context!())
        .expect("AgentHub desktop shell failed");
}
