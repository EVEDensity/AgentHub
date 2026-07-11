package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── Video Handler ──────────────────────────────────────────────────────

// videoFrameRequest is the JSON body for POST /platform/utils/video-frames.
type videoFrameRequest struct {
	Video           string `json:"video"`            // base64-encoded video
	IntervalSeconds int    `json:"interval_seconds"` // seconds between frames (default 5)
	MaxFrames       int    `json:"max_frames"`       // max frames to extract (default 10)
}

// videoFrameResponse is the JSON response for the video frame extraction request.
type videoFrameResponse struct {
	Frames   []videoFrame `json:"frames"`
	Status   string       `json:"status"`
	Message  string       `json:"message"`
	VideoID  string       `json:"video_id"`
}

type videoFrame struct {
	Index     int    `json:"index"`
	Timestamp float64 `json:"timestamp_seconds"`
	Data      string `json:"data,omitempty"` // base64-encoded frame
}

// videoFrameStatus is the JSON response for GET /platform/utils/video-frames/{id}.
type videoFrameStatus struct {
	VideoID      string       `json:"video_id"`
	Status       string       `json:"status"` // pending, processing, completed, failed
	Frames       []videoFrame `json:"frames"`
	ErrorMessage string       `json:"error_message,omitempty"`
	CreatedAt    string       `json:"created_at"`
}

// videoJob tracks an in-flight video frame extraction request.
type videoJob struct {
	VideoID   string
	Status    string
	Frames    []videoFrame
	Error     string
	CreatedAt time.Time
}

type videoHandler struct {
	mu   sync.RWMutex
	bus  *eventbus.Client
	jobs map[string]*videoJob
}

func newVideoHandler(bus *eventbus.Client) *videoHandler {
	return &videoHandler{
		bus:  bus,
		jobs: make(map[string]*videoJob),
	}
}

func (vh *videoHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/utils/video-frames")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case rel == "" && r.Method == http.MethodPost:
		vh.handleExtractFrames(w, r)
	case rel != "" && r.Method == http.MethodGet:
		vh.handleGetStatus(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

func (vh *videoHandler) handleExtractFrames(w http.ResponseWriter, r *http.Request) {
	var req videoFrameRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}

	if req.Video == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "video field is required"})
		return
	}

	if req.IntervalSeconds <= 0 {
		req.IntervalSeconds = 5
	}
	if req.MaxFrames <= 0 {
		req.MaxFrames = 10
	}

	// Strip data URI prefix if present
	videoB64 := req.Video
	if idx := strings.Index(videoB64, "base64,"); idx != -1 {
		videoB64 = videoB64[idx+7:]
	}

	// Decode base64 to get raw video bytes
	rawBytes, err := base64.StdEncoding.DecodeString(videoB64)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid base64 video data"})
		return
	}

	// Generate video ID from hash
	h := sha256.Sum256(rawBytes)
	videoID := "vid-" + hex.EncodeToString(h[:8])

	// Store video to a temp location
	tempDir := filepath.Join(os.TempDir(), "agenthub-videos")
	if err := os.MkdirAll(tempDir, 0755); err != nil {
		log.Printf("video-handler: failed to create temp dir %s: %v", tempDir, err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "storage unavailable"})
		return
	}

	videoPath := filepath.Join(tempDir, videoID)
	if err := os.WriteFile(videoPath, rawBytes, 0644); err != nil {
		log.Printf("video-handler: failed to write video %s: %v", videoID, err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "write failed"})
		return
	}

	// Track the job
	now := time.Now().UTC()
	job := &videoJob{
		VideoID:   videoID,
		Status:    "pending",
		CreatedAt: now,
	}
	vh.mu.Lock()
	vh.jobs[videoID] = job
	vh.mu.Unlock()

	// Publish NATS event for async processing
	if vh.bus != nil {
		event := events.NewEnvelope(
			"video.frame_extraction.requested",
			"",
			"",
			"vid-trace-"+videoID,
			events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
			map[string]any{
				"video_id":         videoID,
				"video_path":       videoPath,
				"interval_seconds": req.IntervalSeconds,
				"max_frames":       req.MaxFrames,
			},
		)
		event.EventID = "vid-evt-" + videoID
		event.Routing = &events.Routing{
			Channel:      "video",
			PartitionKey: videoID,
			Priority:     events.PriorityNormal,
		}

		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if err := vh.bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
			log.Printf("video-handler: NATS publish error for %s: %v", videoID, err)
		} else {
			job.Status = "processing"
		}
	}

	resp := videoFrameResponse{
		Frames:  []videoFrame{},
		Status:  "video_received",
		Message: "Video frame extraction requires ffmpeg; video saved for async processing",
		VideoID: videoID,
	}

	log.Printf("video-handler: video %s received (%d bytes), saved to %s", videoID, len(rawBytes), videoPath)
	json.NewEncoder(w).Encode(resp)
}

func (vh *videoHandler) handleGetStatus(w http.ResponseWriter, _ *http.Request, videoID string) {
	vh.mu.RLock()
	job, ok := vh.jobs[videoID]
	vh.mu.RUnlock()

	if !ok {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "video not found"})
		return
	}

	resp := videoFrameStatus{
		VideoID:      job.VideoID,
		Status:       job.Status,
		Frames:       job.Frames,
		ErrorMessage: job.Error,
		CreatedAt:    job.CreatedAt.Format(time.RFC3339),
	}
	if resp.Frames == nil {
		resp.Frames = []videoFrame{}
	}
	json.NewEncoder(w).Encode(resp)
}
