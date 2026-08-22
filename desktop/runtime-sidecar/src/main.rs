use serde::Serialize;
use std::env;
use std::io::{Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::time::Duration;
use url::Url;

const PROTOCOL_VERSION: u16 = 1;
const DEFAULT_ENDPOINT: &str = "http://127.0.0.1:18097/readyz";
const MAX_REQUEST_BYTES: usize = 8 * 1024;
const MAX_HEADER_BYTES: usize = 4 * 1024;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    protocol_version: u16,
    status: &'static str,
}

fn main() -> Result<(), String> {
    let endpoint = parse_endpoint().map_err(|error| format!("invalid health endpoint: {error}"))?;
    let listener =
        TcpListener::bind(endpoint).map_err(|error| format!("health bind failed: {error}"))?;
    listener
        .set_nonblocking(false)
        .map_err(|error| format!("health listener setup failed: {error}"))?;

    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                if let Err(error) = handle_request(&mut stream) {
                    let _ = write_response(&mut stream, 400, "bad request", None);
                    eprintln!("runtime request rejected: {error}");
                }
            }
            Err(error) => eprintln!("runtime accept failed: {error}"),
        }
    }
    Ok(())
}

fn parse_endpoint() -> Result<SocketAddr, String> {
    let mut endpoint = DEFAULT_ENDPOINT.to_owned();
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--health-endpoint" {
            endpoint = args
                .next()
                .ok_or_else(|| "--health-endpoint requires a value".to_owned())?;
        } else {
            return Err(format!("unsupported argument {arg}"));
        }
    }

    let parsed = Url::parse(&endpoint).map_err(|_| "endpoint is not a URL".to_owned())?;
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

fn handle_request(stream: &mut TcpStream) -> Result<(), String> {
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
    })
    .map_err(|error| error.to_string())?;
    write_response(stream, 200, "ok", Some(&body))
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
    use super::parse_endpoint;

    #[test]
    fn endpoint_defaults_to_loopback_ready_path() {
        let endpoint = parse_endpoint().expect("default endpoint");
        assert_eq!(endpoint.ip().to_string(), "127.0.0.1");
        assert_eq!(endpoint.port(), 18097);
    }
}
