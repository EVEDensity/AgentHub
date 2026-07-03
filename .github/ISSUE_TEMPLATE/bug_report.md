---
name: 🐛 Bug Report
description: Report a bug to help us improve AgentHub
title: '[Bug]: '
labels: ['bug', 'triage']
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to file a bug report! Please fill out the sections below.

  - type: input
    id: version
    attributes:
      label: AgentHub Version
      description: Which version are you running? (`git describe --tags` or Docker image tag)
      placeholder: e.g. v0.5.0, latest, main branch
    validations:
      required: true

  - type: textarea
    id: summary
    attributes:
      label: What happened?
      description: A clear, concise description of the bug.
      placeholder: When I do X, Y happens instead of Z...
    validations:
      required: true

  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      description: Provide the minimal steps to reproduce the bug.
      placeholder: |
        1. Go to '...'
        2. Click on '...'
        3. Run command '...'
        4. See error
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: What did you expect to happen?
      description: Describe what you expected to happen instead.
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Relevant logs or screenshots
      description: Paste logs, error messages, or screenshots. Use code blocks (```) for logs.
      render: shell

  - type: dropdown
    id: component
    attributes:
      label: Affected component
      multiple: true
      options:
        - Gateway (Go)
        - Orchestrator
        - MCP Gateway
        - Sandbox Service
        - Model Adapter (Python)
        - Knowledge Pipeline
        - Frontend (Next.js)
        - Documentation
        - Docker / Deployment
        - Other

  - type: input
    id: os
    attributes:
      label: Operating system
      placeholder: e.g. Ubuntu 24.04, macOS 14.5, Windows 11
