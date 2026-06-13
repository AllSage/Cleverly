# External Agent Study Packs

Cleverly can track external learning and architecture references without
turning them into runtime dependencies. These packs are listed in the Training
Lab as offline study material only. They are not cloned, installed, fetched, or executed by Cleverly.

## Included References

| Source | Use in Cleverly | Runtime posture |
|---|---|---|
| [FareedKhan-dev/train-llm-from-scratch](https://github.com/FareedKhan-dev/train-llm-from-scratch) | Training fundamentals and small-model concepts for the local Training Lab. | Reference only. |
| [Sumanth077/Hands-On-AI-Engineering](https://github.com/Sumanth077/Hands-On-AI-Engineering) | Practical AI engineering patterns for offline workflows and agent experiments. | Reference only. |
| [elementalsouls/Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter) | Authorized security-assessment methodology and skill-pack structure. | Manual import only; never enabled by default. |
| [ConardLi/easy-agent](https://github.com/ConardLi/easy-agent) | Coding-agent architecture ideas: layered orchestration, tool permissions, sessions, MCP, context, and sandboxing. | Reference only. |

## Offline Intake Policy

- Do not add network buttons, auto-downloaders, or installer wrappers for these
  repositories in the offline runtime.
- Do not run upstream install scripts inside the sensitive/offline container.
- If material is copied in, keep it under `data/reference-packs` or
  user-managed `data/skills`, and preserve the upstream license text next to
  the copied material.
- Treat copied skills as drafts until reviewed, narrowed, and tested locally.
- Do not import payload collections, exploit automation, scanner launchers, or
  post-exploitation procedures into Cleverly's default shipped skill set.

## Security Pack Boundary

`Claude-BugHunter` is dual-use security material. Cleverly's safe integration
boundary is:

- owned systems,
- lab targets,
- CTFs,
- or systems covered by written authorization.

Cleverly should not ship this pack as a default enabled capability. If an
operator manually imports selected skills, keep them scoped to validation,
triage, evidence hygiene, reporting, and defensive review. Avoid importing
anything that would automate unauthorized scanning, exploitation, persistence,
credential abuse, data exfiltration, stealth, or command-and-control behavior.

## Connected Prep Workflow

Use a connected, non-sensitive prep machine when you need to inspect upstream
repos:

1. Review the upstream repository, license, scripts, dependencies, and recent
   history.
2. Select only the documents or skill files you actually need.
3. Remove commands that fetch from the internet, install packages, contact
   external services, or assume cloud API keys.
4. Copy the curated material to the offline machine as files, not as an
   installer.
5. Keep the original source URL, commit, and license with the copied files.
6. Run Cleverly's local skill audit before publishing imported skills.

## Current Implementation

Cleverly currently incorporates these packs only as:

- static Training Lab study-pack entries,
- this offline intake runbook,
- and project acknowledgments.

No upstream source code from these repositories is vendored into Cleverly.
