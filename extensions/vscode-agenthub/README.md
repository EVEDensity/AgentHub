# VS Code extension

The extension is a command and event projection for AgentHub Mission Control.
It does not own task state or imply that an admitted Mission has a live Runner.
Configure `agenthub.serverUrl`, `agenthub.apiKey` (a user token for protected
servers) and `agenthub.workspaceId` for a workspace that user can access. Reload
the extension after changing these settings.

## Supported flow

- The composer and Create Mission command send `message`, `workspaceId` and
  `stream` to `POST /api/v1/chat/mission`.
- A rule requiring confirmation displays Confirm and Cancel controls. Both use
  the admitted `pendingId`; confirmation attaches the resulting Mission stream.
- Cancel after admission calls the versioned Mission cancellation endpoint.
  Cancellation is displayed only after server acknowledgement; errors allow retry.
- Send Selection to Chat fills the draft with the selected code. The user sends
  it through the same text admission command. Failed submission keeps the draft.
- Server health means HTTP liveness only. Events project actual task state;
  attaching a stream does not prove execution. Stream authorization failures
  appear as errors.

HTTP commands have a 30-second deadline. Interrupted or malformed responses
fail the command and release the submission controls for retry. An interrupted
event response reports one transport error and schedules reconnection; EOF
never synthesizes a Mission completion event.

Mission listing and file-diff watching are not implemented. The sidebar states
that listing is unavailable and provides command shortcuts after a real health
probe. No file-diff command is advertised. Attachments, model/execution policy
selection and independent verification controls are not extension features yet.

## Development and verification

Run `npm ci`, then `npm test`. This compiles TypeScript and runs Node contract
and adapter tests against a local HTTP test server, including admission,
confirmation, both cancellation routes, HTTP/SSE failures and webview messaging.
`src/chatHtml.ts` contains the static browser projection; `ChatPanel` handles
commands through `AgentHubClient`. The checked-in `out/` files must be regenerated
by `npm run compile` when sources change. An existing `.vsix` is a prior packaged
release and does not automatically include source changes.

A real VS Code host and authenticated Runner integration still require manual
verification before publishing a new extension package.
