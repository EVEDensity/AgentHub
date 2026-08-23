#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod protocol;
mod runtime;

use config::{
    ConfigurationDetails, ConfigurationStatus, ConfigurationStore, DesktopConfigInput, SecretInput,
    SecretKind,
};
use protocol::RuntimeSnapshot;
use runtime::LocalRuntime;
use std::process::Command;
use tauri::{Manager, State};

#[tauri::command]
fn runtime_status(runtime: State<'_, LocalRuntime>) -> RuntimeSnapshot {
    runtime.snapshot()
}

#[tauri::command]
fn start_runtime(
    runtime: State<'_, LocalRuntime>,
    configuration: State<'_, ConfigurationStore>,
) -> Result<RuntimeSnapshot, String> {
    let configuration_ready = configuration
        .status()
        .map_err(|error| error.to_string())?
        .ready_for_runtime;
    Ok(runtime.start(configuration_ready))
}

#[tauri::command]
fn stop_runtime(runtime: State<'_, LocalRuntime>) -> RuntimeSnapshot {
    runtime.stop()
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
fn open_control_plane(configuration: State<'_, ConfigurationStore>) -> Result<(), String> {
    let endpoint = configuration
        .mission_control_endpoint()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "Mission Control endpoint is not configured".to_owned())?;

    Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", endpoint.as_str()])
        .spawn()
        .map(|_| ())
        .map_err(|_| "Unable to open Mission Control in the default browser".to_owned())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            app.manage(ConfigurationStore::new(config_dir));
            let resource_dir = app.path().resource_dir()?;
            app.manage(LocalRuntime::from_resource_dir(resource_dir));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            start_runtime,
            stop_runtime,
            configuration_status,
            configuration_details,
            save_configuration,
            set_configuration_secret,
            clear_configuration_secret,
            open_control_plane
        ])
        .run(tauri::generate_context!())
        .expect("AgentHub desktop shell failed");
}
