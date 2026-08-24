use crate::protocol::{ControlPlaneReachability, ControlPlaneSnapshot};
use serde::Deserialize;
use std::io::Read;
use std::time::Duration;
use url::Url;

const HEALTH_PATH: &str = "/api/health";
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_BODY_BYTES: u64 = 8 * 1024;

#[derive(Deserialize)]
struct HealthBody {
    status: String,
}

pub fn probe_control_plane(endpoint: Option<&str>, token: Option<&str>) -> ControlPlaneSnapshot {
    let Some(endpoint) = endpoint.filter(|value| !value.is_empty()) else {
        return snapshot(ControlPlaneReachability::NotConfigured);
    };
    let Ok(origin) = Url::parse(endpoint) else {
        return snapshot(ControlPlaneReachability::Unhealthy);
    };
    let Ok(health_url) = health_url(&origin) else {
        return snapshot(ControlPlaneReachability::Unhealthy);
    };

    let agent = ureq::AgentBuilder::new()
        .timeout(PROBE_TIMEOUT)
        .redirects(0)
        .user_agent("AgentHub-Desktop/0.1")
        .build();
    let mut request = agent.get(health_url.as_str());
    if let Some(token) = token.filter(|value| !value.is_empty()) {
        if may_attach_token(&health_url) {
            request = request.set("Authorization", &format!("Bearer {token}"));
        }
    }

    match request.call() {
        Ok(response) => classify_response(response),
        Err(ureq::Error::Status(_, response)) => classify_response(response),
        Err(ureq::Error::Transport(_)) => snapshot(ControlPlaneReachability::Unreachable),
    }
}

fn health_url(origin: &Url) -> Result<Url, ()> {
    let mut health = origin.clone();
    health.set_path(HEALTH_PATH);
    health.set_query(None);
    health.set_fragment(None);
    if !health.username().is_empty() {
        let _ = health.set_username("");
    }
    let _ = health.set_password(None);
    Ok(health)
}

fn may_attach_token(url: &Url) -> bool {
    url.scheme() == "https"
        || matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
}

fn classify_response(response: ureq::Response) -> ControlPlaneSnapshot {
    let status = response.status();
    let body = read_body(response);
    let reachability = match status {
        401 | 403 => ControlPlaneReachability::Unauthorized,
        200 if is_healthy_contract(&body) => ControlPlaneReachability::Reachable,
        _ => ControlPlaneReachability::Unhealthy,
    };
    snapshot(reachability)
}

fn read_body(response: ureq::Response) -> String {
    let mut body = String::new();
    let _ = response
        .into_reader()
        .take(MAX_BODY_BYTES)
        .read_to_string(&mut body);
    body
}

fn is_healthy_contract(body: &str) -> bool {
    serde_json::from_str::<HealthBody>(body)
        .ok()
        .is_some_and(|health| matches!(health.status.as_str(), "ok" | "ready" | "healthy"))
}

fn snapshot(reachability: ControlPlaneReachability) -> ControlPlaneSnapshot {
    ControlPlaneSnapshot {
        endpoint_configured: reachability != ControlPlaneReachability::NotConfigured,
        detail: detail(reachability).to_owned(),
        reachability,
    }
}

fn detail(reachability: ControlPlaneReachability) -> &'static str {
    match reachability {
        ControlPlaneReachability::NotConfigured => "Mission Control endpoint is not configured.",
        ControlPlaneReachability::Unreachable => {
            "Mission Control did not respond within the probe timeout."
        }
        ControlPlaneReachability::Unauthorized => {
            "Mission Control rejected the stored credentials."
        }
        ControlPlaneReachability::Unhealthy => {
            "Mission Control did not return a valid health contract."
        }
        ControlPlaneReachability::Reachable => "Mission Control health endpoint is reachable.",
    }
}

#[cfg(test)]
mod tests {
    use super::{health_url, may_attach_token, probe_control_plane};
    use crate::protocol::ControlPlaneReachability;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;
    use std::time::Duration;
    use url::Url;

    fn start_server(response: String) -> (Url, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("probe listener binds");
        let address = listener.local_addr().expect("listener address");
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("probe request arrives");
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut buffer = vec![0_u8; 4096];
            let read = stream.read(&mut buffer).unwrap_or(0);
            let request = String::from_utf8_lossy(&buffer[..read]).into_owned();
            let _ = stream.write_all(response.as_bytes());
            request
        });
        (
            Url::parse(&format!("http://127.0.0.1:{}", address.port())).expect("loopback origin"),
            handle,
        )
    }

    fn http_response(status_line: &str, body: &str) -> String {
        format!(
            "{status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
    }

    #[test]
    fn missing_endpoint_does_not_touch_the_network() {
        let snapshot = probe_control_plane(None, Some("secret-token"));
        assert_eq!(
            snapshot.reachability,
            ControlPlaneReachability::NotConfigured
        );
        assert!(!snapshot.endpoint_configured);
        let encoded = serde_json::to_string(&snapshot).expect("snapshot encodes");
        assert!(!encoded.contains("secret-token"));
    }

    #[test]
    fn health_url_uses_origin_and_fixed_path() {
        let origin = Url::parse("https://control.example.test:8443/console").expect("origin");
        assert_eq!(
            health_url(&origin).expect("health url").as_str(),
            "https://control.example.test:8443/api/health"
        );
    }

    #[test]
    fn cleartext_remote_hosts_do_not_receive_authorization() {
        assert!(!may_attach_token(
            &Url::parse("http://control.example.test/api/health").expect("remote http")
        ));
        assert!(may_attach_token(
            &Url::parse("https://control.example.test/api/health").expect("https")
        ));
        assert!(may_attach_token(
            &Url::parse("http://127.0.0.1:8080/api/health").expect("loopback")
        ));
    }

    #[test]
    fn reachable_health_contract_is_accepted_without_leaking_secrets() {
        let body = r#"{"status":"ok","service":"AgentHub","token":"secret-from-body"}"#;
        let (endpoint, server) = start_server(http_response("HTTP/1.1 200 OK", body));
        let snapshot = probe_control_plane(Some(endpoint.as_str()), Some("secret-token"));
        let request = server.join().expect("server joins");
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Reachable);
        assert!(request.contains("GET /api/health"));
        assert!(request.contains("Authorization: Bearer secret-token"));
        let encoded = serde_json::to_string(&snapshot).expect("snapshot encodes");
        assert!(!encoded.contains("secret-token"));
        assert!(!encoded.contains("secret-from-body"));
    }

    #[test]
    fn refused_connection_is_unreachable() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("ephemeral bind");
        let port = listener.local_addr().expect("listener address").port();
        drop(listener);
        let snapshot = probe_control_plane(Some(&format!("http://127.0.0.1:{port}")), None);
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Unreachable);
        assert!(snapshot.endpoint_configured);
    }

    #[test]
    fn unauthorized_status_does_not_copy_response_body() {
        let (endpoint, server) = start_server(http_response(
            "HTTP/1.1 401 Unauthorized",
            r#"{"error":"not the token"}"#,
        ));
        let snapshot = probe_control_plane(Some(endpoint.as_str()), Some("secret-token"));
        let _ = server.join();
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Unauthorized);
        let encoded = serde_json::to_string(&snapshot).expect("snapshot encodes");
        assert!(!encoded.contains("not the token"));
        assert!(!encoded.contains("secret-token"));
    }

    #[test]
    fn html_success_is_unhealthy() {
        let (endpoint, server) = start_server(http_response(
            "HTTP/1.1 200 OK",
            "<html>Mission Control</html>",
        ));
        let snapshot = probe_control_plane(Some(endpoint.as_str()), None);
        let _ = server.join();
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Unhealthy);
    }

    #[test]
    fn redirects_are_not_followed() {
        let (endpoint, server) = start_server(
            "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/steal\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                .to_owned(),
        );
        let snapshot = probe_control_plane(Some(endpoint.as_str()), Some("secret-token"));
        let request = server.join().expect("server joins");
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Unhealthy);
        assert!(request.contains("GET /api/health"));
        assert!(!request.contains("/steal"));
    }
}
