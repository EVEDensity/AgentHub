# ── Build Stage ─────────────────────────────────────────────────────────
FROM rust:1.78-slim-bookworm AS builder

WORKDIR /app

# Copy workspace workspace-level manifests first for dependency caching.
COPY Cargo.toml Cargo.lock ./
COPY crates/platform-events/Cargo.toml crates/platform-events/
COPY crates/agentnet-core/Cargo.toml crates/agentnet-core/

# Dummy source stubs so cargo can resolve dependencies.
RUN mkdir -p crates/platform-events/src crates/agentnet-core/src && \
    echo 'fn main() {}' > crates/platform-events/src/lib.rs && \
    echo 'fn main() {}' > crates/agentnet-core/src/lib.rs

# Build just the dependencies (will be cached unless Cargo.toml changes).
RUN cargo build --release -p agentnet-core --bin agentnet-core-service 2>/dev/null || true

# Now copy real source.
COPY crates/platform-events/src crates/platform-events/src/
COPY crates/agentnet-core/src crates/agentnet-core/src/

# Force rebuild of only the agentnet-core crate.
RUN cargo build --release -p agentnet-core --bin agentnet-core-service

# ── Runtime Stage ───────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/agentnet-core-service /usr/local/bin/agentnet-core-service

EXPOSE 8107
ENTRYPOINT ["/usr/local/bin/agentnet-core-service"]
