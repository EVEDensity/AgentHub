//! Stack bootstrap downloader (north-star M3 / §4.0 product baseline).
//!
//! Rust twin of `app/cli/stack_installer.py`: the desktop shell's first
//! run fetches a stack manifest from the release source, downloads
//! every listed file into `<data>/stacks/<version>-<commit>/` with
//! sha256 verification and `*.part` staging (verified files are skipped
//! on retry — resume), writes the manifest copy where `services.rs`
//! discovers it, and pins atomically. A failed install never disturbs
//! the current pin; older stacks remain for rollback.

use crate::services::{version_dir_name_of, StackManifest};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use ureq::Agent;

const MANIFEST_SCHEMA_VERSION: u64 = 1;
const MANIFEST_FILE: &str = "stack-manifest.json";
const PIN_FILE: &str = ".pinned";
const SERVICES_DIR: &str = "local-services";
const HTTP_TIMEOUT_SECONDS: u64 = 120;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawManifest {
    schema_version: u64,
    version: String,
    commit: Option<String>,
    generated_at: Option<String>,
    files: Vec<RawFileEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawFileEntry {
    path: String,
    sha256: String,
    size: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProgressEvent {
    /// Relative path of the file just verified/downloaded.
    pub path: String,
    /// 1-based index of the file.
    pub index: u32,
    /// Total files in the manifest.
    pub total: u32,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapReport {
    /// Installed stack directory name (`<version>-<commit>`).
    pub directory: String,
    pub version: String,
    pub commit: String,
    /// Number of files in the installed stack.
    pub files: u32,
    /// Number of files actually downloaded this run (rest resumed).
    pub downloaded: u32,
}

#[derive(Debug)]
struct VerifiedEntry {
    path: String,
    sha256: String,
    size: u64,
}

/// Errors surface as strings through the Tauri command boundary.
#[derive(Debug)]
pub enum BootstrapError {
    ManifestFetch(String),
    ManifestInvalid(String),
    Download { path: String, reason: String },
    Integrity { path: String, expected: String, got: String },
    Io(String),
}

impl std::fmt::Display for BootstrapError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BootstrapError::ManifestFetch(reason) => write!(f, "无法获取栈清单: {reason}"),
            BootstrapError::ManifestInvalid(reason) => write!(f, "栈清单无效: {reason}"),
            BootstrapError::Download { path, reason } => {
                write!(f, "下载失败 {path}: {reason}")
            }
            BootstrapError::Integrity { path, expected, got } => {
                write!(f, "完整性校验失败 {path}: 期望 sha256 {expected}, 实际 {got}")
            }
            BootstrapError::Io(reason) => write!(f, "本地写入失败: {reason}"),
        }
    }
}

fn http_agent() -> Agent {
    Agent::config_builder()
        .timeout(Some(std::time::Duration::from_secs(HTTP_TIMEOUT_SECONDS)))
        .build()
        .into()
}

fn fetch_bytes(agent: &Agent, url: &str) -> Result<Vec<u8>, String> {
    let response = agent
        .get(url)
        .call()
        .map_err(|error| error.to_string())?;
    let mut bytes: Vec<u8> = Vec::new();
    response
        .into_reader()
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    Ok(bytes)
}

fn sha256_hex(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    let mut hex = String::with_capacity(64);
    for byte in digest {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

/// Parse and strictly validate a manifest; rejects schema drift,
/// empty file lists, traversal-shaped paths, and bad sizes.
fn parse_manifest(bytes: &[u8]) -> Result<(String, String, String, Vec<VerifiedEntry>), BootstrapError> {
    let raw: RawManifest = serde_json::from_slice(bytes)
        .map_err(|error| BootstrapError::ManifestInvalid(error.to_string()))?;
    if raw.schema_version != MANIFEST_SCHEMA_VERSION {
        return Err(BootstrapError::ManifestInvalid(format!(
            "不支持的 schemaVersion {}（期望 {}）",
            raw.schema_version, MANIFEST_SCHEMA_VERSION
        )));
    }
    if raw.version.trim().is_empty() {
        return Err(BootstrapError::ManifestInvalid("缺少 version".into()));
    }
    if raw.files.is_empty() {
        return Err(BootstrapError::ManifestInvalid("清单没有文件".into()));
    }
    let mut entries = Vec::with_capacity(raw.files.len());
    for file in raw.files {
        if file.path.trim().is_empty() || file.sha256.trim().is_empty() {
            return Err(BootstrapError::ManifestInvalid(format!(
                "文件条目缺少 path/sha256: {}",
                file.path
            )));
        }
        let candidate = Path::new(&file.path);
        if candidate.is_absolute() || file.path.contains("..") {
            return Err(BootstrapError::ManifestInvalid(format!(
                "清单路径越界: {}",
                file.path
            )));
        }
        entries.push(VerifiedEntry {
            path: file.path.replace('\\', "/"),
            sha256: file.sha256,
            size: file.size,
        });
    }
    Ok((
        raw.version,
        raw.commit.unwrap_or_default(),
        raw.generated_at.unwrap_or_default(),
        entries,
    ))
}

fn file_is_verified(path: &Path, expected_sha: &str, expected_size: u64) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() || metadata.len() != expected_size {
        return false;
    }
    match fs::read(path) {
        Ok(bytes) => sha256_hex(&bytes) == expected_sha,
        Err(_) => false,
    }
}

fn write_atomic(final_path: &Path, bytes: &[u8]) -> Result<(), BootstrapError> {
    let mut part_path = final_path.as_os_str().to_owned();
    part_path.push(".part");
    let part_path = PathBuf::from(part_path);
    let mut handle = fs::File::create(&part_path)
        .map_err(|error| BootstrapError::Io(error.to_string()))?;
    handle
        .write_all(bytes)
        .map_err(|error| BootstrapError::Io(error.to_string()))?;
    drop(handle);
    fs::rename(&part_path, final_path)
        .map_err(|error| BootstrapError::Io(error.to_string()))?;
    Ok(())
}

/// Pin atomically: write `.pinned.tmp`, then rename over `.pinned`.
fn write_pin(stacks_dir: &Path, name: &str) -> Result<(), BootstrapError> {
    let tmp = stacks_dir.join(format!("{PIN_FILE}.tmp"));
    fs::write(&tmp, name).map_err(|error| BootstrapError::Io(error.to_string()))?;
    fs::rename(&tmp, stacks_dir.join(PIN_FILE))
        .map_err(|error| BootstrapError::Io(error.to_string()))?;
    Ok(())
}

/// Download and verify one stack, then pin it.
///
/// `base_url` prefixes every manifest file path when the release source
/// serves files from a different root than the manifest. `on_progress`
/// is invoked per file (after verify or download). All IO and HTTP run
/// on the calling thread — the Tauri command wraps this in
/// `spawn_blocking`.
pub fn bootstrap_stack(
    manifest_url: &str,
    data_dir: &Path,
    base_url: &str,
    on_progress: &mut dyn FnMut(ProgressEvent),
) -> Result<BootstrapReport, BootstrapError> {
    let agent = http_agent();
    let manifest_bytes = fetch_bytes(&agent, manifest_url)
        .map_err(BootstrapError::ManifestFetch)?;
    let (version, commit, generated_at, entries) = parse_manifest(&manifest_bytes)?;
    let stack_name = version_dir_name_of(&version, &commit);
    let stacks_dir = data_dir.join("stacks");
    let stack_dir = stacks_dir.join(&stack_name);
    let services_dir = stack_dir.join(SERVICES_DIR);
    fs::create_dir_all(&services_dir).map_err(|error| BootstrapError::Io(error.to_string()))?;

    let total = entries.len() as u32;
    let mut downloaded: u32 = 0;
    for (index, entry) in entries.iter().enumerate() {
        let index = index as u32 + 1;
        let target = stack_dir.join(&entry.path);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|error| BootstrapError::Io(error.to_string()))?;
        }
        if file_is_verified(&target, &entry.sha256, entry.size) {
            on_progress(ProgressEvent { path: entry.path.clone(), index, total });
            continue; // resume: already verified
        }
        let url = if base_url.is_empty() {
            entry.path.clone()
        } else {
            format!("{}/{}", base_url.trim_end_matches('/'), entry.path.trim_start_matches('/'))
        };
        let payload = fetch_bytes(&agent, &url).map_err(|reason| BootstrapError::Download {
            path: entry.path.clone(),
            reason,
        })?;
        let digest = sha256_hex(&payload);
        if digest != entry.sha256 || payload.len() as u64 != entry.size {
            return Err(BootstrapError::Integrity {
                path: entry.path.clone(),
                expected: entry.sha256.clone(),
                got: digest,
            });
        }
        write_atomic(&target, &payload)?;
        downloaded += 1;
        on_progress(ProgressEvent { path: entry.path.clone(), index, total });
    }

    // Manifest copy goes exactly where services.rs discovers stacks.
    let manifest_copy = services_dir.join(MANIFEST_FILE);
    let manifest_document = serde_json::json!({
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "version": version,
        "commit": commit,
        "generatedAt": generated_at,
        "files": entries
            .iter()
            .map(|entry| serde_json::json!({
                "path": entry.path,
                "sha256": entry.sha256,
                "size": entry.size,
            }))
            .collect::<Vec<_>>(),
    });
    write_atomic(
        &manifest_copy,
        serde_json::to_vec_pretty(&manifest_document)
            .map_err(|error| BootstrapError::Io(error.to_string()))?
            .as_slice(),
    )?;
    write_pin(&stacks_dir, &stack_name)?;
    Ok(BootstrapReport {
        directory: stack_name,
        version,
        commit,
        files: total,
        downloaded,
    })
}

/// Re-export for command handlers that want to read the manifest copy
/// through the shared `services.rs` types.
pub fn installed_stack_manifest(stack_dir: &Path) -> Option<StackManifest> {
    crate::services::read_stack_manifest_public(&stack_dir.join(SERVICES_DIR).join(MANIFEST_FILE))
}

use std::io::Read as _;

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(path: &str, bytes: &[u8]) -> RawFileEntry {
        RawFileEntry {
            path: path.into(),
            sha256: sha256_hex(bytes),
            size: bytes.len() as u64,
        }
    }

    fn manifest_bytes(files: &[RawFileEntry]) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1,
            "version": "0.1.0",
            "commit": "deadbee",
            "generatedAt": "2026-08-31T00:00:00Z",
            "files": files,
        }))
        .unwrap()
    }

    #[test]
    fn parses_and_rejects_traversal() {
        let (version, commit, _, entries) =
            parse_manifest(&manifest_bytes(&[entry("local-services/app.exe", b"BIN")])).unwrap();
        assert_eq!(version, "0.1.0");
        assert_eq!(commit, "deadbee");
        assert_eq!(entries.len(), 1);

        let bad = serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1,
            "version": "x",
            "files": [{ "path": "../evil.exe", "sha256": "0".repeat(64), "size": 1 }],
        }))
        .unwrap();
        assert!(matches!(
            parse_manifest(&bad),
            Err(BootstrapError::ManifestInvalid(_))
        ));
    }

    #[test]
    fn rejects_schema_drift_and_empty_files() {
        let drift = serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 99, "version": "x", "files": [],
        }))
        .unwrap();
        assert!(matches!(
            parse_manifest(&drift),
            Err(BootstrapError::ManifestInvalid(_))
        ));
        let empty = serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1, "version": "x", "files": [],
        }))
        .unwrap();
        assert!(matches!(
            parse_manifest(&empty),
            Err(BootstrapError::ManifestInvalid(_))
        ));
    }

    #[test]
    fn atomic_write_and_pin() {
        let dir = std::env::temp_dir().join(format!("agenthub-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        write_atomic(&dir.join("f.bin"), b"DATA").unwrap();
        assert_eq!(fs::read(&dir.join("f.bin")).unwrap(), b"DATA");
        assert!(!dir.join("f.bin.part").exists());
        write_pin(&dir, "0.1.0-deadbee").unwrap();
        assert_eq!(fs::read_to_string(dir.join(PIN_FILE)).unwrap(), "0.1.0-deadbee");
        assert!(!dir.join(format!("{PIN_FILE}.tmp")).exists());
        let _ = fs::remove_dir_all(&dir);
    }
}
