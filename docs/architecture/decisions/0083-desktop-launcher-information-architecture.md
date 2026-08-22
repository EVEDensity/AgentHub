# ADR-0083: Desktop Launcher Information Architecture

> Status: accepted
> Owner: desktop maintainers
> Date: 2026-08-22
> Scope: `desktop/ui/`, desktop-to-Mission-Control navigation

## Context

AgentHub's durable value is reliable execution in Mission Control: Missions,
Contracts, WorkUnits, Artifacts, Evidence, Decisions, and Outcomes. The native
desktop shell does not own those objects and should not become a second
dashboard. The previous shell used a large introduction and three explanatory
cards, which spent the first viewport describing architecture instead of
getting a user to the real work surface.

The referenced DeepSeek Harness Desktop project demonstrates a useful desktop
role: package the local runtime, own the window/lifecycle boundary, and bring
the primary work surface forward. AgentHub adopts that product shape without
copying its plugin model: the AgentHub desktop is a launcher and local-runtime
supervisor, while Mission Control remains the work surface.

## Decision

The desktop initial view contains only:

- AgentHub identity and local desktop state;
- a single Mission Control launch surface;
- local Runtime lifecycle and readiness state;
- connection feedback and refresh/action controls.

Mission lists, agent graphs, approval queues, Artifact/Evidence browsing,
model configuration, analytics, and protocol administration stay in the web
management backend. They may be opened from the desktop but are not duplicated
in the desktop shell.

The first viewport uses flat sections and separators. Shadows are reserved for
interactive controls and their pressed/hover affordance; informational surfaces
do not float or stack as decorative cards. Copy is short and operational, not a
marketing explanation of the architecture.

The native `open_control_plane` command reads only the validated, non-secret
Mission Control endpoint and asks the operating system to open it in the
default browser. It cannot open an unconfigured endpoint and never receives a
credential.

## Consequences

- The desktop stays useful even as the management backend evolves independently.
- The initial viewport has a small, stable visual and behavioral contract.
- Onboarding remains the next product step; until configured, the launch action
  is visibly disabled and Runtime start remains fail-closed.
- Desktop and backend testing can proceed independently without duplicating
  business state.

## Verification

- Static UI checks cover the rewritten DOM and JavaScript syntax.
- Browser verification confirms the compact launcher renders at wide and narrow
  viewports and does not claim a connection without native configuration.
