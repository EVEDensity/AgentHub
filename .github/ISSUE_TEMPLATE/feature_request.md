---
name: 💡 Feature Request
description: Suggest a new feature or enhancement for AgentHub
title: '[Feature]: '
labels: ['enhancement', 'triage']
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the feature suggestion! Please provide as much context as you can.

  - type: textarea
    id: problem
    attributes:
      label: Is your feature request related to a problem?
      description: Describe the pain point this feature would solve.
      placeholder: I'm always frustrated when...
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: Proposed solution
      description: Describe what you'd like to see implemented.
      placeholder: It would be great if AgentHub could...
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: What workarounds or alternative solutions have you tried?
      placeholder: I've tried X and Y but they don't work because...

  - type: dropdown
    id: scope
    attributes:
      label: Scope
      description: Which area of the platform would this affect?
      multiple: true
      options:
        - Agent orchestration
        - Multi-Agent collaboration (AgentNet)
        - MCP / Tool integration
        - Knowledge base / RAG
        - Security (IAM / KMS / Audit)
        - Frontend UI / UX
        - Docker / Deployment
        - Documentation
        - SDK / API
        - Observability
        - Other

  - type: textarea
    id: context
    attributes:
      label: Additional context
      description: Add any other context, mockups, or references about the feature request here.
