# Ko‘prik v1647 — Design QA

**Source visual truth**

- `/workspace/scratch/ce2e01c62c86/upload/design-comparison.png`
- `/workspace/scratch/ce2e01c62c86/upload/design-comparison-mobile.png`
- `/workspace/scratch/ce2e01c62c86/upload/preview.html`

**Implementation**

- `/workspace/scratch/ce2e01c62c86/work/v1629-compare/new/v1629-source/static/index.html`
- Browser-rendered implementation screenshot: unavailable.

**Viewport and state**

- Intended desktop QA viewport: `1366 × 900`, density `1`.
- Intended mobile QA viewport: `393 × 852`, density `1`.
- States requested for comparison: first-location selection, cabinet login, registration role choice, staff login, ordinary cabinet, business cabinet, public ordinary/specialist/business profiles.
- Source desktop image: `2048 × 768`.
- Source mobile image: `952 × 2048`.
- Density normalization was not applied because the implementation could not be captured.

**Full-view comparison evidence**

- The supplied desktop and mobile reference images were opened and used as the visual source.
- The implementation could not be opened by Cloud Browser: the local preview address returned `ERR_CONNECTION_REFUSED`.
- A browser-rendered full-view comparison therefore could not be produced.

**Focused region comparison evidence**

- Not available. Focused comparisons require a browser-rendered implementation screenshot from the same state and viewport.

**Findings**

- [P1] Browser-rendered visual evidence is missing.
  - Location: all v1647 screens.
  - Evidence: source screenshots are available, but the local implementation could not be reached by Cloud Browser.
  - Impact: typography, spacing, responsive wrapping, colors, asset rendering, and screen overflow cannot be approved visually.
  - Fix: open the deployed v1647 build or a reachable preview URL, capture the listed desktop/mobile states, and compare each capture against the supplied source in one combined comparison image.

**Required fidelity surfaces**

- Fonts and typography: implemented with existing Ko‘prik font tokens; visual verification blocked.
- Spacing and layout rhythm: responsive rules exist at `720px`; visual verification blocked.
- Colors and visual tokens: dark green surfaces, turquoise primary action, muted green borders, and light form controls are implemented; visual verification blocked.
- Image quality and asset fidelity: no new fake image assets were introduced; visual verification blocked.
- Copy and content: existing Uzbek copy, field IDs, and backend-connected content were preserved; visual verification blocked.

**Primary interactions tested**

- Contract tests confirm all existing location, login, Telegram verification, registration, staff login, cabinet, profile, map, story, search, and subscription IDs remain present.
- The full automated suite passed: `258 tests`.
- Browser click, focus, responsive, and console checks could not run because the local preview was unreachable from Cloud Browser.

**Console errors checked**

- Not available because the implementation page could not be opened.

**Comparison history**

- Iteration 1: source files opened; implementation preview connection failed before the first comparable capture.
- No visual fixes were made from screenshot comparison because no valid implementation screenshot was available.

**Implementation checklist**

1. Publish or expose the v1647 preview at a browser-reachable URL.
2. Capture desktop and mobile versions of every requested state.
3. Check typography, spacing, colors, images, copy, overflow, keyboard focus, and console errors.
4. Fix any P0/P1/P2 differences and repeat the comparison.

final result: blocked
