use serde::Serialize;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;
use url::Url;

const PROTOCOL_VERSION: u16 = 1;
const DEFAULT_ENDPOINT: &str = "http://127.0.0.1:18097/readyz";
const MAX_REQUEST_BYTES: usize = 8 * 1024;
const MAX_HEADER_BYTES: usize = 4 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum ArtifactRootStatus {
    NotConfigured,
    Ready,
    Unavailable,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    protocol_version: u16,
    status: &'static str,
    artifact_root_status: ArtifactRootStatus,
}

struct RuntimeConfig {
    endpoint: SocketAddr,
    artifact_root_status: ArtifactRootStatus,
}

fn main() -> Result<(), String> {
    let config = parse_config().map_err(|error| format!("invalid runtime config: {error}"))?;
    let listener =
        TcpListener::bind(config.endpoint).map_err(|error| format!("health bind failed: {error}"))?;
    listener
        .set_nonblocking(false)
        .map_err(|error| format!("health listener setup failed: {error}"))?;

    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                if let Err(error) = handle_request(&mut stream, &config) {
                    let _ = write_response(&mut stream, 400, "bad request", None);
                    eprintln!("runtime request rejected: {error}");
                }
            }
            Err(error) => eprintln!("runtime accept failed: {error}"),
        }
    }
    Ok(())
}

fn parse_config() -> Result<RuntimeConfig, String> {
    let mut endpoint = DEFAULT_ENDPOINT.to_owned();
    let mut explicit_endpoint = false;
    let mut artifact_root: Option<PathBuf> = None;
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--health-endpoint" => {
                if explicit_endpoint {
                    return Err("--health-endpoint was supplied more than once".to_owned());
                }
                explicit_endpoint = true;
                endpoint = args
                    .next()
                    .ok_or_else(|| "--health-endpoint requires a value".to_owned())?;
            }
            "--artifact-root" => {
                if artifact_root.is_some() {
                    return Err("--artifact-root was supplied more than once".to_owned());
                }
                let value = args
                    .next()
                    .ok_or_else(|| "--artifact-root requires a value".to_owned())?;
                artifact_root = Some(PathBuf::from(value));
            }
            other => return Err(format!("unsupported argument {other}")),
        }
    }

    let artifact_root_status = match artifact_root {
        None => ArtifactRootStatus::NotConfigured,
        Some(path) => evaluate_artifact_root(&path),
    };

    Ok(RuntimeConfig {
        endpoint: parse_endpoint_value(&endpoint)?,
        artifact_root_status,
    })
}

fn evaluate_artifact_root(path: &Path) -> ArtifactRootStatus {
    if !path.is_absolute() {
        return ArtifactRootStatus::Unavailable;
    }
    if fs::create_dir_all(path).is_err() {
        return ArtifactRootStatus::Unavailable;
    }
    let probe = path.join(".agenthub-artifact-root-probe");
    match OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&probe)
        .and_then(|_| fs::remove_file(&probe))
    {
        Ok(()) => ArtifactRootStatus::Ready,
        Err(_) => ArtifactRootStatus::Unavailable,
    }
}

fn parse_endpoint_value(endpoint: &str) -> Result<SocketAddr, String> {
    let parsed = Url::parse(endpoint).map_err(|_| "endpoint is not a URL".to_owned())?;
    if parsed.scheme() != "http"
        || parsed.host_str() != Some("127.0.0.1")
        || parsed.path() != "/readyz"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("endpoint must be http://127.0.0.1:<port>/readyz".to_owned());
    }
    let port = parsed
        .port()
        .ok_or_else(|| "endpoint must include an explicit port".to_owned())?;
    if port == 0 {
        return Err("endpoint port must be non-zero".to_owned());
    }
    Ok(SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port))
}

fn handle_request(stream: &mut TcpStream, config: &RuntimeConfig) -> Result<(), String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    let mut request = Vec::with_capacity(1024);
    let mut buffer = [0_u8; 1024];
    loop {
        let read = stream
            .read(&mut buffer)
            .map_err(|error| error.to_string())?;
        if read == 0 {
            return Err("request ended before headers".to_owned());
        }
        request.extend_from_slice(&buffer[..read]);
        if request.len() > MAX_REQUEST_BYTES || request.len() > MAX_HEADER_BYTES {
            return Err("request headers exceed limit".to_owned());
        }
        if request.windows(4).any(|window| window == b"\r\n\r\n") {
            break;
        }
    }
    let request_text =
        std::str::from_utf8(&request).map_err(|_| "request is not UTF-8".to_owned())?;
    let request_line = request_text
        .lines()
        .next()
        .ok_or_else(|| "request line is missing".to_owned())?;
    let mut fields = request_line.split_whitespace();
    let method = fields.next().unwrap_or_default();
    let path = fields.next().unwrap_or_default();
    let version = fields.next().unwrap_or_default();
    if method != "GET" || path != "/readyz" || version != "HTTP/1.1" {
        write_response(stream, 404, "not found", None)?;
        return Ok(());
    }
    let body = serde_json::to_vec(&HealthResponse {
        protocol_version: PROTOCOL_VERSION,
        status: "ready",
        artifact_root_status: config.artifact_root_status,
    })
    .map_err(|error| error.to_string())?;
    write_response(stream, 200, "OK", Some(&body))
}

fn write_response(
    stream: &mut TcpStream,
    status: u16,
    reason: &str,
    body: Option<&[u8]>,
) -> Result<(), String> {
    let body = body.unwrap_or_default();
    let headers = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(headers.as_bytes())
        .and_then(|_| stream.write_all(body))
        .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        evaluate_artifact_root, handle_request, parse_config, parse_endpoint_value,
        ArtifactRootStatus,
    };
    use std::path::Path;
    use std::fs;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_artifact_root() -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock after epoch")
            .as_nanos();
        std::env::temp_dir().join(format!("agenthub-artifact-root-{suffix}"))
    }

    #[test]
    fn endpoint_defaults_to_loopback_ready_path() {
        let endpoint = parse_config().expect("default config").endpoint;
        assert_eq!(endpoint.ip().to_string(), "127.0.0.1");
        assert_eq!(endpoint.port(), 18097);
    }

    #[test]
    fn endpoint_rejects_remote_hosts_and_wrong_paths() {
        for endpoint in [
            "https://127.0.0.1:18097/readyz",
            "http://localhost:18097/readyz",
            "http://192.168.1.20:18097/readyz",
            "http://127.0.0.1:18097/health",
        ] {
            assert!(
                parse_endpoint_value(endpoint).is_err(),
                "accepted {endpoint}"
            );
        }
    }

    #[test]
    fn artifact_root_must_be_absolute_and_writable() {
        let root = temp_artifact_root();
        assert_eq!(evaluate_artifact_root(&root), ArtifactRootStatus::Ready);
        let _ = fs::remove_dir_all(&root);
        assert_eq!(
            evaluate_artifact_root(Path::new("relative/path")),
            ArtifactRootStatus::Unavailable
        );
    }

    #[test]
    fn readiness_request_returns_versioned_json_with_artifact_status() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener binds");
        let address = listener.local_addr().expect("listener address");
        let config = super::RuntimeConfig {
            endpoint: address,
            artifact_root_status: ArtifactRootStatus::Ready,
        };
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("client connects");
            handle_request(&mut stream, &config).expect("request handles");
        });
        let mut client = TcpStream::connect(address).expect("client connects");
        client
            .write_all(b"GET /readyz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            .expect("request writes");
        let mut response = String::new();
        client
            .read_to_string(&mut response)
            .expect("response reads");
        server.join().expect("server joins");
        assert!(
            response.starts_with("HTTP/1.1 200 OK"),
            "response: {response:?}"
        );
        assert!(response.contains("\"protocolVersion\":1"));
        assert!(response.contains("\"status\":\"ready\""));
        assert!(response.contains("\"artifactRootStatus\":\"ready\""));
    }
}
