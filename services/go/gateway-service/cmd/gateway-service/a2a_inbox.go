package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

func newA2AInboxHandler(selfCard *AgentCard, client *http.Client, trustPolicy A2ATrustPolicy, control a2aControlPlane) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		body := http.MaxBytesReader(w, r.Body, maxA2ATaskResponseBytes)
		defer body.Close()
		decoder := json.NewDecoder(body)
		decoder.DisallowUnknownFields()
		var request A2ATaskRequest
		if err := decoder.Decode(&request); err != nil {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32700, Message: "Parse error: " + err.Error()},
				ID:      "null",
			})
			return
		}
		if err := decoder.Decode(&struct{}{}); err != io.EOF {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32600, Message: "Invalid Request: exactly one JSON object is required"},
				ID:      request.ID,
			})
			return
		}
		if request.JSONRPC != "2.0" || strings.TrimSpace(request.Method) == "" {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32600, Message: "Invalid Request: jsonrpc 2.0 and method are required"},
				ID:      request.ID,
			})
			return
		}
		if _, present := request.Params["agentUrl"]; present {
			writeA2AInvalidParams(w, request.ID, "agentUrl is an outbound routing field and is not accepted by the A2A inbox")
			return
		}
		if _, present := request.Params["target"]; present {
			writeA2AInvalidParams(w, request.ID, "target is an outbound routing field and is not accepted by the A2A inbox")
			return
		}
		sourceAgentURL, _ := request.Params["sourceAgentUrl"].(string)
		sourceAgentURL = strings.TrimSpace(sourceAgentURL)
		if sourceAgentURL == "" {
			writeA2AInvalidParams(w, request.ID, "sourceAgentUrl is required")
			return
		}
		sourceOrigin, err := canonicalA2AOrigin(sourceAgentURL)
		if err != nil {
			writeA2AInvalidParams(w, request.ID, "sourceAgentUrl must be an absolute HTTP(S) origin")
			return
		}
		sourceAgentURL = sourceOrigin
		workspaceID, _ := request.Params["workspaceId"].(string)
		if strings.TrimSpace(workspaceID) == "" {
			writeA2AInvalidParams(w, request.ID, "workspaceId is required")
			return
		}
		taskID, _ := request.Params["id"].(string)
		if strings.TrimSpace(taskID) == "" {
			writeA2AInvalidParams(w, request.ID, "task id is required")
			return
		}
		if control == nil {
			writeA2AControlError(w, request.ID, taskID, fmt.Errorf("Mission control plane is not configured"))
			return
		}
		if _, err := probeAgentCard(r.Context(), client, sourceAgentURL, nil, trustPolicy); err != nil {
			writeJSON(w, http.StatusForbidden, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32003, Message: "Source Agent trust verification failed: " + err.Error()},
				ID:      request.ID,
			})
			return
		}

		switch request.Method {
		case "tasks/send":
			message := extractMessage(request.Params)
			objective := extractTextObjective(message)
			if objective == "" {
				writeA2AInvalidParams(w, request.ID, "message must contain a non-empty text part")
				return
			}
			requiredCapabilities, err := extractRequiredCapabilities(request.Params)
			if err != nil {
				writeA2AInvalidParams(w, request.ID, err.Error())
				return
			}
			if missing := missingAgentCapabilities(selfCard, requiredCapabilities); len(missing) > 0 {
				writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
					JSONRPC: "2.0",
					Error:   &A2AError{Code: -32005, Message: "AgentHub does not advertise required capabilities: " + strings.Join(missing, ", ")},
					ID:      request.ID,
				})
				return
			}
			controlTask, err := control.Accept(
				r.Context(),
				r.Header.Get("Authorization"),
				a2aControlAccept{
					TaskID:               taskID,
					WorkspaceID:          workspaceID,
					Objective:            objective,
					SourceAgentURL:       sourceAgentURL,
					RequiredCapabilities: requiredCapabilities,
				},
			)
			if err != nil {
				writeA2AControlError(w, request.ID, taskID, err)
				return
			}
			task := controlTask.toA2ATask()
			task.Message = message
			writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: task, ID: request.ID})

		case "tasks/cancel":
			controlTask, err := control.CancelInbound(
				r.Context(),
				r.Header.Get("Authorization"),
				workspaceID,
				sourceAgentURL,
				taskID,
			)
			if err != nil {
				writeA2AControlError(w, request.ID, taskID, err)
				return
			}
			writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: controlTask.toA2ATask(), ID: request.ID})

		case "tasks/get":
			controlTask, err := control.GetInbound(
				r.Context(),
				r.Header.Get("Authorization"),
				workspaceID,
				sourceAgentURL,
				taskID,
			)
			if err != nil {
				writeA2AControlError(w, request.ID, taskID, err)
				return
			}
			writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: controlTask.toA2ATask(), ID: request.ID})

		default:
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32601, Message: "Method not found: " + request.Method},
				ID:      request.ID,
			})
		}
	}
}
