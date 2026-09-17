use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;

#[test]
fn compiled_sidecar_serves_loopback_readiness() {
    let reserved = TcpListener::bind("127.0.0.1:0").expect("reserve loopback port");
    let port = reserved.local_addr().expect("reserved address").port();
    drop(reserved);
    let endpoint = format!("http://127.0.0.1:{port}/readyz");
    let mut child = Command::new(env!("CARGO_BIN_EXE_agenthub-runtime"))
        .args(["--health-endpoint", endpoint.as_str()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("compiled sidecar starts");

    let result = (|| {
        let mut response = Vec::new();
        for _ in 0..40 {
            if child.try_wait().expect("sidecar status reads").is_some() {
                return Err("sidecar exited before readiness".to_owned());
            }
            match TcpStream::connect(("127.0.0.1", port)) {
                Ok(mut stream) => {
                    stream
                        .set_read_timeout(Some(Duration::from_millis(250)))
                        .expect("read timeout sets");
                    stream
                        .write_all(b"GET /readyz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                        .expect("readiness request writes");
                    stream
                        .read_to_end(&mut response)
                        .expect("readiness response reads");
                    break;
                }
                Err(_) => thread::sleep(Duration::from_millis(25)),
            }
        }
        let response = String::from_utf8(response).map_err(|_| "response is not UTF-8")?;
        if !response.starts_with("HTTP/1.1 200 OK") {
            return Err(format!("unexpected readiness response: {response}"));
        }
        if !response.contains("\"protocolVersion\":1") || !response.contains("\"status\":\"ready\"")
        {
            return Err(format!("readiness contract is incomplete: {response}"));
        }
        Ok(())
    })();

    let _ = child.kill();
    let _ = child.wait();
    result.expect("compiled sidecar readiness smoke passes");
}
