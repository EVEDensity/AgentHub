package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const maxControlPlaneResponseBytes = 1 << 20

type a2aControlTask struct {
	TaskID         string `json:"taskId"`
	AgentURL       string `json:"agentUrl"`
	State          string `json:"state"`
	MissionID      string `json:"missionId"`
	MissionStatus  string `json:"missionStatus"`
	WorkUnitID     string `json:"workUnitId"`
	WorkUnitStatus string `json:"workUnitStatus"`
}

func (task *a2aControlTask) toA2ATask() *A2ATask {
	if task == nil {
		return nil
	}
	return &A2ATask{
		ID:         task.TaskID,
		Status:     task.State,
		MissionID:  task.MissionID,
		WorkUnitID: task.WorkUnitID,
	}
}

type a2aControlSubmit struct {
	TaskID               string   `json:"taskId"`
	WorkspaceID          string   `json:"workspaceId"`
	Objective            string   `json:"objective"`
	AgentURL             string   `json:"agentUrl"`
	RequiredCapabilities []string `json:"requiredCapabilities,omitempty"`
}

type a2aControlPlane interface {
	Submit(context.Context, string, a2aControlSubmit) (*a2aControlTask, error)
	Get(context.Context, string, string, string) (*a2aControlTask, error)
	Cancel(context.Context, string, string, string) (*a2aControlTask, error)
	Fail(context.Context, string, string, string, string) (*a2aControlTask, error)
}

type a2aControlPlaneClient struct {
	baseURL string
	client  *http.Client
}

type a2aControlPlaneError struct {
	StatusCode int
	Detail     string
}

func (err *a2aControlPlaneError) Error() string {
	if err.Detail == "" {
		return fmt.Sprintf("control plane returned HTTP %d", err.StatusCode)
	}
	return fmt.Sprintf("control plane returned HTTP %d: %s", err.StatusCode, err.Detail)
}

func newA2AControlPlaneClient(baseURL string, client *http.Client) *a2aControlPlaneClient {
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	return &a2aControlPlaneClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		client:  client,
	}
}

func (client *a2aControlPlaneClient) Submit(ctx context.Context, authorization string, input a2aControlSubmit) (*a2aControlTask, error) {
	var task a2aControlTask
	if err := client.do(ctx, http.MethodPost, "/api/v1/a2a/tasks", authorization, input, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (client *a2aControlPlaneClient) Get(ctx context.Context, authorization, workspaceID, taskID string) (*a2aControlTask, error) {
	query := url.Values{"workspaceId": {workspaceID}, "taskId": {taskID}}
	var task a2aControlTask
	if err := client.do(ctx, http.MethodGet, "/api/v1/a2a/tasks?"+query.Encode(), authorization, nil, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (client *a2aControlPlaneClient) Cancel(ctx context.Context, authorization, workspaceID, taskID string) (*a2aControlTask, error) {
	var task a2aControlTask
	body := map[string]string{"workspaceId": workspaceID, "taskId": taskID}
	if err := client.do(ctx, http.MethodPost, "/api/v1/a2a/tasks/cancel", authorization, body, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (client *a2aControlPlaneClient) Fail(ctx context.Context, authorization, workspaceID, taskID, reason string) (*a2aControlTask, error) {
	var task a2aControlTask
	body := map[string]string{
		"workspaceId": workspaceID,
		"taskId":      taskID,
		"reason":      reason,
	}
	if err := client.do(ctx, http.MethodPost, "/api/v1/a2a/tasks/fail", authorization, body, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (client *a2aControlPlaneClient) do(ctx context.Context, method, path, authorization string, input, output any) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return fmt.Errorf("encode control-plane request: %w", err)
		}
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, client.baseURL+path, body)
	if err != nil {
		return fmt.Errorf("create control-plane request: %w", err)
	}
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if authorization != "" {
		req.Header.Set("Authorization", authorization)
	}

	resp, err := client.client.Do(req)
	if err != nil {
		return fmt.Errorf("call A2A control plane: %w", err)
	}
	defer resp.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(resp.Body, maxControlPlaneResponseBytes+1))
	if err != nil {
		return fmt.Errorf("read control-plane response: %w", err)
	}
	if len(responseBody) > maxControlPlaneResponseBytes {
		return fmt.Errorf("control-plane response exceeds %d bytes", maxControlPlaneResponseBytes)
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		var errorBody struct {
			Detail any `json:"detail"`
		}
		_ = json.Unmarshal(responseBody, &errorBody)
		detail := strings.TrimSpace(fmt.Sprint(errorBody.Detail))
		if detail == "<nil>" {
			detail = strings.TrimSpace(string(responseBody))
		}
		return &a2aControlPlaneError{StatusCode: resp.StatusCode, Detail: detail}
	}
	if err := json.Unmarshal(responseBody, output); err != nil {
		return fmt.Errorf("decode control-plane response: %w", err)
	}
	return nil
}
