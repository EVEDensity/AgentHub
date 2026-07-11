//! HTTP server for agentnet-core — exposes health, stats, and DAG management endpoints.
//!
//! Endpoints:
//! - `GET /healthz` — liveness check
//! - `GET /stats` — aggregate AgentNet statistics
//! - `GET /dags` — list all DAGs
//! - `GET /dags/{dag_id}` — get specific DAG
//! - `GET /dags/{dag_id}/ready` — get ready nodes
//! - `POST /dags` — create a new DAG
//! - `POST /dags/{dag_id}/node` — add/update a node
//! - `POST /dags/{dag_id}/edge` — add/remove an edge
//! - `GET /agents` — list registered agent capabilities
//! - `GET /agents/{agent_id}` — get specific agent capability

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::{Body, Request, Response, Server, Method, StatusCode};
use hyper::service::{make_service_fn, service_fn};

use crate::core::{AgentRegistry, DagEngine};
use crate::types::{Dag, DagNode, DagEdge};
use crate::AgentNetConfig;

/// HTTP server wrapper providing REST endpoints for AgentNet operations.
pub struct AgentNetHttpServer {
    engine: Arc<DagEngine>,
    registry: Arc<AgentRegistry>,
    addr: SocketAddr,
}

impl AgentNetHttpServer {
    pub fn new(engine: Arc<DagEngine>, registry: Arc<AgentRegistry>, addr: SocketAddr) -> Self {
        Self { engine, registry, addr }
    }

    /// Start serving HTTP requests (blocks until error).
    pub async fn serve(self) -> Result<(), hyper::Error> {
        let engine = self.engine;
        let registry = self.registry;

        let make_svc = make_service_fn(move |_conn| {
            let engine = engine.clone();
            let registry = registry.clone();
            async move {
                Ok::<_, hyper::Error>(service_fn(move |req| {
                    route_request(req, engine.clone(), registry.clone())
                }))
            }
        });

        let server = Server::bind(&self.addr).serve(make_svc);
        tracing::info!(addr = %self.addr, "agentnet-core HTTP server listening");
        server.await
    }
}

async fn route_request(
    req: Request<Body>,
    engine: Arc<DagEngine>,
    registry: Arc<AgentRegistry>,
) -> Result<Response<Body>, hyper::Error> {
    let path = req.uri().path().to_string();
    let method = req.method().clone();

    match (method, path.as_str()) {
        // Health
        (Method::GET, "/healthz") => Ok(json_response(200, &serde_json::json!({"status": "ok"}))),

        // Stats
        (Method::GET, "/stats") => {
            let stats = engine.stats().await;
            Ok(json_response(200, &stats))
        }

        // Agent registry
        (Method::GET, "/agents") => {
            let agents = registry.all().await;
            Ok(json_response(200, &agents))
        }

        // Agent by ID
        (Method::GET, path) if path.starts_with("/agents/") => {
            let agent_id = path.trim_start_matches("/agents/");
            let agents = registry.all().await;
            match agents.iter().find(|a| a.agent_id == agent_id) {
                Some(agent) => Ok(json_response(200, agent)),
                None => Ok(json_response(404, &serde_json::json!({"error": "agent not found"}))),
            }
        }

        // List DAGs
        (Method::GET, "/dags") => {
            let dags = engine.list_dags().await;
            Ok(json_response(200, &dags))
        }

        // Create DAG
        (Method::POST, "/dags") => {
            match deserialize_body::<Dag>(req).await {
                Ok(dag) => match engine.create_dag(&dag.dag_id, dag.name).await {
                    Ok(created) => Ok(json_response(201, &created)),
                    Err(e) => Ok(json_response(409, &serde_json::json!({"error": e}))),
                },
                Err(e) => Ok(json_response(400, &serde_json::json!({"error": e}))),
            }
        }

        // Get DAG / ready nodes
        (Method::GET, path) if path.starts_with("/dags/") => {
            let rest = path.trim_start_matches("/dags/");
            if rest.ends_with("/ready") {
                let dag_id = rest.trim_end_matches("/ready");
                match engine.ready_nodes(dag_id).await {
                    Ok(ready) => Ok(json_response(200, &serde_json::json!({
                        "dag_id": dag_id,
                        "ready": ready,
                        "total": ready.len(),
                    }))),
                    Err(e) => Ok(json_response(404, &serde_json::json!({"error": e}))),
                }
            } else {
                match engine.get_dag(rest).await {
                    Some(dag) => Ok(json_response(200, &dag)),
                    None => Ok(json_response(404, &serde_json::json!({"error": "dag not found"}))),
                }
            }
        }

        // Add node to DAG
        (Method::POST, path) if path.starts_with("/dags/") && path.ends_with("/node") => {
            let dag_id = path
                .trim_start_matches("/dags/")
                .trim_end_matches("/node");
            match deserialize_body::<DagNode>(req).await {
                Ok(node) => match engine.add_node(dag_id, node).await {
                    Ok(dag) => Ok(json_response(200, &dag)),
                    Err(e) => Ok(json_response(400, &serde_json::json!({"error": e}))),
                },
                Err(e) => Ok(json_response(400, &serde_json::json!({"error": e}))),
            }
        }

        // Add edge to DAG
        (Method::POST, path) if path.starts_with("/dags/") && path.ends_with("/edge") => {
            let dag_id = path
                .trim_start_matches("/dags/")
                .trim_end_matches("/edge");
            match deserialize_body::<DagEdge>(req).await {
                Ok(edge) => match engine.add_edge(dag_id, &edge.from, &edge.to, edge.label).await {
                    Ok(dag) => Ok(json_response(200, &dag)),
                    Err(e) => Ok(json_response(400, &serde_json::json!({"error": e}))),
                },
                Err(e) => Ok(json_response(400, &serde_json::json!({"error": e}))),
            }
        }

        // 404
        _ => Ok(json_response(404, &serde_json::json!({"error": "not found"}))),
    }
}

/// Deserialize the request body as JSON.
async fn deserialize_body<T: serde::de::DeserializeOwned>(
    req: Request<Body>,
) -> Result<T, String> {
    let body_bytes = hyper::body::to_bytes(req.into_body())
        .await
        .map_err(|e| format!("failed to read body: {}", e))?;
    serde_json::from_slice(&body_bytes).map_err(|e| format!("invalid json: {}", e))
}

/// Build a JSON HTTP response.
fn json_response<T: serde::Serialize>(status: u16, body: &T) -> Response<Body> {
    let json = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    Response::builder()
        .status(StatusCode::from_u16(status).unwrap_or(StatusCode::OK))
        .header("Content-Type", "application/json")
        .header("Access-Control-Allow-Origin", "*")
        .body(Body::from(json))
        .unwrap()
}
