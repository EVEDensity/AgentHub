FROM rust:1.79 AS builder
WORKDIR /src
COPY services/rust /src/services/rust
WORKDIR /src/services/rust
RUN cargo build -p stream-core --release

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=builder /src/services/rust/target/release/deps /app/deps
CMD ["/bin/sh", "-c", "echo stream-core built; ls -1 /app/deps | head"]
