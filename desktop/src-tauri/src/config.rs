#[cfg(not(windows))]
compile_error!(
    "AgentHub desktop requires a native credential-store implementation for this target"
);

use serde::{Deserialize, Serialize};
use std::fmt::{Display, Formatter};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use url::Url;

pub const CONFIG_SCHEMA_VERSION: u16 = 1;
const CONFIG_FILE_NAME: &str = "config.json";
const KEYRING_SERVICE: &str = "com.agenthub.desktop";
const DEFAULT_MISSION_CONTROL_ENDPOINT: &str = "http://127.0.0.1:8080";
const DEFAULT_MCP_ENDPOINT: &str = "http://127.0.0.1:8099";

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopConfigInput {
    pub mission_control_endpoint: Option<String>,
    pub mcp_endpoint: Option<String>,
    pub artifact_directory: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopConfig {
    schema_version: u16,
    mission_control_endpoint: Option<String>,
    mcp_endpoint: Option<String>,
    artifact_directory: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Hash, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SecretKind {
    MissionControlToken,
    McpToken,
    ModelApiKey,
}

impl SecretKind {
    fn account(self) -> &'static str {
        match self {
            Self::MissionControlToken => "mission-control-token",
            Self::McpToken => "mcp-token",
            Self::ModelApiKey => "model-api-key",
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SecretInput {
    pub kind: SecretKind,
    pub value: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SecretAvailability {
    Missing,
    Configured,
    Unavailable,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfigurationStatus {
    pub schema_version: u16,
    pub mission_control_endpoint_configured: bool,
    pub mcp_endpoint_configured: bool,
    pub artifact_directory_configured: bool,
    pub mission_control_token: SecretAvailability,
    pub mcp_token: SecretAvailability,
    pub model_api_key: SecretAvailability,
    pub ready_for_runtime: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfigurationDetails {
    pub schema_version: u16,
    pub mission_control_endpoint: Option<String>,
    pub mcp_endpoint: Option<String>,
    pub artifact_directory: Option<String>,
    pub mission_control_token: SecretAvailability,
    pub mcp_token: SecretAvailability,
    pub model_api_key: SecretAvailability,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SecretStoreError {
    Unavailable,
}

pub trait SecretStore: Send + Sync {
    fn set(&self, kind: SecretKind, value: &str) -> Result<(), SecretStoreError>;
    fn get(&self, kind: SecretKind) -> Result<Option<String>, SecretStoreError>;
    fn delete(&self, kind: SecretKind) -> Result<(), SecretStoreError>;
}

#[derive(Default)]
pub struct KeyringSecretStore;

impl KeyringSecretStore {
    fn entry(kind: SecretKind) -> Result<keyring::Entry, SecretStoreError> {
        keyring::Entry::new(KEYRING_SERVICE, kind.account())
            .map_err(|_| SecretStoreError::Unavailable)
    }
}

impl SecretStore for KeyringSecretStore {
    fn set(&self, kind: SecretKind, value: &str) -> Result<(), SecretStoreError> {
        Self::entry(kind)?
            .set_password(value)
            .map_err(|_| SecretStoreError::Unavailable)
    }

    fn get(&self, kind: SecretKind) -> Result<Option<String>, SecretStoreError> {
        match Self::entry(kind)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(SecretStoreError::Unavailable),
        }
    }

    fn delete(&self, kind: SecretKind) -> Result<(), SecretStoreError> {
        match Self::entry(kind)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(SecretStoreError::Unavailable),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ConfigurationError {
    InvalidInput,
    UnsupportedSchema,
    Io,
    Serialization,
    SecretStoreUnavailable,
}

impl Display for ConfigurationError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        let message = match self {
            Self::InvalidInput => "configuration input is invalid",
            Self::UnsupportedSchema => "configuration schema is unsupported",
            Self::Io => "configuration storage is unavailable",
            Self::Serialization => "configuration could not be encoded",
            Self::SecretStoreUnavailable => "operating system credential storage is unavailable",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for ConfigurationError {}

pub struct ConfigurationStore {
    path: PathBuf,
    secrets: Arc<dyn SecretStore>,
}

impl ConfigurationStore {
    pub fn new(config_dir: PathBuf) -> Self {
        Self {
            path: config_dir.join(CONFIG_FILE_NAME),
            secrets: Arc::new(KeyringSecretStore),
        }
    }

    #[cfg(test)]
    fn with_secret_store(config_dir: PathBuf, secrets: Arc<dyn SecretStore>) -> Self {
        Self {
            path: config_dir.join(CONFIG_FILE_NAME),
            secrets,
        }
    }

    pub fn status(&self) -> Result<ConfigurationStatus, ConfigurationError> {
        let config = self.load_config()?;
        let mission_control_token = self.secret_status(SecretKind::MissionControlToken);
        let mcp_token = self.secret_status(SecretKind::McpToken);
        let model_api_key = self.secret_status(SecretKind::ModelApiKey);
        let ready_for_runtime = config.mission_control_endpoint.as_deref().map(str::trim).is_some_and(|value| !value.is_empty())
            && config.artifact_directory.as_deref().map(str::trim).is_some_and(|value| !value.is_empty());

        Ok(ConfigurationStatus {
            schema_version: config.schema_version,
            mission_control_endpoint_configured: config.mission_control_endpoint.as_deref().map(str::trim).is_some_and(|value| !value.is_empty()),
            mcp_endpoint_configured: config.mcp_endpoint.as_deref().map(str::trim).is_some_and(|value| !value.is_empty()),
            artifact_directory_configured: config.artifact_directory.as_deref().map(str::trim).is_some_and(|value| !value.is_empty()),
            mission_control_token,
            mcp_token,
            model_api_key,
            ready_for_runtime,
        })
    }

    pub fn details(&self) -> Result<ConfigurationDetails, ConfigurationError> {
        let config = self.load_config()?;
        Ok(ConfigurationDetails {
            schema_version: config.schema_version,
            mission_control_endpoint: config.mission_control_endpoint,
            mcp_endpoint: config.mcp_endpoint,
            artifact_directory: config.artifact_directory,
            mission_control_token: self.secret_status(SecretKind::MissionControlToken),
            mcp_token: self.secret_status(SecretKind::McpToken),
            model_api_key: self.secret_status(SecretKind::ModelApiKey),
        })
    }

    pub fn ensure_defaults(&self) -> Result<(), ConfigurationError> {
        let default_artifact_directory = self
            .path
            .parent()
            .ok_or(ConfigurationError::Io)?
            .join("artifacts");
        fs::create_dir_all(&default_artifact_directory).map_err(|_| ConfigurationError::Io)?;
        let mut config = if self.path.exists() {
            self.load_config()?
        } else {
            DesktopConfig {
                schema_version: CONFIG_SCHEMA_VERSION,
                ..DesktopConfig::default()
            }
        };
        let mut changed = false;
        if config.mission_control_endpoint.as_deref().map(str::trim).unwrap_or_default().is_empty() {
            config.mission_control_endpoint = Some(DEFAULT_MISSION_CONTROL_ENDPOINT.to_owned());
            changed = true;
        }
        if config.mcp_endpoint.as_deref().map(str::trim).unwrap_or_default().is_empty() {
            config.mcp_endpoint = Some(DEFAULT_MCP_ENDPOINT.to_owned());
            changed = true;
        }
        if config.artifact_directory.as_deref().map(str::trim).unwrap_or_default().is_empty() {
            config.artifact_directory = Some(default_artifact_directory.to_string_lossy().into_owned());
            changed = true;
        }
        if !changed && self.path.exists() {
            return Ok(());
        }
        let mut encoded =
            serde_json::to_vec_pretty(&config).map_err(|_| ConfigurationError::Serialization)?;
        encoded.push(b'\n');
        let parent = self.path.parent().ok_or(ConfigurationError::Io)?;
        fs::create_dir_all(parent).map_err(|_| ConfigurationError::Io)?;
        fs::write(&self.path, encoded).map_err(|_| ConfigurationError::Io)
    }

    pub fn save_config(
        &self,
        input: DesktopConfigInput,
    ) -> Result<ConfigurationStatus, ConfigurationError> {
        let config = DesktopConfig {
            schema_version: CONFIG_SCHEMA_VERSION,
            mission_control_endpoint: validate_endpoint(input.mission_control_endpoint)?,
            mcp_endpoint: validate_endpoint(input.mcp_endpoint)?,
            artifact_directory: validate_artifact_directory(input.artifact_directory)?,
        };
        let mut encoded =
            serde_json::to_vec_pretty(&config).map_err(|_| ConfigurationError::Serialization)?;
        encoded.push(b'\n');

        let parent = self.path.parent().ok_or(ConfigurationError::Io)?;
        fs::create_dir_all(parent).map_err(|_| ConfigurationError::Io)?;
        fs::write(&self.path, encoded).map_err(|_| ConfigurationError::Io)?;
        self.status()
    }

    pub fn set_secret(
        &self,
        input: SecretInput,
    ) -> Result<ConfigurationStatus, ConfigurationError> {
        if input.value.is_empty() {
            return Err(ConfigurationError::InvalidInput);
        }
        self.secrets
            .set(input.kind, &input.value)
            .map_err(|_| ConfigurationError::SecretStoreUnavailable)?;
        self.status()
    }

    pub fn clear_secret(
        &self,
        kind: SecretKind,
    ) -> Result<ConfigurationStatus, ConfigurationError> {
        self.secrets
            .delete(kind)
            .map_err(|_| ConfigurationError::SecretStoreUnavailable)?;
        self.status()
    }

    pub fn secret(&self, kind: SecretKind) -> Result<Option<String>, ConfigurationError> {
        self.secrets
            .get(kind)
            .map_err(|_| ConfigurationError::SecretStoreUnavailable)
    }

    pub(crate) fn mission_control_endpoint(&self) -> Result<Option<String>, ConfigurationError> {
        Ok(self.load_config()?.mission_control_endpoint)
    }

    fn load_config(&self) -> Result<DesktopConfig, ConfigurationError> {
        if !self.path.exists() {
            return Ok(DesktopConfig {
                schema_version: CONFIG_SCHEMA_VERSION,
                ..DesktopConfig::default()
            });
        }
        let bytes = fs::read(&self.path).map_err(|_| ConfigurationError::Io)?;
        let config: DesktopConfig =
            serde_json::from_slice(&bytes).map_err(|_| ConfigurationError::Serialization)?;
        if config.schema_version != CONFIG_SCHEMA_VERSION {
            return Err(ConfigurationError::UnsupportedSchema);
        }
        Ok(config)
    }

    fn secret_status(&self, kind: SecretKind) -> SecretAvailability {
        match self.secrets.get(kind) {
            Ok(Some(_)) => SecretAvailability::Configured,
            Ok(None) => SecretAvailability::Missing,
            Err(_) => SecretAvailability::Unavailable,
        }
    }
}

fn validate_endpoint(value: Option<String>) -> Result<Option<String>, ConfigurationError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }

    let parsed = Url::parse(value).map_err(|_| ConfigurationError::InvalidInput)?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(ConfigurationError::InvalidInput);
    }
    Ok(Some(value.to_owned()))
}

fn validate_artifact_directory(
    value: Option<String>,
) -> Result<Option<String>, ConfigurationError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }
    if !Path::new(value).is_absolute() {
        return Err(ConfigurationError::InvalidInput);
    }
    Ok(Some(value.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::{
        ConfigurationStore, DesktopConfigInput, SecretAvailability, SecretInput, SecretKind,
        SecretStore, SecretStoreError, DEFAULT_MCP_ENDPOINT, DEFAULT_MISSION_CONTROL_ENDPOINT,
    };
    use std::collections::HashMap;
    use std::fs;
    use std::path::Path;
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[derive(Default)]
    struct MemorySecretStore {
        values: Mutex<HashMap<SecretKind, String>>,
    }

    impl SecretStore for MemorySecretStore {
        fn set(&self, kind: SecretKind, value: &str) -> Result<(), SecretStoreError> {
            self.values
                .lock()
                .expect("memory secret store lock")
                .insert(kind, value.to_owned());
            Ok(())
        }

        fn get(&self, kind: SecretKind) -> Result<Option<String>, SecretStoreError> {
            Ok(self
                .values
                .lock()
                .expect("memory secret store lock")
                .get(&kind)
                .cloned())
        }

        fn delete(&self, kind: SecretKind) -> Result<(), SecretStoreError> {
            self.values
                .lock()
                .expect("memory secret store lock")
                .remove(&kind);
            Ok(())
        }
    }

    fn test_store() -> (ConfigurationStore, std::path::PathBuf) {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock after epoch")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("agenthub-config-{suffix}"));
        let store = ConfigurationStore::with_secret_store(
            dir.clone(),
            Arc::new(MemorySecretStore::default()),
        );
        (store, dir)
    }

    #[test]
    fn config_file_never_contains_secret_values() {
        let (store, dir) = test_store();
        let artifact_dir = std::env::temp_dir().to_string_lossy().into_owned();
        store
            .save_config(DesktopConfigInput {
                mission_control_endpoint: Some("https://control.example.test".into()),
                mcp_endpoint: Some("https://mcp.example.test".into()),
                artifact_directory: Some(artifact_dir),
            })
            .expect("config saves");
        store
            .set_secret(SecretInput {
                kind: SecretKind::MissionControlToken,
                value: "mission-token-never-on-disk".into(),
            })
            .expect("secret saves");

        let contents = fs::read_to_string(dir.join("config.json")).expect("config readable");
        assert!(!contents.contains("mission-token-never-on-disk"));
        assert_eq!(
            store.status().expect("status reads").mission_control_token,
            SecretAvailability::Configured
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn first_run_defaults_create_local_endpoints_and_artifacts() {
        let (store, dir) = test_store();
        store.ensure_defaults().expect("defaults save");
        let details = store.details().expect("details read");
        assert_eq!(details.mission_control_endpoint.as_deref(), Some(DEFAULT_MISSION_CONTROL_ENDPOINT));
        assert_eq!(details.mcp_endpoint.as_deref(), Some(DEFAULT_MCP_ENDPOINT));
        let artifact_directory = details.artifact_directory.expect("artifact directory");
        assert!(Path::new(&artifact_directory).is_dir());
        assert!(store.status().expect("status reads").ready_for_runtime);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn existing_partial_config_is_upgraded_with_local_defaults() {
        let (store, dir) = test_store();
        fs::create_dir_all(&dir).expect("config directory");
        fs::write(
            dir.join("config.json"),
            r#"{"schemaVersion":1,"missionControlEndpoint":null,"mcpEndpoint":null,"artifactDirectory":null}"#,
        )
        .expect("partial config writes");
        store.ensure_defaults().expect("defaults upgrade");
        let details = store.details().expect("details read");
        assert_eq!(details.mission_control_endpoint.as_deref(), Some(DEFAULT_MISSION_CONTROL_ENDPOINT));
        assert_eq!(details.mcp_endpoint.as_deref(), Some(DEFAULT_MCP_ENDPOINT));
        assert!(details.artifact_directory.is_some());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn endpoints_reject_credentials_and_non_http_schemes() {
        let (store, dir) = test_store();
        let result = store.save_config(DesktopConfigInput {
            mission_control_endpoint: Some("https://user:password@example.test".into()),
            mcp_endpoint: Some("file:///tmp/mcp".into()),
            artifact_directory: None,
        });
        assert!(result.is_err());
        assert!(!dir.join("config.json").exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn clearing_secret_is_idempotent_and_never_returns_value() {
        let (store, dir) = test_store();
        let status = store
            .clear_secret(SecretKind::McpToken)
            .expect("missing secret can be cleared");
        assert_eq!(status.mcp_token, SecretAvailability::Missing);
        store
            .set_secret(SecretInput {
                kind: SecretKind::McpToken,
                value: "mcp-token".into(),
            })
            .expect("secret saves");
        let status = store
            .clear_secret(SecretKind::McpToken)
            .expect("secret clears");
        assert_eq!(status.mcp_token, SecretAvailability::Missing);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn details_returns_non_sensitive_config_and_redacted_secret_status() {
        let (store, dir) = test_store();
        let artifact_dir = std::env::temp_dir().to_string_lossy().into_owned();
        store
            .save_config(DesktopConfigInput {
                mission_control_endpoint: Some("https://control.example.test".into()),
                mcp_endpoint: Some("https://mcp.example.test".into()),
                artifact_directory: Some(artifact_dir.clone()),
            })
            .expect("config saves");
        store
            .set_secret(SecretInput {
                kind: SecretKind::MissionControlToken,
                value: "never-returned-token".into(),
            })
            .expect("secret saves");

        let details = store.details().expect("details read");
        assert_eq!(
            details.mission_control_endpoint.as_deref(),
            Some("https://control.example.test")
        );
        assert_eq!(
            details.mcp_endpoint.as_deref(),
            Some("https://mcp.example.test")
        );
        assert_eq!(
            details.artifact_directory.as_deref(),
            Some(artifact_dir.as_str())
        );
        assert_eq!(
            details.mission_control_token,
            SecretAvailability::Configured
        );
        assert_eq!(details.mcp_token, SecretAvailability::Missing);
        let encoded = serde_json::to_string(&details).expect("details encode");
        assert!(!encoded.contains("never-returned-token"));
        let _ = fs::remove_dir_all(dir);
    }
}
