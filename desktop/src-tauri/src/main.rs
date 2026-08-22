#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod protocol;
mod runtime;

use protocol::RuntimeSnapshot;
use runtime::LocalRuntime;
use tauri::State;

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

fn main() {
    tauri::Builder::default()
        .manage(LocalRuntime::new())
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            start_runtime,
            stop_runtime
        ])
        .run(tauri::generate_context!())
        .expect("AgentHub desktop shell failed");
}
