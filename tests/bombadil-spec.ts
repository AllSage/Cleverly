/**
 * Bombadil spec for Cleverly UI.
 */
import { extract, always, eventually, now, actions } from "@antithesishq/bombadil";
export * from "@antithesishq/bombadil/defaults";

const runtimeEnv = ((globalThis as any).process && (globalThis as any).process.env) || {};
const TEST_USERNAME = runtimeEnv.CLEVERLY_BOMBADIL_USERNAME || "cleverly-demo";
const TEST_PASSWORD = runtimeEnv.CLEVERLY_BOMBADIL_PASSWORD || "cleverly-demo-password";

// Extractors. This is the only place the spec reads the DOM.

const onLoginPage = extract((state) => {
  return state.document.querySelector("#username") !== null;
});

const loginElements = extract((state) => {
  const user = state.document.querySelector("#username") as HTMLElement | null;
  const pass = state.document.querySelector("#password") as HTMLElement | null;
  const btn = state.document.querySelector('button[type="submit"]') as HTMLElement | null;
  if (!user || !pass || !btn) return null;
  const ur = user.getBoundingClientRect();
  const pr = pass.getBoundingClientRect();
  const br = btn.getBoundingClientRect();
  return {
    user: { x: ur.left + ur.width / 2, y: ur.top + ur.height / 2 },
    pass: { x: pr.left + pr.width / 2, y: pr.top + pr.height / 2 },
    btn: { x: br.left + br.width / 2, y: br.top + br.height / 2 },
  };
});

const chatInput = extract((state) => {
  const el = state.document.querySelector("#message") as HTMLElement | null;
  if (!el || (el as any).offsetParent === null) return null;
  const rect = el.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, disabled: (el as any).disabled };
});

const pageHasContent = extract((state) => {
  return state.document.body && state.document.body.children.length > 0;
});

const visibleModals = extract((state) => {
  let count = 0;
  state.document.querySelectorAll(".modal").forEach((m: any) => {
    if (!m.classList.contains("hidden") && m.offsetParent !== null) count++;
  });
  return count;
});

const commandCenterState = extract((state) => {
  if (state.document.querySelector("#username") !== null) return null;
  const root = state.document.querySelector("#command-center") as HTMLElement | null;
  const chatContainer = state.document.querySelector("#chat-container") as HTMLElement | null;
  if (!root || !chatContainer) return null;
  const rect = chatContainer.getBoundingClientRect();
  const readinessCards = Array.from(state.document.querySelectorAll("#cc-command-readiness-deck [data-state]")) as HTMLElement[];
  const targetCards = Array.from(state.document.querySelectorAll(".command-center-targets [data-state]")) as HTMLElement[];
  const targetSummary = (state.document.querySelector("#cc-targets-summary")?.textContent || "").trim();
  return {
    ready: (state.document.body as HTMLElement).dataset.cleverlyCommandCenterReady || "",
    top: rect.top,
    horizontalOverflow: state.document.documentElement.scrollWidth > state.document.documentElement.clientWidth + 1,
    readinessCount: readinessCards.length,
    targetCount: targetCards.length,
    loadingReadiness: readinessCards.filter((el) => el.dataset.state === "loading").length,
    loadingTargets: targetCards.filter((el) => el.dataset.state === "loading").length,
    targetSummary,
  };
});

const clickableElements = extract((state) => {
  const els: { name: string; x: number; y: number }[] = [];
  const selectors = "button:not([disabled]),.list-item,.icon-rail-btn,.section-header-flex,.send-btn,.sidebar-brand,input[type=checkbox]";
  state.document.querySelectorAll(selectors).forEach((el: any) => {
    if (el.offsetParent === null) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const name = el.id || el.tagName;
    els.push({ name, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  });
  return els;
});

// Login actions.

export const login = actions(() => {
  const le = loginElements.current;
  if (!le) return [];
  return [
    { Click: { name: "username", point: le.user } },
    { TypeText: { text: TEST_USERNAME, delayMillis: 30 } },
    { Click: { name: "password", point: le.pass } },
    { TypeText: { text: TEST_PASSWORD, delayMillis: 30 } },
    { Click: { name: "submit", point: le.btn } },
  ];
});

// App exploration.

export const explore = actions(() => {
  if (onLoginPage.current) return [];
  const acts: any[] = [];

  const els = clickableElements.current || [];
  for (const el of els) {
    acts.push({ Click: { name: el.name, point: { x: el.x, y: el.y } } });
  }

  const input = chatInput.current;
  if (input && !input.disabled) {
    acts.push({ Click: { name: "chat-input", point: { x: input.x, y: input.y } } });
    acts.push({ TypeText: { text: "hello", delayMillis: 50 } });
    acts.push({ PressKey: { code: 13 } });
  }

  acts.push({ ScrollDown: { origin: { x: 512, y: 400 }, distance: 300 } });
  acts.push({ ScrollUp: { origin: { x: 512, y: 400 }, distance: 300 } });
  acts.push("Wait");

  return acts;
});

// Properties.

export const noBlankPage = always(() => pageHasContent.current === true);
export const noModalStacking = always(() => (visibleModals.current || 0) <= 2);
export const chatInputAppears = always(
  now(() => onLoginPage.current === false).implies(
    eventually(() => chatInput.current !== null).within(10, "seconds")
  )
);
export const commandCenterBecomesReady = always(
  now(() => onLoginPage.current === false).implies(
    eventually(() => {
      const cc = commandCenterState.current;
      return cc !== null && cc.ready === "ready" && cc.readinessCount >= 8 && cc.targetCount >= 9;
    }).within(15, "seconds")
  )
);
export const commandCenterDoesNotStayLoading = always(() => {
  const cc = commandCenterState.current;
  return cc === null || cc.ready !== "ready" || (cc.loadingReadiness === 0 && cc.loadingTargets === 0);
});
export const commandCenterHasResponsiveClearance = always(() => {
  const cc = commandCenterState.current;
  return cc === null || (cc.top >= 0 && cc.horizontalOverflow === false);
});
export const commandCenterTargetRoutesVisible = always(
  now(() => onLoginPage.current === false).implies(
    eventually(() => {
      const cc = commandCenterState.current;
      return cc !== null && cc.targetSummary.includes("9/9 route-ready");
    }).within(15, "seconds")
  )
);
