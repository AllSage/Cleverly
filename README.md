# Cleverly

Local AI operating console for models, agents, tools, documents, research,
memory, automations, and sealed Docker operations.

![Cleverly](docs/cleverly-icon.svg)

Cleverly is a local-first control plane for the work people normally split
across ChatGPT, Claude, model servers, notes, documents, email, calendars,
task tools, shell sessions, and admin scripts. It runs on your hardware, keeps
your data under your control, and gives you one console for model setup, agent
workflows, knowledge, automation, and offline verification.

## Operating Console Pillars

- **Models**: local models and API providers, including Ollama, OpenAI-compatible endpoints, OpenRouter, OpenAI, hardware-aware recommendations, downloads, and serving through vLLM, llama.cpp, and related engines.
- **Agents and tools**: chat, autonomous agents, MCP, web, files, shell, skills, memory, task workflows, and bundled Agent Loops for tests, builds, security checks, docs sync, model onboarding, and release smoke runs.
- **Code operations**: import a local repo archive, edit files, apply diffs, run offline test/build commands, inspect git status/diff, and commit changes inside the sealed Docker data volume.
- **Knowledge and research**: persistent memory, reusable skills, ChromaDB/fastembed vector search, personal documents, Deep Research reports, and blind side-by-side model comparison.
- **Documents and daily work**: multi-tab documents with markdown, HTML, CSV, syntax highlighting, AI edits, suggestions, notes, todos, reminders, email triage, calendar sync, and `.ics` import/export.
- **Automation and notifications**: scheduled tasks, ntfy/browser/email notification channels, local response-complete alerts, webhooks when enabled, and background utility-model jobs.
- **Offline operations**: Docker sealed mode, explicit network break-glass controls, support-service profiles, backup/vault tooling, setup checks, and operator verification before sensitive work.
- **Labs and mobility**: Training Lab for offline starter text training, local datasets and saved model artifacts, plus a responsive installable PWA interface.

The Command Center dashboard includes a Toolchain band that inventories the
integrated local modules: Offline Control, Ollama, ChromaDB/RAG, SearXNG
research, Training Lab, Code Workspace, Voice I/O, Tasks, Calendar, Memory,
Notes, Library, Gallery, Agent Loops, backups, recovery, and Docker support
services. The operator API also exposes read-only command route proof through
`/api/operator/route` and `/api/operator/routes`, a read-only service repair
plan through `/api/operator/repair-plan` with alert rows, approval packet,
request-route proof, and handoff rows for status capture, backup checkpoints,
log review, data-boundary review, one-service repair, support-service startup,
activity evidence, and offline/no-pull policy, and a read-only note-to-task draft
with request-route proof through `/api/operator/note-task-draft`. Change Brief evidence is available
through `/api/operator/change-brief`, including dashboard/text/palette/voice/workflow
request-route proof, alert rows for missing workspace context, failed recent
activity, source-load issues, and the read-only git evidence boundary. Backup verification evidence is
available through `/api/operator/backup-plan`, including backup alert rows for
dashboard/text/palette/voice/workflow request-route proof, snapshot coverage,
audit evidence, restore-drill checks, approval gates, and handoff rows for
encrypted export, full snapshots, verification, restore drills, password
custody, storage location, and activity evidence. Recovery readiness evidence
is available through `/api/operator/recovery-plan`.
Activity timeline coverage is
available through `/api/operator/activity-plan`, Code test execution evidence is
available through `/api/operator/code-test-plan`, repeated build-watch evidence
is available through `/api/operator/build-watch-plan`, local document search
evidence and request-route proof are available through
`/api/operator/document-search-plan`, research and
SearXNG readiness evidence is available through `/api/operator/research-plan`,
gallery/media evidence is available through `/api/operator/gallery-plan`, local
workspace workbench evidence that unifies code, build-watch, local documents,
research, gallery/media, file gates, and data paths is available through
`/api/operator/workspace-plan`, local
calendar/reminder/sync evidence is available through
`/api/operator/calendar-plan`, scheduled task/run evidence is available through
`/api/operator/tasks-plan`, local training run evidence is available through
`/api/operator/training-plan`, and
Voice I/O readiness evidence is available through `/api/operator/voice-plan`.
Permissioned-autonomy evidence is available through
`/api/operator/autonomy-plan`, unified automation operations evidence for
scheduled tasks, Agent Loops, route proof, activity, build-watch, approvals, and
recovery handoffs is available through `/api/operator/automation-plan`, approval queue and permission posture evidence
with dashboard/text/palette/voice/workflow request-route proof is available
through `/api/operator/approval-plan`, Agent Loop readiness evidence
with dashboard/text/palette/voice/workflow request-route proof is available
through `/api/operator/loops-plan`, and unified-memory evidence is
available through `/api/operator/memory-plan`. Unified local work operations
evidence for today briefing, workday state, task automation, notes/reminders,
note-to-task drafts, calendar windows, request-route proof, and approval
boundaries is available through `/api/operator/work-ops-plan`.
Workday/scheduling evidence is available through
`/api/operator/workday-plan`, local model/training inventory snapshot evidence
is available through `/api/operator/models`, and local model operation evidence is available
through `/api/operator/model-ops-plan`. Unified local AI runtime evidence for
models, training, Ollama, ChromaDB, SearXNG, sealed volumes, and service gates is
available through `/api/operator/ai-runtime-plan`. Local Data Map boundary evidence is
available through `/api/operator/data-plan`, file operation evidence is available
through `/api/operator/file-ops-plan`, and runtime resource evidence is
available through `/api/operator/runtime-plan`, local service readiness evidence
with dashboard/text/palette/voice/workflow request-route proof and handoffs for
service probes, container status, repair-plan routing, backup/data boundaries,
host-command approval, support starts, no-pull policy, and activity evidence is
available through `/api/operator/services-plan`, unified Docker runtime
operations evidence for runtime mode, local services, captured container status,
host command gates, repair readiness, sealed volumes, AI support services, and
the image rebuild/recreate deployment boundary is available through
`/api/operator/docker-runtime-plan`, credential posture evidence
with dashboard/text/palette/voice/workflow request-route proof is available
through `/api/operator/credentials-plan`, backend command-layer readiness
evidence for catalog freshness, route-matrix health, entry points, trust
gates, and handoffs for text/palette/voice routing, policy, approvals,
workflow starts, activity writes, and network-risk escalation is available
through `/api/operator/command-layer-plan`, Command Center
situational-awareness evidence for system status, models, offline/security,
jobs, memory, work, code, research/library/gallery, training, alerts, and
handoffs for command routing, alert feeds, approvals, activity, recovery,
backup readiness, and safety policy is available through
`/api/operator/console-plan`, local toolchain integration evidence is available
through `/api/operator/toolchain-plan`, tool/skill/MCP access posture evidence
is available through `/api/operator/tool-access-plan`, safety-boundary evidence
is available through `/api/operator/safety-plan`, operating-console goal
readiness evidence with explicit capability coverage is available through
`/api/operator/goal-plan`, and backend target-experience
proof and target alert rows are available through
`/api/operator/experience-plan`. Notes/checklist/reminder evidence and
dashboard/text/palette/voice/workflow request-route proof are available
through `/api/operator/notes-plan`, so
target phrases, container repair requests, local note handoffs, backup
preparation, activity/retry reviews, code test requests, build-watch requests,
local document search requests, training requests, voice/text command requests,
memory/profile requests, "summarize today" requests, and
"what changed since yesterday" summaries can be
audited against persisted local catalogs, activity records, workspace metadata,
data paths, backup gates, candidate test/build commands, document index/RAG
metadata, document search route proof, Change Brief route proof, training
dataset/artifact ledgers, local memory/profile coverage, and current service
probes without executing anything.
Typed, palette, and voice command text use `/api/operator/route` as a
backend read-only route preflight before the browser executes a local command.
The Command Center and global Command Palette route previews also check that
backend route after a short debounce, so visible route proof, trust mode, and
approval requirement match what the typed command path will use.
In the Command Palette, pressing Enter routes the typed text through the
backend preflight; clicking a specific command row intentionally executes that
chosen command under the same trust controls.
If the backend route catalog is unavailable, stale, unauthenticated, or returns
no selected command, the browser falls back to its local matcher and then chat.
The repair plan lists suggested host Docker commands as evidence only; restarts,
starts, pulls, deletes, network use, and host filesystem changes still require
explicit approval. It also returns dashboard/text/palette/voice/workflow
route-proof rows, alert rows, and an approval packet with affected services,
candidate host commands, preflight checklist rows, and disallowed actions so
"check containers and fix anything unhealthy" has a clear local scope before any
owner-approved repair. The note-to-task endpoint returns
dashboard/text/palette/voice/workflow route-proof rows, local alert rows for
missing note sources/manual draft mode/save review, and a draft payload only;
saving or scheduling still happens from the Tasks review form. Opening a note
task draft from Command Center or the command fallback is mirrored to
`data/operator_activity.json` with note and draft metadata only; full note text
and draft prompts are not stored in the activity ledger. The backup plan
endpoint returns scope, evidence, approval rows, route-proof rows for
dashboard/text/palette/voice/workflow entry points, and a verification packet
with required artifacts, dry-run restore checks, snapshot verification checks,
pass criteria, and disallowed actions only; encrypted export, restore drill, full
snapshots, tarball verification, restore, uploads, moves, and deletion stay
behind explicit user actions. The activity-plan endpoint
audits status/result/log coverage, trust tags, retryable routes, failures,
pending work, recovery prompts, timeline alert rows for failures/gaps/retry and
delete gates, dashboard/text/palette/voice/workflow request-route proof,
handoff rows for Details, Copy Log, Retry checkpoint, Recovery/rollback,
ledger writes, deletion/clear, and trust review, per-record action-affordance
rows for Details, Copy Log, Retry checkpoint, and Recovery/rollback review, and
timeline data paths only; it does not write or delete records, retry commands,
approve actions, restore data, restart services, run shell commands, or use
network access. The recovery-plan endpoint returns retry,
failure-review, rollback-hint, snapshot, backup, repair, local-data, trust-gate,
alert, and dashboard/text/palette/voice/workflow request-route rows only; it
does not retry commands, restore data, repair services, delete files, export
data, run shell commands, or use network access. The code-test endpoint returns
workspace inventory, candidate commands, dashboard/text/palette/voice/workflow
request-route proof, and handoffs for workspace selection, status/diff review,
snapshot checkpoints, exact command approval, runner isolation, activity output,
and recovery/rollback only; snapshots, test runs, diffs, restores, commits, and
shell execution stay in Code Workspace controls.
The training-plan endpoint returns dataset, artifact, backend training route,
dashboard/text/palette/voice/workflow request-route, dependency, job,
handoff rows for dataset review, approval checkpoint, job monitoring, artifact
sampling, model-routing review, activity evidence, and data-location evidence
only; dataset creation, tiny-model training, LoRA jobs, model pulls, endpoint
changes, artifact writes, network access, and job approval stay in Training Lab
controls.
The voice-plan endpoint returns provider, permission, route, API gate,
dashboard/text/palette/voice/workflow request-route proof, voice alert rows for
microphone/user-activation gates, disabled providers, endpoint privacy,
local-service routes, setup gaps, transcript handoff rows for permission,
transcript routing, trust gates, activity metadata, speech-output review, and
endpoint privacy, and data path evidence only; it does not start the microphone,
record audio, upload audio, transcribe audio, synthesize speech, speak audio,
change STT/TTS settings, run shell commands, or use network access.
The automation-plan endpoint returns command catalog, workflow route, Agent Loop,
scheduled task boundary, build-watch handoff, activity ledger, detail/copy-log/
retry/recovery handoff metadata, alert, API-gate, and
dashboard/text/palette/voice/workflow request-route rows only; it highlights
missing automation catalogs, unresolved workflow routes, pending/running/queued
records, failed or blocked activity, build-watch gaps, and local-first
webhook/network boundaries without starting automation, starting loops, running
tasks, routing live commands, executing commands, approving actions, writing
activity, running shell commands, writing files, calling webhooks, or using
network access.
The autonomy-plan endpoint returns trust policy, command catalog, workflow
route, approval gate, decision-mode rows for suggest/ask/execute/auto-execute,
permission checkpoint rows for route preview, ask evidence review, execution
ledger review, local-only auto scope, and workflow handoff, activity decision,
retry evidence, handoff rows for route preview, approval review, workflow
starts, activity ledger, retry/recovery, trust policy, network/offline policy,
and safety boundaries, automation alert rows, dashboard/text/palette/voice/workflow
request-route proof, and data-path rows only; it highlights missing catalogs,
unresolved workflow routes, pending approvals, failed commands, unsafe trust modes, and retryable records without
routing commands, approving commands, retrying commands, starting workflows,
changing trust policy, deleting activity, running shell commands, modifying
files, or using network access.
The approval-plan endpoint returns trust policy rows, ask-gated command rows,
workflow approval gates, pending approval activity rows, decision rows, decision
checkpoint rows, failure rows, dashboard/text/palette/voice/workflow
request-route proof, approval handoffs for evidence review, allow/cancel,
retry/recovery, trust policy changes, workflow gates, network risk, and activity
ledger writes, API gates, and approval alert rows only; it highlights missing
command/policy evidence, non-ask network and high-risk tiers, auto-routed risky
commands, pending approvals, failed records, recovery-before-retry checkpoints,
and workflow gate issues without routing commands, executing commands,
approving, cancelling, or retrying commands, changing trust policy, writing
activity, starting workflows, running shell commands, writing files, or using
network access.
The loops-plan endpoint returns Agent Loop template, workflow-route,
approval-gate, activity, dashboard/text/palette/voice/workflow request-route
proof, API-gate, and data-path rows only; it highlights missing loop catalogs,
unresolved loop routes, missing approval gates, pending/failed loop activity,
and unsafe trust modes without starting loops, routing commands, executing
commands, approving actions, starting jobs, changing trust policy, running shell
commands, writing files, or using network access.
The memory-plan endpoint returns backend memory bucket coverage for identity,
preferences, projects, decisions, model choices, recurring tasks, and workflows,
plus recent-memory rows, dedicated model-choice handoff rows, recall toggle, profile alert rows,
dashboard/text/palette/voice/workflow request-route proof, API gate,
write-boundary, and data-path rows only; it
highlights empty memory stores, missing operator-profile areas, disabled
recall/extraction or skill settings, missing model preference, and
model-assisted write gates without adding memories, importing files, extracting
memories, tidying or auditing memories with a model, pinning memories, updating
memories, deleting memories, editing notes, running automation, running shell
commands, or using network access.
The workday, tasks, notes, and calendar plan endpoints return task, task-run,
calendar, note, briefing, API-gate, data-path, and
dashboard/text/palette/voice/workflow request-route rows only; they do not
create tasks, update tasks, run tasks, create calendar events, sync calendars,
edit notes, send notifications, start automation, run shell commands, or use
network access.
The research-plan endpoint returns Deep Research feature, provider, model,
active-job, saved-report, alert, API-gate, and
dashboard/text/palette/voice/workflow request-route proof only; it does not
start research, run web search, fetch URLs, write reports, call SearXNG, query
external providers, approve network access, or use network access.
The briefing endpoint returns a deterministic local operating snapshot for
"summarize today" with overview rows, suggested next-action rows, source rows,
dashboard/text/palette/voice/workflow request-route proof, briefing alert rows
for source gaps, work review, failed activity, service/model issues, empty
memory/notes, workflow handoff gates, guardrails, and API evidence from local tasks, task runs, calendar events,
notes, memory, model/training status, services, workflows, and operator
activity only; it does not write activity, start work, run commands, repair
services, train models, query networks, or modify local data.
The model snapshot endpoint returns primary-model, endpoint, dataset, artifact,
fine-tune dependency/job, readiness, and model snapshot alert rows for missing
primary models, missing local endpoints, external endpoint posture, empty
training stores, missing LoRA dependencies/base models, and failed fine-tune
jobs only; it does not set routes, pull models, start serving, start training,
change settings, run shell commands, or use network access.
The model-ops-plan endpoint returns primary-model, endpoint, local model,
training, fine-tune, Ollama, model alert rows for missing local readiness,
external endpoint posture, active/failed model jobs, approval gates, API-gate,
dashboard/text/palette/voice/workflow request-route proof, model operation
handoff rows for primary review, endpoint routing, serving/download approval,
training/fine-tune review, context retrieval, and offline/network policy, and
data-path rows only; it does not set or auto-select the primary model, register
or delete endpoints, pull or download models, start serving, benchmark models,
start training or fine-tuning, change settings, run shell commands, or use
network access.
The ai-runtime-plan endpoint returns one local AI runtime readiness view across
the model snapshot, model operation gates, Training Lab, runtime resource plan,
Ollama, ChromaDB, SearXNG, sealed model/vector/search volumes, API gates, and
dashboard/text/palette/voice/workflow request-route proof, plus handoff rows for
model routing, Training Lab, Ollama, ChromaDB, SearXNG policy, and runtime
resource guards only; it does not set primary models, download models, start
serving, start training, restart services, write files, run shell commands, pull
images, call SearXNG, or use network access.
The data-plan endpoint returns Local Data Map scope rows, sensitive local-store
posture, backup-required scope counts, local data alert rows,
dashboard/text/palette/voice/workflow request-route proof, handoff rows for
file operations, credential posture, memory/profile review, backup coverage,
offline/network policy, and activity evidence, API gates, and path-only evidence
for app data, logs, memory, documents, gallery, research, code workspaces,
training, models, backups, caches, vault, app key, and SSH material; it does not
read file contents, return secret values, write files, delete files, export
data, restore data, run shell commands, or use network access.
The file-ops-plan endpoint returns app-owned file roots, shallow file metadata,
sensitive-path flags, file alert rows for missing roots/sensitive roots/operation
gates, API gates, backup requirements, dashboard/text/palette/voice/workflow
request-route proof, file handoff rows for read scope, write/import,
delete/restore, backup/snapshot, indexing/library, and activity/recovery, and
data-path rows only; it does not read file contents, write files, copy files,
move files, delete files, upload files, import files, index files, export files,
restore files, run shell commands, or use network access.
The runtime-plan endpoint returns Docker/native mode, offline posture, runtime
limits, memory/process counters, disk-capacity rows for app/cache/model/job
roots, sealed named-volume/support-service rows, runtime alert rows,
dashboard/text/palette/voice/workflow request-route proof, heavy-job gates,
API gates, and data-path rows only; it highlights
missing roots, low storage, memory/process pressure, native-mode boundaries,
network-enabled posture, and heavy-job approval gates without running shell
commands, reading file contents, writing files, deleting files, starting jobs,
downloading models, pulling images, restarting services, or using network
access.
The services-plan endpoint returns local service probe rows, expected
Docker/Compose service rows, captured container status rows, host command
evidence, service alert rows, dashboard/text/palette/voice/workflow
request-route proof, API gates, and approval-boundary rows only; it highlights
required service issues, optional support-service gaps, missing Compose maps,
missing or unhealthy container status evidence, Docker socket exposure, and host
command gates without restarting services, starting services, pulling images,
running Docker, running shell commands, writing files, sending notifications, or
using network access.
The docker-runtime-plan endpoint returns one Docker operations view across
runtime/offline mode, service posture, captured container status, host command
gates, repair approval packets, sealed volumes, AI support services, and the
backend image deployment boundary for approved rebuild/recreate work, plus
handoff rows for host status capture, repair/restart, support-service start,
image rebuild/recreate, image-pull egress, volume deletion, and activity/rollback
evidence; it does not restart services, start services, repair containers, build
images, recreate services, pull images, run Docker, run shell commands, write
files, delete volumes, or use network access.
The lower-level service snapshots from `/api/operator/services` and
`/api/operator/checks` also return read-only alert rows for local probe issues,
offline/check warnings, missing captured container evidence, unhealthy captured
container rows, Docker socket exposure, and host command approval gates; they do
not restart services, run Docker, run shell commands, pull images, or modify
files.
Captured container status is read from
`DATA_DIR/operator_container_status.json` or `CLEVERLY_CONTAINER_STATUS_FILE`
when an approved host-side check writes bounded metadata such as container name,
image, status, source, and capture time; Cleverly never runs `docker ps` from
that endpoint.
The credentials-plan endpoint returns credential metadata, masked setting
posture, feature flags, sensitive path presence,
dashboard/text/palette/voice/workflow request-route proof, handoff rows for
masked settings, vault unlock review, network credential egress, feature gates,
backup/key protection, and activity audit evidence, API gates, and credential
alert rows only; it highlights configured secret-like settings, network-capable
credentials, vault config, local encryption keys, missing auth/settings paths,
and unsafe high-risk trust policy without reading secret values, returning
secret values, writing credentials, changing settings, unlocking vaults, sending
email, calling networks, or running shell commands.
The console-plan endpoint returns Command Center section coverage,
dashboard/text/palette/voice/workflow request-route proof, data-feed, API-gate,
guard-rail, data-path rows, alert feed rows for the dashboard/service/check/model
briefing/voice/activity/recovery/automation/data safety queues, and console
alert rows for missing dashboard routes, entry points, command catalogs,
workflow catalogs, and policy evidence only; it
does not route commands, execute commands, approve actions, start workflows,
start jobs, run shell commands, write files, restart services, train models,
export data, delete records, or use network access.
The toolchain-plan endpoint returns module wiring,
dashboard/text/palette/voice/workflow request-route proof, API-feed, data-path,
local/support-service, network-capability, guard-rail rows, handoff rows for
command routing, AI runtime, knowledge/RAG, research/network, code/build,
training artifacts, work automation, memory/profile, Docker services,
backup/recovery, and tool-access safety, and toolchain alert rows for missing
module routes, entry points, command catalogs, workflow catalogs, and policy
evidence only; it does not route commands, execute
commands, approve actions, start workflows, start jobs, run shell commands,
write files, restart services, train models, download models, query web search,
export data, delete records, or use network access.
The tool-access-plan endpoint returns built-in tool registry rows,
disabled-tool toggles, owner-scoped skill metadata, MCP server metadata,
approval-gate rows, dashboard/text/palette/voice/workflow request-route rows,
API-gate rows, and tool access alert rows for disabled core operator tools,
weak shell/filesystem/network gates, missing skill visibility, and MCP
inventory gaps only; it does not execute tools, run shell commands, write
files, change settings, add/connect/delete MCP servers, publish skills, use
network access, or read secret values.
The safety-plan endpoint returns destructive, network, credential, filesystem,
and shell boundary rows with trust-policy, command-gate, API-gate, data-path,
activity-ledger evidence, dashboard/text/palette/voice/workflow request-route
proof, safety handoffs for destructive/recovery approval, network egress,
credential review, filesystem boundaries, shell/Docker/tests, backup/recovery,
and activity logging, and safety alert rows for missing/weak ask gates, missing
command catalogs, missing workflow catalogs, and missing policy evidence only;
it does not route commands, execute commands, approve actions, start workflows,
start jobs, run shell commands, write files, restart services, train models,
query web search, read credentials, export data, delete records, or use network
access.
The goal-plan endpoint returns operating-console identity, principle,
definition-of-done, explicit v1 release-gate rows for Docker startup,
Command Center UI, target command routes, permission gates, activity timeline,
operator route smokes, focused tests, responsive UI inspection, and clean
commit/push proof, evidence, dashboard/text/palette/voice/workflow request-route
proof, goal handoff rows for console readiness, command routing,
autonomy/approvals, memory/profile, practical control, activity/recovery,
safety, Docker runtime, target experience, and completion audit, goal alert
rows, API-gate, guard-rail, and data-path rows only; it does not route
commands, execute commands, approve actions, start
workflows, start jobs, run shell commands, write files, restart services, train
models, query web search, read credentials, export data, delete records, or use
network access.
The experience-plan endpoint returns target phrase, command route, approval
gate, route-match evidence with selected route/expected route/score/trust mode,
dashboard/text/palette/voice/workflow entry-path, target handoff rows for
briefing, container repair, code tests, training, build watch, note-to-task,
local document search, change briefs, and backup verification, API-gate,
data-path rows, and target alert rows for missing phrase routes, command catalogs,
workflow catalogs, and policy evidence only; it does not route commands,
execute commands, start workflows, start jobs, run shell commands, write files,
restart services, approve actions, or use network access.
The notes-plan endpoint returns note metadata counts, checklist counts, reminder
posture, note-to-task candidate rows, dashboard/text/palette/voice/workflow
request-route proof, API gates, notes alert rows, and handoff rows for
create/update, archive/delete, checklist edits, reminders, note-to-task drafts,
search/export boundaries, and activity/recovery only; it does not create notes,
update notes, archive notes, delete notes, toggle checklist items, fire
reminders, create tasks, run shell commands, or use network access.
The calendar-plan endpoint returns local calendar event counts, today/upcoming
event posture, reminder posture, sync configuration, API gates, and calendar
alert rows, plus handoff rows for event create/update, event/calendar deletion,
ICS import/export, remote sync/connection tests, reminder dispatch, recurring
rule edits, and activity/recovery only; it does not create calendar events,
update events, delete events, import calendars, export calendars, sync calendars,
test remote connections, send notifications, run shell commands, or use network
access.
The tasks-plan endpoint returns scheduled task counts, trigger posture, recent
task-run status, notification/webhook posture, shell-capable action gates, API
gates, task alert rows, and handoff rows for create/update, lifecycle/delete,
run/stop/retry, webhook triggers, notification/email output, shell/admin
actions, and activity/recovery only; it does not create tasks, update tasks,
pause or resume tasks, delete tasks, revert built-ins, run tasks, stop tasks,
clear caches, regenerate webhook tokens, trigger webhooks, send notifications,
run shell commands, or use network access.
The work-ops-plan endpoint returns one read-only work operations view across
briefing, workday, tasks, notes, reminders, note-to-task drafts, calendar
windows, dashboard/text/palette/voice/workflow route proof, alert rows, explicit
work handoff rows, API gates, sync gates, guard rails, and local data paths only; it does not create tasks, update
tasks, run tasks, create or update notes, fire reminders, create or update
calendar events, sync calendars, start automation, write activity, send
notifications, run shell commands, or use network access.
The build-watch endpoint returns workspace inventory, candidate build commands,
loop limits, dashboard/text/palette/voice/workflow request-route proof, route
IDs, API gates, and evidence rows only; loop starts, build runs, file edits,
snapshot create/restore, dependency installs, network fetches, commits, and
shell execution stay behind Code Workspace and Agent Loop approval controls.
The document-search endpoint returns personal document index counts, RAG
readiness, route proof, local search alert rows, API gates, and data paths only;
it highlights empty indexes, missing keyword chunks, unavailable vector search,
excluded-file review, and index-write gates without running a query, reading
result snippets, reloading indexes, adding directories, rebuilding RAG, using
web search, or modifying files.
The research-plan endpoint returns Deep Research feature posture, search-provider
configuration, SearXNG path evidence, saved report metadata, active-job rows
when provided, API gates, research alert rows, and research/library handoff rows
for local documents, saved reports, gallery evidence, workspace follow-up, and
approved web-source starts only; it does not start research, run web search,
fetch URLs, write reports, call SearXNG, query external providers, approve
network access, or use network access.
The gallery-plan endpoint returns generated-image, gallery, gallery-upload, chat
upload, image-model, vision-cache, API-gate, media alert rows,
dashboard/text/palette/voice/workflow request-route proof, and media handoffs
for upload/import, AI media generation, edit/transform, delete/archive,
export/download, vision refresh, and network-provider review only; it does not
upload files, generate images, edit media, replace images, delete media, export
media, refresh vision captions, call model endpoints, or use network access.
The workspace-plan endpoint returns one local workbench view across code
workspaces, build-watch readiness, local document search, research, gallery,
file operations, data paths, and handoffs for code tests, build watching,
document search, research escalation, media operations, file/data boundaries,
backup/recovery, and activity/retry logging; it does not run tests, start build
watches, search documents, start research, upload files, generate images, write
files, delete files, run shell commands, or use network access.

Local document search is also routed through the local Library/RAG index. When
you ask Cleverly to search local documents from chat, voice, or Command Center,
it searches indexed personal documents first, falls back to the local keyword
index, and reports a no-match result instead of claiming it has no local access.
The Command Center Local Document Search modal uses
`/api/operator/document-search-plan` before a query so the local index,
dashboard/text/palette/voice/workflow request-route proof,
`/api/personal/search` route, RAG/keyword fallback, query requirement, handoff
rows for query review, vector/keyword routing, index refresh, directory scope,
file exclusions, research escalation, and activity logging, and safety
boundaries are visible before retrieval starts. Completed local document searches are mirrored
to `data/operator_activity.json` with query metadata, result count, route type,
and result titles/sources only; result snippets are not stored in the activity
ledger.

Voice I/O includes an approval-gated browser voice setup route that enables
browser STT/TTS locally; microphone access still requires the browser's own
permission prompt when voice starts. The Voice Operations Preflight shows
dashboard/text/palette/voice/workflow request-route proof and transcript
handoffs for permission, route preview, trust policy, activity metadata,
speech-output review, and endpoint privacy before listening or speaking, with
each row marked plan-only for microphone, speech, and network use. Voice command
start/listen/no-speech/route and error states are mirrored to
`data/operator_activity.json` with provider and status metadata only; audio is
not stored. Voice configuration is stored in `data/settings.json`, generated
local TTS cache files live under `data/tts_cache/`, and browser speech
recognition/synthesis stays in the browser unless local or endpoint providers
are explicitly selected.

Work Operations Preflight includes a read-only local alert queue from the
backend workday plan. It highlights overdue tasks, failed or active task runs,
calendar items due today, note-to-task candidates, and source warnings, while
leaving task edits, calendar changes, task runs, notifications, and sync behind
their existing review and approval gates.

In Docker offline/sealed mode, internet-dependent actions such as web research,
external model endpoints, Cookbook downloads, email/calendar sync, webhooks, and
cloud APIs are hidden or blocked unless you intentionally enable break-glass
network access.

## Start Here

Default behavior:

- `.\Cleverly.ps1 start` is offline-only. It uses sealed Docker volumes, binds
  the UI to `127.0.0.1:7000`, and never pulls images or models.
- `.\Cleverly.ps1 setup -AllowConnectedPrep` is the easiest first-run command
  for a connected, non-sensitive prep machine. It builds/pulls what is needed,
  auto-picks a model from detected GPU memory, seals data into Docker volumes,
  and starts Cleverly.
- There is no hidden cloud model default. If no model is explicitly set during
  connected setup, Cleverly chooses a local Ollama model from the launcher's
  hardware profiles. See [docs/model-onboarding.md](docs/model-onboarding.md)
  for the current model choices.

### Runtime Modes

- **Docker sealed mode** is the recommended sensitive-machine mode. It uses
  Docker network isolation, hardened containers, sealed Docker volumes, and
  offline startup checks.
- **Standalone mode** runs without Docker. It is easier to start, binds to
  `127.0.0.1`, sets `CLEVERLY_OFFLINE=1`, and uses app-level offline policy, but
  it does not provide Docker network isolation or sealed-volume protection.

### Easiest Windows Setup

For the simplest startup, double-click:

```text
Cleverly-App.cmd
```

Then use **Check Setup**. If Docker images or the primary model are missing,
use **Connected Prep** on a connected, non-sensitive prep machine or **Build
Bundle** for transfer to an offline machine. After prep, use **Start Offline**
and **Verify Offline** before sensitive work.

Command-line equivalent on a connected machine that is allowed to download
Docker images and the selected local model:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep
```

For a 24GB GPU target, force the hardware tier:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep -GpuGB 24
```

For a specific model tag, force the model:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep -Model qwen3-coder:30b
```

Open:

```text
http://127.0.0.1:7000
```

After setup, normal starts stay offline:

```powershell
.\Cleverly.ps1 start
```

If you built the optional fine-tune image, use:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep -FineTune
.\Cleverly.ps1 start -FineTune
```

Common commands:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep
.\Cleverly.ps1 start
.\Cleverly.ps1 start -FineTune
.\Cleverly.ps1 seal-data -FineTune
.\Cleverly.ps1 stop
.\Cleverly.ps1 status
.\Cleverly.ps1 doctor -FineTune
.\Cleverly.ps1 logs
```

Double-clicking `Cleverly.cmd` also starts the offline app and opens the
browser. The Windows launcher does not pull, build, or download during normal
start.

For the guided desktop-style control window, double-click:

```text
Cleverly-App.cmd
```

If images or models are missing, run prep on a connected, non-sensitive machine.
By default, connected prep detects the host GPU memory and chooses a matching
Ollama model profile. CPU-only machines start with the smallest safe local
model; a 24GB GPU selects the code-focused `qwen3-coder:30b` profile.

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep
```

Use `-GpuGB` to force a hardware tier, or `-Model` to override the auto pick:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -GpuGB 24
.\Cleverly.ps1 prep -AllowConnectedPrep -Model gpt-oss:20b
```

Then move the prepared images/data to the offline machine and start again.

To make that transfer easier, build a portable offline bundle:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep -FineTune
```

It writes `dist\cleverly-offline-bundle`. Copy that folder to the offline
machine, then run `load-cleverly.cmd`, `seal-data.cmd`, and
`start-cleverly.cmd` to launch. The selected primary model is recorded in the
bundle and used by the offline runtime.

Use `-HostData` only when you intentionally want Docker to write runtime state
to visible `./data` and `./logs` folders:

```powershell
.\Cleverly.ps1 start -FineTune -HostData
```

### Standalone Windows App

Use this when Docker Desktop is not available and you accept the weaker
standalone boundary:

```powershell
.\Cleverly-Standalone.ps1 setup -AllowConnectedPrep
.\Cleverly-Standalone.ps1 start
```

After setup, double-clicking `Cleverly-Standalone.cmd` starts the no-Docker app
with local-only, app-enforced offline defaults. Run:

```powershell
.\Cleverly-Standalone.ps1 doctor
```

For the exact safety boundary, read
[docs/standalone-mode.md](docs/standalone-mode.md).

### First Login

On first boot, Cleverly creates an admin account named `admin` unless
`CLEVERLY_ADMIN_USER` is set. The temporary password is printed in the terminal.
For Docker, get it with:

```bash
docker compose logs cleverly
```

Log in, then change the password in **Settings**.

After login, open **Setup** on the welcome screen. The setup wizard walks
through offline status, local model registration, and the no-internet proof
check. You can also open it directly at:

```text
http://127.0.0.1:7000/setup
```

### Docker Quick Start

Use this on a connected, non-sensitive Windows prep/development machine. The
launcher builds the app image, prepares required support images, chooses a
local model, seals data into Docker volumes, and starts the offline runtime:

```powershell
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
.\Cleverly.ps1 setup -AllowConnectedPrep
```

Open `http://127.0.0.1:7000`.

Manual Compose is also supported for development. The default Compose startup
builds and starts the core app, worker, and local proxy without requiring
optional support images:

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
cp .env.example .env
docker compose up -d --build
```

ChromaDB, SearXNG, and ntfy are optional support services in the `support`
profile. The hardened Compose file uses `pull_policy: never` for those services
so runtime startup cannot pull from the internet. On a fresh connected machine,
pull those images first, then enable the profile:

```bash
docker pull ghcr.io/chroma-core/chroma:latest
docker pull ghcr.io/searxng/searxng:latest
docker pull docker.io/binwiederhier/ntfy:latest
docker compose --profile support up -d --build
```

Docker uses the Compose stack name `cleverly` and Docker named volumes for app
runtime state by default. The main containers default to:

```text
cleverly
cleverly-proxy
cleverly-ollama
```

### Offline Docker Start

Use this after images and model data have already been built or loaded:

For manual Compose starts, set `OLLAMA_MODEL=<prepared tag>` in `.env` first.
The `Cleverly.ps1` launcher sets this automatically from `-Model` or from the
saved primary-model manifest created during prep.

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  up -d --no-build --pull never
```

With the optional fine-tune image:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  -f docker/finetune.yml \
  up -d --no-build --pull never
```

The app container runs on an internal-only Docker network. Only the local proxy
binds to `127.0.0.1:7000`, so your browser can use the app while the app
container has no internet egress.

For the full air-gap checklist, use
[docs/offline-release.md](docs/offline-release.md).
For operator green/red checks on the target machine, use
[docs/airgap-operator-checklist.md](docs/airgap-operator-checklist.md).

To check a local install without downloading anything:

```powershell
.\Cleverly.ps1 doctor -FineTune
```

For development verification from an already-prepared checkout:

```powershell
.\scripts\dev-verify.ps1
```

On a connected development machine that still needs Python dependencies:

```powershell
.\scripts\dev-verify.ps1 -Install
```

### Choose And Pull A Local Model

Run this only on a connected prep machine. The launcher can auto-pick from
detected GPU memory:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep
```

Use `-GpuGB <number>` to force a hardware tier, or pass `-Model <tag>`
explicitly when you already know the exact Ollama model to carry offline.
In other words, pass `-Model` only when you want to override the hardware pick.

```bash
docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
OLLAMA_MODEL=qwen3-coder:30b docker compose -f docker-compose.yml -f docker/ollama.yml up -d --build
```

This stores Ollama models under `./data/ollama` for transfer. Run
`.\Cleverly.ps1 seal-data` on the offline machine after loading images to copy
that model store into the sealed Docker volume.

For model choices and exact prep commands, use
[docs/model-onboarding.md](docs/model-onboarding.md). The first-run Setup
wizard uses the same recommendations.

### Sensitive Machine Checklist

Before loading sensitive files, memories, email, calendars, private repos, or
client data:

- Prepare images and models only on a connected, non-sensitive machine.
- Move the offline bundle to the target machine by trusted removable media.
- Run `load-cleverly.cmd` from the bundle.
- Run `seal-data.cmd` if prepared data/model files were included.
- Start with `.\Cleverly.ps1 start` or `.\Cleverly.ps1 start -FineTune`.
- Open **Setup** or **Offline** and confirm zero failed offline-policy checks.
- Run **Test No Internet** in Offline Control.
- Run `.\ci\smoke-operator-routes.ps1` against the running `cleverly`
  container and keep `dist\operator-route-smoke.json`; it verifies the
  read-only operator routes and the nine v1 target command phrases.
- Run `.\ci\fresh-machine-offline-smoke.ps1` and keep the JSON report.
- Run `.\ci\fresh-machine-proof.ps1` on the offline target and keep the JSON
  report plus `.sha256` file.
- Confirm the UI is only at `http://127.0.0.1:7000`.
- Do not pass `-HostData` unless visible host folders are intentional.
- Do not set `CLEVERLY_ALLOW_NETWORK` unless accepting the break-glass risk.
- Keep the Docker data root protected by full-disk encryption when possible.

See [docs/release-checklist.md](docs/release-checklist.md),
[docs/fresh-machine-offline-test.md](docs/fresh-machine-offline-test.md), and
[docs/security-review.md](docs/security-review.md). For the exact security
boundary, read [docs/threat-model.md](docs/threat-model.md).

### Offline Release Build

On a connected, non-sensitive release workstation, the wrapper below runs the
local checks, writes an SBOM, runs the no-network container smoke, builds the
offline bundle, and packages installer artifacts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-offline-release.ps1 -Model qwen3-coder:30b -RequireSignature
```

For a named release-candidate folder with a target-machine proof note:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\make-release.ps1 -Version 1.0.0-rc1 -Model qwen3-coder:30b -RequireSignature -Zip
```

The release wrapper writes `release-manifest.json`, `checksums.sha256`,
`cleverly-sbom.json` as a CycloneDX JSON SBOM, `static-security.json`, `model-integrity.json`,
`release-dashboard.html`, `release-dashboard.json`, no-network smoke evidence,
and operator-route smoke evidence into the release folder.

For dependency-only review, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\generate-sbom.ps1
```

For local static-security checks that do not contact advisory services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-static-security.ps1
```

For model release evidence:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\write-model-integrity.ps1 -Model qwen3-coder:30b -ExpectedGpuGB 24
```

To create an annotated release-candidate tag:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create-release-tag.ps1 -Version v0.1.0-rc1 -Push
```

### Pipeline And Branch Protection

GitHub Actions includes:

- **Cleverly CI**: Python tests, JavaScript syntax checks, PowerShell parser
  checks, Docker Compose validation, static security scan, and no-network
  container smoke.
- **Security Analysis**: CodeQL plus Dependency Review on pull requests.
- **Release Artifacts**: tag/manual release evidence build, release dashboard,
  SBOM/model/security reports, artifact upload, and GitHub artifact
  attestations.

The optional Bombadil UI exploration spec does not store login credentials in
source. Set `CLEVERLY_BOMBADIL_USERNAME` and `CLEVERLY_BOMBADIL_PASSWORD` when
running `tests/bombadil-spec.ts` against a configured local account.

After pushing the workflows, configure branch protection from an authenticated
GitHub admin shell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure-branch-protection.ps1 -RequirePullRequest
```

Use pull requests for normal work once branch protection is enabled.

### Windows Installer

Cleverly includes a per-user Windows installer project. Local test builds can
be unsigned, but release installers should be Authenticode-signed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\new-self-signed-code-signing-cert.ps1

powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 `
  -CertificatePath .\dist\signing\cleverly-local-test-codesign.pfx `
  -CertificatePasswordPath .\dist\signing\cleverly-local-test-codesign.password.txt `
  -RequireSignature
```

The self-signed certificate path is for local signing workflow validation only.
Use a real trusted code-signing certificate for public distribution. Details:
[docs/windows-installer.md](docs/windows-installer.md).

### Native Linux / macOS

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -c requirements.lock
python setup.py
CLEVERLY_OFFLINE=1 python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Use `--host 0.0.0.0` only when you intentionally want LAN or reverse-proxy access.

Requirements: Python 3.11+. Cookbook also needs `tmux` for background model
downloads and serves.

### Native Windows

```powershell
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
powershell -ExecutionPolicy Bypass -File .\Cleverly-Standalone.ps1 setup -AllowConnectedPrep
powershell -ExecutionPolicy Bypass -File .\Cleverly-Standalone.ps1 start
```

Manual setup:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt -c requirements.lock
python setup.py
$env:CLEVERLY_OFFLINE='1'
$env:APP_BIND='127.0.0.1'
$env:AUTH_ENABLED='true'
$env:LOCALHOST_BYPASS='false'
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

For full Cookbook background downloads and the agent shell tool on Windows,
install [Git for Windows](https://git-scm.com/download/win) so `bash.exe` is
available.

### Apple Silicon

Docker on macOS cannot use the Metal GPU. For GPU-accelerated Cookbook on an
M-series Mac, run Cleverly natively:

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
./start-macos.sh
```

It launches at `http://127.0.0.1:7860`. To build a clickable app wrapper:

```bash
./build-macos-app.sh
```

### Training Lab

The built-in [Training Lab](docs/local-training-lab.md) runs offline. It uses
pasted local text and writes datasets/artifacts under `./data/training`; it does
not download datasets or call model endpoints. Advanced LoRA fine-tuning works
only when its optional dependencies and a trainable local model directory are
already baked into the image.

The Command Center Training Run Plan uses `/api/operator/training-plan` to
inspect local datasets, starter artifacts, fine-tune dependency state, job
ledgers, backend training routes, dashboard/text/palette/voice/workflow request
route proof, alert rows, and data paths before any run. Its alert
queue highlights missing datasets, failed or active jobs, dependency/base-weight
blockers, primary-model readiness, and the explicit run-approval gate. It is
evidence-only: it does not create datasets, start tiny training, start LoRA
jobs, pull models, change model endpoints, write artifacts, use network access,
or approve jobs.

External AI/security references are tracked as
[study packs](docs/external-agent-study-packs.md) only. Cleverly does not pull
or execute those repositories during offline runtime.

### Code Workspace

Use **Code** in the sidebar to work on a complete repo inside Cleverly. Import a
`.zip`, `.tar`, `.tar.gz`, or `.tgz` archive, then browse files, edit files,
apply unified diffs, run local test/build commands, inspect git status/diff, and
commit changes.

Use **Loops** in the sidebar when you want a repeatable local workflow prompt
for testing, build repair, security review, offline leak checks, docs sync,
model onboarding, or release smoke testing. Loops are bundled templates only:
they copy or insert prompts and do not install hooks or contact external
services.

Code workspaces live under the sealed Docker data volume by default. Network
fetch/install commands such as `curl`, `wget`, `git pull`, `pip install`, and
`npm install` are blocked in workspace command runs. Archive imports reject path
traversal, symlinks, `.git` internals, and oversized expansion.

The Code panel also includes a coding-agent workflow. Give it a task, choose a
local test command such as `pytest -q`, and it will snapshot the repo, read a
bounded set of files, and ask the configured model for a unified diff. The diff
is shown for review first; use **Apply**, **Reject**, **Snapshot**, **Run
Tests**, and **Restore** in the Code panel before committing changes. You can
also manually create snapshots, restore the latest snapshot, or export the
patched repo archive.

Safety Level defaults to **Apply With Tests**. **Review Only** blocks Save,
Apply, and Commit for inspection-only sessions. **Commit Allowed** must be
selected before the Commit button can run.

Allowed Paths can further restrict Code Workspace writes. Enter comma-separated
repo-relative prefixes such as `src, tests, README.md`; Save, Apply, validation,
and agent drafts are blocked outside those prefixes.

The Code Workspace model key is intentionally blank by default. Set it in the
Code panel or with `manage_settings` before expecting an agent to use a specific
coding model, for example `GLM-5.2`. In offline mode, Code Workspace only uses
loopback or Docker-service model endpoints; cloud/API endpoints are refused.

In Docker, workspace test/build commands run through the `cleverly-code-worker`
sidecar by default. That worker has `network_mode: none` and communicates with
the app through the sealed Docker data volume. Native/development runs use the
in-process runner unless `CODE_WORKSPACE_RUNNER=worker` is set and the worker is
started manually.

The Command Center Code Test Plan uses `/api/operator/code-test-plan` to inspect
workspace metadata and common local test config files such as `package.json`,
`pyproject.toml`, `pytest.ini`, `go.mod`, and `Cargo.toml`. It suggests commands
as evidence only and returns route-proof rows for dashboard/text/palette/voice
and workflow entry points plus code alert rows for missing workspaces, runner
isolation review, manual command gaps, snapshot approval, and test-run approval.
Candidate command rows can stage a detected command in the
Code Workspace Run panel with the matching workspace selected, but they do not
press Run. A staged command is mirrored to `data/operator_activity.json` with
status `staged`, the workspace metadata, and a note that no tests executed. The
user still reviews scope, status, diff, and snapshot state before using the Code
Workspace Run button. When the Code Workspace run endpoint executes a command,
including the browser Run Command button and direct API/tool calls, the result
is mirrored to `data/operator_activity.json` with the command, workspace,
runner, exit code, and truncated stdout/stderr so Activity Details, Copy Log,
retry, and recovery views have execution evidence. The plan does not run tests, create
snapshots, apply diffs, restore snapshots, commit, install dependencies, use
network access, or execute shell commands. Blocked or invalid run attempts are
also recorded as `blocked` activity before the API returns the validation error.
Code Workspace agent runs also write operator activity with the task, model,
selected files, snapshot id, diff/test state, and blocked/failure status without
storing the full proposed diff in the activity ledger.
Diff validation runs write operator activity with snapshot id, patch/test exit
codes, truncated output, and pass/fail status before the temporary snapshot is
restored.

The Command Center Build Watch Plan uses `/api/operator/build-watch-plan` to
inspect workspace metadata, infer candidate build/check commands, and show the
Build Until Green loop approval gates. It returns build alert rows for missing
workspaces, manual build-command gaps, runner isolation review, recovery
snapshot approval, loop approval, and required activity evidence. It also
returns Build Watch handoff rows for workspace selection, status/diff review,
snapshot checkpoints, loop approval, runner execution, repair iterations,
recovery/rollback, and activity ledger writes. It does not start loops, run
builds, edit files, create or restore snapshots, install dependencies, use
network access, commit, or execute shell commands.

## Docker Notes

`Cleverly.ps1 start` starts the offline app, bundled Ollama, networkless code
worker, and local proxy with `--pull never`. Manual default Compose starts only
the core app, worker, and proxy. ChromaDB, SearXNG, and ntfy are available in
the optional `support` profile when those images are prepared. Those support
services and the bundled Ollama overlays use `pull_policy: never`, so a missing
image fails closed instead of pulling from the internet during runtime.
Cleverly and bundled services run on an internal-only Docker network by default.
Only the proxy publishes a host port, and it binds to `127.0.0.1`.

The Cleverly service runs as a non-root UID/GID, drops Linux capabilities, uses
`no-new-privileges`, mounts the application filesystem read-only, and uses tmpfs
for `/tmp`, `/run`, and `/var/tmp`. Runtime state is written to Docker named
volumes by default. The Docker entrypoint also refuses to start with
`CLEVERLY_OFFLINE` disabled unless `CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK`
is explicitly set. The app itself also runs an offline startup policy check and
will fail closed if offline mode, loopback binding, worker isolation, or
local-only model endpoint checks fail.

Code Workspace commands run in the `cleverly-code-worker` sidecar by default.
That container is read-only, drops Linux capabilities, uses `no-new-privileges`,
has process limits, mounts only the sealed data/cache volumes it needs, and uses
`network_mode: none`.

Sealed Docker volumes are not encryption. A host administrator, anyone with
Docker access, or anyone with access to Docker's data root can inspect them.
Optional stronger at-rest protection can come from full-disk encryption or an
encrypted Docker data root. On Windows, use
[docs/encrypted-docker-data-root.md](docs/encrypted-docker-data-root.md) to
check or enable BitLocker protection when you have Administrator rights. If the
target computer does not allow admin access, skip that optional hardening and
run the sealed offline container normally.

To use the old visible host-folder layout, add `-f docker/host-data.yml` to
manual Compose commands or pass `-HostData` to `Cleverly.ps1`. On Linux, make
sure those bind-mounted directories are writable by the configured `PUID`/`PGID`
before first boot:

```bash
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
chown -R "$(id -u):$(id -g)" data logs
```

In sealed mode, Cookbook downloads, local package installs, npm cache, logs,
and app data live in Docker named volumes so they survive container recreation.

To use Ollama from Docker, prefer the bundled Ollama overlay documented above.
It keeps inference traffic inside Docker's internal-only network.

Useful checks:

```bash
docker compose ps
docker compose logs --tail=120 cleverly
docker compose logs cleverly | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

To let the Local Services Map show host-captured container status without
granting the app Docker control, write metadata to
`data/operator_container_status.json` from an approved host shell:

```json
{
  "source": "approved host docker ps",
  "captured_at": "2026-06-22T12:00:00Z",
  "containers": [
    {"name": "cleverly", "image": "cleverly:local", "status": "Up 3 minutes (healthy)"}
  ]
}
```

## Security Notes

Cleverly is a local AI operating console with powerful local tools: shell access,
file uploads, model downloads, web research, email/calendar integrations, API
tokens, and webhooks. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Do not expose it directly to the public internet without HTTPS and a trusted reverse proxy.
- Keep `data/`, `.env`, logs, databases, and uploaded/generated media out of Git.
- Review `data/auth.json` after first boot: disable open signup unless you intentionally want it.
- Keep shell/Python/file read-write, MCP management, API tokens, webhooks, model serving, backup/vault, and app settings admin-only.
- Rotate any API keys or tokens that were ever pasted into shared chats, screenshots, demos, or logs.
- Prefer binding manual development runs to `127.0.0.1`; bind to `0.0.0.0` only when you intentionally want LAN/reverse-proxy access.
- Check the admin-only operator page at `http://127.0.0.1:7000/operator` before loading sensitive data.
- Review dependency changes with [docs/dependency-audit.md](docs/dependency-audit.md); `requirements.txt` remains the direct dependency input, while Docker and reproducible native installs use `requirements.lock` as constraints.

For HTTPS, put a TLS-terminating reverse proxy in front. Minimal Caddy example:

```caddy
cleverly.example.com {
  reverse_proxy localhost:7000
}
```

## Configuration

Most setup is done inside the app with `/setup` or **Settings**. Use `.env`
for deployment-level defaults and secrets you want present before first boot.

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server |
| `LLM_HOSTS` | unset | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | unset | Optional OpenAI key |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL |
| `SEARXNG_SECRET` | generated on first Docker boot | Optional SearXNG cookie/CSRF secret |
| `APP_BIND` | `127.0.0.1` | Docker Compose local proxy bind address |
| `APP_PORT` | `7000` | Docker Compose host port |
| `CLEVERLY_CONTAINER_NAME` | `cleverly` | Main Cleverly Docker container name |
| `CLEVERLY_PROXY_CONTAINER_NAME` | `cleverly-proxy` | Local proxy Docker container name |
| `CLEVERLY_OLLAMA_CONTAINER_NAME` | `cleverly-ollama` | Bundled Ollama Docker container name |
| `OLLAMA_MODEL` | unset | Required for manual bundled-Ollama Compose; the launcher sets it from `-Model` or the saved primary-model manifest |
| `OLLAMA_IMAGE` | `cleverly-ollama:local` | Bundled Ollama image used by offline startup |
| `CLEVERLY_AUTO_ADD_OLLAMA` | `1` in Ollama overlays | Auto-register the bundled local Ollama endpoint |
| `CLEVERLY_OLLAMA_ENDPOINT_NAME` | `Bundled Ollama` | Display name for the auto-registered Ollama endpoint |
| `PUID` / `PGID` | `1000` / `1000` | UID/GID used by the hardened Docker container |
| `CLEVERLY_TMPFS_SIZE` | `1g` | Size of the Cleverly `/tmp` tmpfs in Docker |
| `CLEVERLY_PIDS_LIMIT` | `4096` | Process limit for the Cleverly container |
| `CLEVERLY_OFFLINE` | `1` in Docker | Disable internet-facing features and startup network warmups |
| `CLEVERLY_ALLOW_NETWORK` | unset | Break-glass token; must equal `I_ACCEPT_NETWORK_RISK` to bypass Docker/app offline startup guards |
| `CLEVERLY_DISABLE_OFFLINE_POLICY` | unset | Development-only bypass for the app-level strict offline startup policy |
| `CLEVERLY_OFFLINE_EMBEDDINGS` | `0` in Docker | Allow local FastEmbed only after its cache is pre-seeded |
| `CLEVERLY_HASH_EMBEDDINGS` | `1` in Docker | Enable no-download local hash embeddings when FastEmbed is unavailable or disabled |
| `CLEVERLY_HASH_EMBEDDING_DIM` | `384` | Dimension for the no-download local hash embedding fallback |
| `CLEVERLY_HOST_DATA` | unset | Set to `1` only to make `Cleverly.ps1` use visible `./data` and `./logs` bind mounts |
| `CODE_WORKSPACE_DIR` | unset | Optional override for sealed code workspace storage; defaults to `DATA_DIR/code-workspaces` |
| `CODE_WORKSPACE_RUNNER` | `worker` in Docker | Use the networkless worker sidecar for Code Workspace commands; native runs default to in-process |
| `CODE_WORKSPACE_WORKER_DIR` | unset | Optional worker queue override; defaults to `DATA_DIR/code-workspaces/.worker` |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for direct loopback requests |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | `localhost` | ChromaDB host |
| `CHROMADB_PORT` | `8100` | ChromaDB port for manual host runs |
| `EMBEDDING_URL` | unset | OpenAI-compatible embeddings endpoint |

## Built-In MCP Servers

Cleverly auto-registers a few built-in MCP servers at startup. The npx-based
ones only start when their npm package is already in the local npx cache. To
enable the browser MCP server:

```bash
npx -y @playwright/mcp@latest --version
```

Restart Cleverly after the package is installed.

## Architecture

```text
app.py      FastAPI entry point
core/       auth, database, middleware, constants
src/        llm_core, agent loop, tools, chat processor, search
routes/     chat, session, document, memory, model, email, calendar endpoints
services/   docs, memory, search, hwfit
static/     frontend HTML, CSS, and JS modules
docs/       landing page and preview media
```

## Data

By default, the Docker launcher uses Docker-managed named volumes. With
`-HostData` or native runs, the same app data is written under the repository's
gitignored `data/` and `logs/` folders.

In the app, open **Command Center -> Operator -> Data** or run the
`Open Local Data Map` command to see the same locations grouped by sealed
Docker volumes, host/native mirrors, app files, backup/privacy boundaries, and a
metadata-only file alert queue for missing required roots, sensitive roots, and
write/delete gates.

Default sealed Docker volumes:

| Volume | Mounted path | Stores |
|---|---|---|
| `cleverly-data` | `/app/data` | SQLite DB, auth/settings/features JSON, sessions, memories, presets, skills, uploads, generated images, personal docs, research reports, tasks, training data, search caches, Code Workspace state, vault config, and other app runtime state |
| `cleverly-logs` | `/app/logs` | Application logs |
| `cleverly-ssh` | `/app/.ssh` | Cookbook remote-server SSH identity |
| `cleverly-cache` | `/app/.cache` | General runtime cache for browser/MCP helpers and package caches |
| `cleverly-huggingface` | `/app/.cache/huggingface` | Hugging Face model/cache data used inside Docker |
| `cleverly-local` | `/app/.local` | Cookbook-installed local Python CLIs/packages |
| `cleverly-npm-cache` | `/app/.npm` | npm/npx cache for optional MCP helpers |
| `cleverly-ollama` | `/root/.ollama` | Bundled Ollama model store when using sealed Ollama overlay |
| `cleverly-chromadb-data` | `/data` | ChromaDB vector store service data |
| `cleverly-searxng-data` | `/etc/searxng` | SearXNG runtime config, including generated secret |
| `cleverly-searxng-cache` | `/var/cache/searxng` | SearXNG persistent cache data |
| `cleverly-ntfy-cache` | `/var/cache/ntfy` | ntfy cache |

Host-data overlay and native paths:

| Host path | Container/native path | Stores |
|---|---|---|
| `./data` | `/app/data` or native `data/` | Main app runtime data |
| `./logs` | `/app/logs` or native `logs/` | Application logs |
| `./data/ssh` | `/app/.ssh` | Cookbook SSH identity |
| `./data/cache` | `/app/.cache` and `XDG_CACHE_HOME=/app/data/cache` | General cache and FastEmbed cache root |
| `./data/cache/fastembed` | `/app/data/cache/fastembed` | FastEmbed model cache when pre-seeded; the hash embedding fallback writes no model cache |
| `./data/huggingface` | `/app/.cache/huggingface` | Hugging Face cache/model files |
| `./data/local` | `/app/.local` | Local package installs used by Cookbook |
| `./data/npm-cache` | `/app/.npm` | npm/npx cache |
| `./data/ollama` | `/root/.ollama` | Bundled Ollama model store for connected prep/offline transfer |

Important files and subdirectories under `data/` include:

| Path | Stores |
|---|---|
| `data/app.db` | Main SQLite database for chat/session records, tasks, task runs, calendars, calendar events, notes, memories, documents, model endpoints, and related local app tables |
| `data/auth.json` | Users, password hashes, privileges, and auth settings |
| `data/settings.json` / `data/features.json` | App settings and feature flags |
| `data/user_prefs.json` | Per-user preferences, UI settings, and the Command Center operator profile |
| `data/sessions.json` | Session metadata cache used by the session manager |
| `data/operator_activity.json` | Durable Command Center/operator activity ledger for command status, trust tags, logs, retry evidence, recovery notes, and rollback hints |
| `data/operator_policy.json` | Owner-scoped Command Center trust policy for local, approval, network, and high-risk command tiers |
| `data/operator_commands.json` | Owner-scoped sanitized command catalog published by the browser command layer for backend readiness, route proof, and audit visibility |
| `data/operator_workflows.json` | Owner-scoped sanitized Agent Loop and workflow route catalog published by Command Center for automation readiness, backend target-phrase proof, and handoff evidence |
| `data/memory.json`, `data/memory_doc.md`, `data/skills`, `data/skills.json` | Memory and skill data |
| `data/uploads`, `data/generated_images`, `data/gallery`, `data/gallery_uploads` | Uploaded and generated media |
| `data/personal_docs`, `data/personal_docs/index`, `data/chroma` | Personal documents and local vector indexes for native/local modes |
| `data/deep_research` | Deep Research job outputs and reports |
| `data/code-workspaces` / `data/code-workspaces/workspaces.json` | Code Workspace imports, metadata index, snapshots, worker queue, outputs, and Change Brief workspace evidence |
| `data/training` | Training Lab root for local datasets, starter artifacts, fine-tune jobs, adapters, and base-model directories |
| `data/training/datasets` | Saved local text datasets used by tiny starter training and LoRA jobs |
| `data/training/artifacts` | Tiny local starter model artifacts and metadata |
| `data/training/finetune/jobs` | Fine-tune job ledgers, logs, status, and result metadata |
| `data/training/finetune/adapters` | Local LoRA adapter outputs |
| `data/training/finetune/base-models` | HF-format base model directories used for local LoRA fine-tuning |
| `data/models`, `data/huggingface` | Local model artifacts and Hugging Face-compatible model/cache files |
| `data/search` | Search/content cache and analytics |
| `data/vault.json`, `data/.app_key` | Vault session/config and local encryption key material |
| `data/cleverly-primary-model.json` | Launcher-selected primary Ollama model manifest |

The optional full data-directory snapshot CLI is `scripts/cleverly-backup`.
By default it writes snapshot tarballs under `backups/`; `restore` is
destructive and is not part of the default Backup Verification Plan.
The Command Center backup alert queue only reports coverage gaps, request-route
proof, audit evidence, verification steps, and approval gates; it does not
create exports, restore data, read backup passwords, move files, or run host
commands.

Do not commit `data/`, `logs/`, `.env`, generated backups, exported workspaces,
or Docker volume contents. Sealed Docker volumes are storage isolation, not
encryption; a host or Docker administrator can still inspect them.

## License

Cleverly is source-available under the [Cleverly Product License](LICENSE).
Original upstream and third-party notices are preserved in
[licenses/](licenses/) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
