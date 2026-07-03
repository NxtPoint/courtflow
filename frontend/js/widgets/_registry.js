// widgets/_registry.js — the shared widget namespace (FRONTEND-STANDARDISATION.md).
// Loaded before the individual widget files in every app shell. Each widget attaches itself to
// window.Widgets.<Name> and exposes a single mount(host, cfg) -> { refresh, destroy }.
window.Widgets = window.Widgets || {};
