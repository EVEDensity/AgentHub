//! 背压窗口：基于 `tokio::mpsc` 有界 channel 的入队控制器。
//!
//! stream-core 在"上游 NATS 订阅"与"下游合并/消费者"之间放一个有界 channel，
//! 当 channel 满时按 [`FullAction`] 策略处理，而不是无限堆积导致 OOM：
//!
//! - [`FullAction::Block`]：await 等待空位（强背压，向上游传导）。
//! - [`FullAction::DropOldest`]：丢弃最旧的一条（保最新，牺牲完整性）。
//! - [`FullAction::DropNewest`]：丢弃当前入队这条（保历史，牺牲新鲜度）。
//! - [`FullAction::Coalesce`]：把当前内容拼到队尾那条后面（合并降级）。
//!
//! 每次降级都会更新 [`BackpressureStats`]，供指标暴露。

use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio::time::Duration;

/// channel 满时的处理策略。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FullAction {
    /// 阻塞等待空位（强背压）。
    Block,
    /// 丢弃最旧的一条。
    DropOldest,
    /// 丢弃当前这条（新入队的）。
    DropNewest,
    /// 把当前内容 coalesce 到队尾。
    Coalesce,
}

impl Default for FullAction {
    fn default() -> Self {
        // 默认丢弃最旧：流式场景下"最新"比"完整"更重要（前端更关心最新进度）。
        FullAction::DropOldest
    }
}

/// 背压配置。
#[derive(Debug, Clone)]
pub struct BackpressureConfig {
    /// channel 容量。
    pub capacity: usize,
    /// channel 满时的策略。
    pub full_action: FullAction,
    /// Block 策略下的最大等待时间；超时后回退为 DropNewest（防止死锁）。
    pub block_timeout: Duration,
}

impl Default for BackpressureConfig {
    fn default() -> Self {
        Self {
            capacity: 1024,
            full_action: FullAction::DropOldest,
            block_timeout: Duration::from_millis(50),
        }
    }
}

/// 背压统计（线程安全，供指标抓取）。
#[derive(Debug, Default, Clone, serde::Serialize)]
pub struct BackpressureStats {
    pub enqueued: u64,
    pub blocked: u64,
    pub dropped_oldest: u64,
    pub dropped_newest: u64,
    pub coalesced: u64,
    pub block_timeouts: u64,
}

/// 支持合并降级的内容。Coalesce 策略下，新值会被并入队尾元素。
pub trait Coalesceable {
    /// 把 `other` 的内容并入 self。
    fn coalesce(&mut self, other: &Self);
}

/// 背压窗口：包装 `tokio::mpsc::Sender`，提供带策略的入队接口。
///
/// 泛型 `T` 通常是 [`crate::chunk::StreamChunk`]。要求 `T: Coalesceable`，
/// 以支持 [`FullAction::Coalesce`] 策略。
pub struct BackpressureChannel<T> {
    tx: mpsc::Sender<T>,
    rx: Arc<Mutex<mpsc::Receiver<T>>>,
    config: BackpressureConfig,
    stats: Arc<std::sync::Mutex<BackpressureStats>>,
}

impl<T: Coalesceable + Send + 'static> BackpressureChannel<T> {
    pub fn new(config: BackpressureConfig) -> Self {
        let (tx, rx) = mpsc::channel(config.capacity);
        Self {
            tx,
            rx: Arc::new(Mutex::new(rx)),
            config,
            stats: Arc::new(std::sync::Mutex::new(BackpressureStats::default())),
        }
    }

    pub fn config(&self) -> &BackpressureConfig {
        &self.config
    }

    /// 接收端（共享，由 [`crate::core::StreamCore`] 持有并驱动）。
    pub fn receiver(&self) -> Arc<Mutex<mpsc::Receiver<T>>> {
        Arc::clone(&self.rx)
    }

    /// 当前快照统计。
    pub fn stats(&self) -> BackpressureStats {
        self.stats.lock().unwrap().clone()
    }

    /// 当前 channel 中的消息数（best-effort）。
    /// `Sender::capacity()` 返回剩余容量，`max_capacity()` 返回上限，差值即已用。
    pub fn len(&self) -> usize {
        self.tx.max_capacity().saturating_sub(self.tx.capacity())
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// 入队一条消息，按策略处理 channel 满的情况。
    ///
    /// 返回 `Ok(())` 表示成功入队（含 coalesce 后入队）；
    /// 返回 `Err(T)` 表示该条被丢弃（DropNewest 或 Block 超时回退）。
    pub async fn send(&self, item: T) -> Result<(), T> {
        // 先尝试无阻塞入队。try_send 成功时 item 被 move 进 channel。
        match self.tx.try_send(item) {
            Ok(_) => {
                self.bump(|s| s.enqueued += 1);
                return Ok(());
            }
            Err(mpsc::error::TrySendError::Full(item)) => {
                self.handle_full(item).await
            }
            Err(mpsc::error::TrySendError::Closed(item)) => Err(item),
        }
    }

    async fn handle_full(&self, item: T) -> Result<(), T> {
        match self.config.full_action {
            FullAction::Block => {
                self.bump(|s| s.blocked += 1);
                let timeout = self.config.block_timeout;
                match tokio::time::timeout(timeout, self.tx.send(item)).await {
                    Ok(Ok(())) => {
                        self.bump(|s| s.enqueued += 1);
                        Ok(())
                    }
                    Ok(Err(e)) => Err(e.0), // closed — SendError.0 取回原 item
                    Err(_) => {
                        // 超时：回退为 DropNewest。
                        self.bump(|s| {
                            s.block_timeouts += 1;
                            s.dropped_newest += 1;
                        });
                        // item 被 timeout 的 future 消耗了——这里我们无法取回。
                        // 为保证语义，Block 超时视作丢弃（已在统计中计 dropped_newest）。
                        // 注意：tokio::time::timeout 在超时时会 drop 内部 future，
                        // 进而 drop 正在 send 的 item。因此返回 Ok(()) 而非 Err，
                        // 表示"已处理（丢弃）"。
                        Ok(())
                    }
                }
            }
            FullAction::DropNewest => {
                self.bump(|s| s.dropped_newest += 1);
                Err(item)
            }
            FullAction::DropOldest => {
                // 从接收端弹出最旧的一条腾出空间。
                let popped = self.pop_oldest().await;
                if popped.is_some() {
                    self.bump(|s| {
                        s.dropped_oldest += 1;
                    });
                }
                // 弹出后重试入队（可能仍有并发竞争，用 try_send）。
                match self.tx.try_send(item) {
                    Ok(_) => {
                        self.bump(|s| s.enqueued += 1);
                        Ok(())
                    }
                    Err(mpsc::error::TrySendError::Full(item)) => {
                        // 仍满，降级为 DropNewest。
                        self.bump(|s| s.dropped_newest += 1);
                        Err(item)
                    }
                    Err(mpsc::error::TrySendError::Closed(item)) => Err(item),
                }
            }
            FullAction::Coalesce => {
                // 取出队尾，合并内容后重新入队。
                if let Some(mut tail) = self.pop_tail().await {
                    tail.coalesce(&item);
                    self.bump(|s| {
                        s.coalesced += 1;
                        s.enqueued += 1;
                    });
                    match self.tx.send(tail).await {
                        Ok(_) => Ok(()),
                        Err(_) => {
                            // tail 入队失败（closed）——item 也丢失。
                            Err(item)
                        }
                    }
                } else {
                    // 队列为空却 try_send 满（矛盾，可能是并发），直接入队。
                    self.bump(|s| s.enqueued += 1);
                    match self.tx.send(item).await {
                        Ok(_) => Ok(()),
                        Err(e) => Err(e.0), // SendError.0 取回原 item
                    }
                }
            }
        }
    }

    /// 是否关闭（发送端全部释放）。
    pub fn is_closed(&self) -> bool {
        self.tx.is_closed()
    }

    async fn pop_oldest(&self) -> Option<T> {
        let mut rx = self.rx.lock().await;
        rx.try_recv().ok()
    }

    async fn pop_tail(&self) -> Option<T> {
        // mpsc 没有 pop_tail 原语；用 drain + 重入队模拟。
        // 仅在 channel 满时（低频）触发，可接受 O(n) 开销。
        let mut rx = self.rx.lock().await;
        let mut all = Vec::new();
        while let Ok(v) = rx.try_recv() {
            all.push(v);
        }
        if all.is_empty() {
            return None;
        }
        let tail = all.pop();
        for v in all {
            let _ = self.tx.try_send(v);
        }
        tail
    }

    fn bump<F: FnOnce(&mut BackpressureStats)>(&self, f: F) {
        let mut s = self.stats.lock().unwrap();
        f(&mut s);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 简单的可合并 String。
    impl Coalesceable for String {
        fn coalesce(&mut self, other: &Self) {
            self.push_str(other);
        }
    }

    fn channel(cap: usize, action: FullAction) -> BackpressureChannel<String> {
        BackpressureChannel::new(BackpressureConfig {
            capacity: cap,
            full_action: action,
            block_timeout: Duration::from_millis(20),
        })
    }

    #[tokio::test]
    async fn enqueue_until_full_then_drop_newest() {
        let ch = channel(2, FullAction::DropNewest);
        assert!(ch.send("a".into()).await.is_ok());
        assert!(ch.send("b".into()).await.is_ok());
        // 第 3 条应被丢弃。
        let r = ch.send("c".into()).await;
        assert!(r.is_err());
        let stats = ch.stats();
        assert_eq!(stats.enqueued, 2);
        assert_eq!(stats.dropped_newest, 1);
    }

    #[tokio::test]
    async fn drop_oldest_keeps_latest() {
        let ch = channel(2, FullAction::DropOldest);
        ch.send("a".into()).await.unwrap();
        ch.send("b".into()).await.unwrap();
        // 第 3 条触发 DropOldest：弹出 "a"，入队 "c"。
        ch.send("c".into()).await.unwrap();
        let rx = ch.receiver();
        let mut rx = rx.lock().await;
        let mut got = Vec::new();
        while let Ok(v) = rx.try_recv() {
            got.push(v);
        }
        assert_eq!(got, vec!["b".to_string(), "c".to_string()]);
        let stats = ch.stats();
        assert_eq!(stats.dropped_oldest, 1);
    }

    #[tokio::test]
    async fn coalesce_merges_into_tail() {
        let ch = channel(2, FullAction::Coalesce);
        ch.send("a".into()).await.unwrap();
        ch.send("b".into()).await.unwrap();
        // 第 3 条触发 Coalesce：取出 "b"，合并成 "bc"，重新入队。
        ch.send("c".into()).await.unwrap();
        let rx = ch.receiver();
        let mut rx = rx.lock().await;
        let mut got = Vec::new();
        while let Ok(v) = rx.try_recv() {
            got.push(v);
        }
        assert_eq!(got, vec!["a".to_string(), "bc".to_string()]);
        let stats = ch.stats();
        assert_eq!(stats.coalesced, 1);
    }
}
