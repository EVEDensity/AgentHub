#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod protocol;
mod runtime;

use config::{
    ConfigurationStatus, ConfigurationStore, DesktopConfigInput, SecretInput, SecretKind,
};
use protocol::RuntimeSnapshot;
use runtime::LocalRuntime;
use tauri::{Manager, State};

#[tauri::command]
fn runtime_status(runtime: State<'_, LocalRuntime>) -> RuntimeSnapshot {
    runtime.snapshot()
}

#[tauri::command]
fn start_runtime(runtime: State<'_, LocalRuntime>) -> RuntimeSnapshot {
    runtime.start()
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

fn main() {
    tauri::Builder::default()
        .manage(LocalRuntime::new())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            app.manage(ConfigurationStore::new(config_dir));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            start_runtime,
            stop_runtime,
            configuration_status,
            save_configuration,
            set_configuration_secret,
            clear_configuration_secret
        ])
        .run(tauri::generate_context!())
        .expect("AgentHub desktop shell failed");
}
