use crate::protocol::{ControlPlaneReachability, ControlPlaneSnapshot};
use serde::Deserialize;
use std::io::Read;
use std::time::Duration;
use url::Url;

const HEALTH_PATH: &str = "/api/health";
const SESSION_PATH: &str = "/api/auth/me";
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_BODY_BYTES: u64 = 8 * 1024;

#[derive(Deserialize)]
struct HealthBody {
    status: String,
}

#[derive(Deserialize)]
struct SessionBody {
    id: Option<String>,
}

pub fn probe_control_plane(endpoint: Option<&str>, token: Option<&str>) -> ControlPlaneSnapshot {
    let Some(endpoint) = endpoint.filter(|value| !value.is_empty()) else {
        return snapshot(ControlPlaneReachability::NotConfigured);
    };
    let Ok(origin) = Url::parse(endpoint) else {
        return snapshot(ControlPlaneReachability::Unhealthy);
    };

    let health_url = match join_origin_path(&origin, HEALTH_PATH) {
        Ok(url) => url,
        Err(()) => return snapshot(ControlPlaneReachability::Unhealthy),
    };
    let health = probe_url(&health_url, None, classify_health);
    if health.reachability != ControlPlaneReachability::Reachable {
        return health;
    }

    let Some(token) = token.filter(|value| !value.is_empty()) else {
        if is_loopback_origin(&origin) {
            return health;
        }
        return ControlPlaneSnapshot {
            endpoint_configured: true,
            reachability: ControlPlaneReachability::Unauthorized,
            detail: "Mission Control token is not configured.".to_owned(),
        };
    };

    let session_url = match join_origin_path(&origin, SESSION_PATH) {
        Ok(url) => url,
        Err(()) => return snapshot(ControlPlaneReachability::Unhealthy),
    };
    probe_url(&session_url, Some(token), classify_session)
}

fn is_loopback_origin(origin: &Url) -> bool {
    matches!(origin.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
        && origin.port_or_known_default().is_some_and(|port| (28_000..=28_999).contains(&port))
}

fn probe_url<F>(url: &Url, token: Option<&str>, classify: F) -> ControlPlaneSnapshot
where
    F: FnOnce(u16, &str) -> ControlPlaneReachability,
{
    let agent = ureq::AgentBuilder::new()
        .timeout(PROBE_TIMEOUT)
        .redirects(0)
        .user_agent("AgentHub-Desktop/0.1")
        .build();
    let mut request = agent.get(url.as_str());
    if let Some(token) = token.filter(|value| !value.is_empty()) {
        if may_attach_token(url) {
            request = request.set("Authorization", &format!("Bearer {token}"));
        }
    }

    match request.call() {
        Ok(response) => {
            let status = response.status();
            let body = read_body(response);
            snapshot(classify(status, &body))
        }
        Err(ureq::Error::Status(status, response)) => {
            snapshot(classify(status, &read_body(response)))
        }
        Err(ureq::Error::Transport(_)) => snapshot(ControlPlaneReachability::Unreachable),
    }
}

fn join_origin_path(origin: &Url, path: &str) -> Result<Url, ()> {
    let mut url = origin.clone();
    url.set_path(path);
    url.set_query(None);
    url.set_fragment(None);
    if !url.username().is_empty() {
        let _ = url.set_username("");
    }
    let _ = url.set_password(None);
    Ok(url)
}

fn may_attach_token(url: &Url) -> bool {
    url.scheme() == "https"
        || matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
}

fn classify_health(status: u16, body: &str) -> ControlPlaneReachability {
    match status {
        401 | 403 => ControlPlaneReachability::Unauthorized,
        200 if is_healthy_contract(body) => ControlPlaneReachability::Reachable,
        _ => ControlPlaneReachability::Unhealthy,
    }
}

fn classify_session(status: u16, body: &str) -> ControlPlaneReachability {
    match status {
        401 | 403 => ControlPlaneReachability::Unauthorized,
        200 if is_authenticated_session(body) => ControlPlaneReachability::Reachable,
        _ => ControlPlaneReachability::Unhealthy,
    }
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

fn is_authenticated_session(body: &str) -> bool {
    serde_json::from_str::<SessionBody>(body)
        .ok()
        .and_then(|session| session.id)
        .is_some_and(|id| !id.is_empty())
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
            "Mission Control did not return a valid health or session contract."
        }
        ControlPlaneReachability::Reachable => {
            "Mission Control health and session probes succeeded."
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        classify_health, classify_session, join_origin_path, may_attach_token,
        probe_control_plane,
    };
    use crate::protocol::ControlPlaneReachability;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};
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

    fn start_routing_server(
        routes: Vec<(&'static str, String)>,
    ) -> (Url, thread::JoinHandle<Vec<String>>) {
        let routes = Arc::new(Mutex::new(
            routes
                .into_iter()
                .map(|(path, response)| (path.to_owned(), response))
                .collect::<Vec<_>>(),
        ));
        let listener = TcpListener::bind("127.0.0.1:0").expect("probe listener binds");
        let address = listener.local_addr().expect("listener address");
        let routes_for_thread = Arc::clone(&routes);
        let handle = thread::spawn(move || {
            let mut requests = Vec::new();
            let route_count = routes_for_thread.lock().expect("routes lock").len();
            while requests.len() < route_count {
                let (mut stream, _) = listener.accept().expect("probe request arrives");
                let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                let mut buffer = Vec::new();
                let mut chunk = [0_u8; 4096];
                loop {
                    if requests.len() >= route_count {
                        break;
                    }
                    match stream.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(read) => {
                            buffer.extend_from_slice(&chunk[..read]);
                            if !buffer.windows(4).any(|window| window == b"\r\n\r\n") {
                                continue;
                            }
                            let request = String::from_utf8_lossy(&buffer).into_owned();
                            requests.push(request.clone());
                            let path = request
                                .lines()
                                .next()
                                .and_then(|line| line.split_whitespace().nth(1))
                                .unwrap_or_default()
                                .to_owned();
                            let response = routes_for_thread
                                .lock()
                                .expect("routes lock")
                                .iter()
                                .find(|(route, _)| route == &path)
                                .map(|(_, response)| response.clone())
                                .unwrap_or_else(|| {
                                    "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                                        .to_owned()
                                });
                            let _ = stream.write_all(response.as_bytes());
                            buffer.clear();
                        }
                        Err(_) => break,
                    }
                }
            }
            requests
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
            join_origin_path(&origin, "/api/health")
                .expect("health url")
                .as_str(),
            "https://control.example.test:8443/api/health"
        );
        assert_eq!(
            join_origin_path(&origin, "/api/auth/me")
                .expect("session url")
                .as_str(),
            "https://control.example.test:8443/api/auth/me"
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
    fn configured_endpoint_without_token_is_unauthorized() {
        let body = r#"{"status":"ok","service":"AgentHub"}"#;
        let (endpoint, server) = start_server(http_response("HTTP/1.1 200 OK", body));
        let snapshot = probe_control_plane(Some(endpoint.as_str()), None);
        let _ = server.join();
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Unauthorized);
        assert!(snapshot.detail.contains("token is not configured"));
    }

    #[test]
    fn local_endpoint_without_token_uses_health_contract() {
        let snapshot = probe_control_plane(Some("http://127.0.0.1:1"), None);
        assert!(matches!(snapshot.reachability, ControlPlaneReachability::Unreachable));
    }

    #[test]
    fn reachable_session_contract_is_accepted_without_leaking_secrets() {
        let (endpoint, server) = start_routing_server(vec![
            (
                "/api/health",
                http_response("HTTP/1.1 200 OK", r#"{"status":"ok"}"#),
            ),
            (
                "/api/auth/me",
                http_response(
                    "HTTP/1.1 200 OK",
                    r#"{"id":"user-1","name":"operator","token":"secret-from-body"}"#,
                ),
            ),
        ]);
        let snapshot = probe_control_plane(Some(endpoint.as_str()), Some("secret-token"));
        let requests = server.join().expect("server joins");
        assert_eq!(snapshot.reachability, ControlPlaneReachability::Reachable);
        assert!(requests[0].contains("GET /api/health"));
        assert!(requests[1].contains("GET /api/auth/me"));
        assert!(requests[1].contains("Authorization: Bearer secret-token"));
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
    fn unauthorized_session_status_does_not_copy_response_body() {
        let (endpoint, server) = start_routing_server(vec![
            (
                "/api/health",
                http_response("HTTP/1.1 200 OK", r#"{"status":"ok"}"#),
            ),
            (
                "/api/auth/me",
                http_response(
                    "HTTP/1.1 401 Unauthorized",
                    r#"{"error":"not the token"}"#,
                ),
            ),
        ]);
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
        let snapshot = probe_control_plane(Some(endpoint.as_str()), Some("secret-token"));
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

    #[test]
    fn health_and_session_classifiers_are_explicit() {
        assert_eq!(
            classify_health(200, r#"{"status":"ok"}"#),
            ControlPlaneReachability::Reachable
        );
        assert_eq!(
            classify_session(200, r#"{"id":"user-1"}"#),
            ControlPlaneReachability::Reachable
        );
        assert_eq!(
            classify_session(200, r#"{"name":"missing-id"}"#),
            ControlPlaneReachability::Unhealthy
        );
    }
}
