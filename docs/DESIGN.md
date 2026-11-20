# SAGE — Design & Screen Reference (React Native handoff)

This document is the design source of truth for re-implementing the SAGE web app as a
React Native app. It covers the visual language, every component, every screen, the
theme/language system, and the places where a native app must behave differently.

Companion document: [`API.md`](./API.md) — every endpoint, payload, and TypeScript example.

| | |
| --- | --- |
| Product name | SAGE (Smart Assistance, Guidance & Ease) |
| Web stack | Flask + Jinja templates + Tailwind CDN + plain-IIFE JS (no build step) |
| App shell | Max width **880px**, centered, sticky top bar + sticky composer |
| Breakpoints used | `sm` = 640px, `lg` = 1024px (Tailwind defaults) |
| Languages | English (`en`) and Arabic (`ar`), full RTL |
| Theme | Light + dark, class-based, follows the OS until the user chooses |

---

## 1. Product tone

SAGE is a **personal assistant with memory**, not a generic chatbot. The persona
documents (`prompts/english.md`, `prompts/arabic.md`, plus the `*-call.md`
variants) define the behavior the UI must not contradict:

- **Adapt to the person, not the person to the system.**
- Warm, patient, concise, non-judgmental. Short paragraphs, not walls of text.
- Non-clinical emotional support: no diagnosis, no treatment claims, no dependency
  building. Crisis language escalates to emergency services / trusted people.
- **Never invent memories.** It retrieves from the user's memory store and says when
  it doesn't know.
- Privacy first: the UI never shows another user's data, never shows coordinates the
  server didn't authorize, and never implies it knows where the user is unless a
  familiar place matched.

Six functional pillars drive the screens:

1. **Chat** with streaming replies and tool activity.
2. **Voice call mode** — spoken replies, microphone input, Egyptian-colloquial Arabic.
3. **Personal memory** — people, relationships, facts; user-owned and delete-able.
4. **Schedule** — appointments, routines, familiar places.
5. **Location context** — familiar-place awareness from periodic location pings.
6. **Trusted people** — mutual, consent-gated visibility of last seen and location.

Copy rules that show up in the UI: neutral phrasing for unfamiliar locations (never
"you seem lost"), "unknown" rather than "stale" for contacts who were never active,
and the words *your account* / *your memory* rather than *our data*.

---

## 2. Design tokens

### 2.1 Colour

Brand (primary, deep green):

| Token | Hex | | Token | Hex |
| --- | --- | --- | --- | --- |
| `brand.50` | `#eef5f1` | | `brand.600` | `#3b665b` |
| `brand.100` | `#dbe5e0` | | `brand.700` | `#1c4f45` |
| `brand.200` | `#b9ede0` | | `brand.800` | `#143f36` |
| `brand.300` | `#9ed1c4` | | `brand.900` | `#0f3d33` |
| `brand.400` | `#7ca89b` | | `brand.950` | `#00261e` |
| `brand.500` | `#5b8f7a` | | | |

Sand (neutrals):

| Token | Hex | Typical use |
| --- | --- | --- |
| `sand.50` | `#faf9f5` | page background (light) |
| `sand.100` | `#f5f4f0` | chips, inert surfaces |
| `sand.200` | `#e3e2df` | borders, dividers |
| `sand.300` | `#d8d6d1` | scrollbar thumb, stronger borders |
| `sand.400` | `#b6b4ae` | secondary text |
| `sand.500` | `#8a8883` | meta text, placeholders |

Dark mode surfaces:

| Purpose | Hex |
| --- | --- |
| Page background | `#0b1512` (root style override `#020617` before Tailwind paints) |
| App shell / top bar / composer | `#141d1b` |
| Card / section | `#1a2320` |
| Auth card | `#111b17` |
| Input field | `#16211d` with `#2f3835` border |

Semantic colours:

| Meaning | Light | Dark |
| --- | --- | --- |
| Error text | `#b91c1c` on `#fef2f2` (border `#fecaca`) | `#fca5a5` on `#2a1414` (border `#7f1d1d`) |
| Success / info | `brand.800` on `brand.50` (border `brand.200`) | `brand.100` on `brand.900/40` |
| Warning banner (voice) | `#8a6d1a` on `#fef9e7` (border `#f3e3b3`) | `#f0d98a` on `#2b2412` (border `#5a4a1d`) |
| Destructive button | `#b91c1c` text, `#fef2f2` bg, `#fecaca` border | `#fca5a5` text, red-900/25 bg |
| Hang up (call) | `#b91c1c` filled, white text | `#dc2626` filled |

Chat bubble colours:

| Bubble | Light | Dark |
| --- | --- | --- |
| Assistant | bg `#e8f2ec`, text `#1b2a24` | bg `#1a2320`, text `#eef2ef` |
| User | bg `#0f3d33`, text `#ffffff` | bg `#b9ede0`, text `#00261e` |
| Error | bg `#fef2f2`, text `#b91c1c` | bg `#2a1414`, text `#fca5a5` |

Brand gradients: avatar and call orb are
`linear-gradient(135deg, #0f3d33 0%, #5b8f7a 100%)`.

### 2.2 Typography

| Family | Weights | Used for |
| --- | --- | --- |
| **Inter** | 400, 500, 600 | body, labels, buttons, chat text |
| **Plus Jakarta Sans** | 600, 700, 800 | headings, the `SAGE` wordmark |
| **Noto Sans Arabic** | 400, 500, 600, 700 | swapped in for **all** text when the language is Arabic |
| **Material Symbols Outlined** | opsz 20–48, wght 300–600 | every icon (ligature names, e.g. `spa`, `event`) |

Type ramp actually used:

| Element | Size / weight / notes |
| --- | --- |
| SAGE wordmark | 15px, 800, letter-spacing `0.14em` (0.18em on auth pages) |
| Auth hero (brand panel) | 32px → 36px at `lg`, 800, line-height 1.15 |
| Auth page title | 24px → 28px at `sm`, 800 |
| Section (card) title | 18px, 700 |
| Chat/thread text | 15px, line-height 1.65 |
| Call transcript text | 14.4px (`0.9rem`), line-height 1.6 |
| Buttons | 14px (`btn-primary`), 13px (`btn-secondary`/`btn-danger`) |
| Labels (auth forms) | 12px, 600, uppercase, letter-spacing `0.04em` |
| Meta / timestamps | 12px (`chat-time`), 11px (status line, footer) |
| Badges / consent pills | 10–12px, 600 |
| Card hints | 14px (`text-sm`) |

Arabic note: `body.lang-ar` swaps the font stack for body/headline/display/label and
raises the line-height to **1.75**. Name the Arabic font explicitly — do not rely on
the system fallback.

### 2.3 Radius, shadow, spacing

| Token | Value |
| --- | --- |
| Input / button radius | 12px |
| Bubble radius | 16px (assistant: bottom-start 4px; user: bottom-end 4px) |
| Call transcript bubble | 14px |
| Card / section radius | 16px (`rounded-2xl`) |
| Auth card radius | 28px |
| Pills, avatars, icon buttons | full (9999px) |
| Card shadow | `0 30px 60px -30px rgba(15,61,51,0.22), 0 2px 6px rgba(15,61,51,0.05)` |
| App shell shadow | `0 0 60px -24px rgba(15,61,51,0.25)` |
| Button shadow | `0 1px 2px rgba(15,61,51,0.18)` |

Spacing follows Tailwind's 4px scale: cards use 20px padding (24px at `sm`), the
thread uses 24px vertical gaps, and icon rows use 8–12px gaps.

### 2.4 A ready-to-use theme object

```ts
// theme.ts — direct port of the tokens above.
export const palette = {
  brand: {
    50: '#eef5f1', 100: '#dbe5e0', 200: '#b9ede0', 300: '#9ed1c4',
    400: '#7ca89b', 500: '#5b8f7a', 600: '#3b665b', 700: '#1c4f45',
    800: '#143f36', 900: '#0f3d33', 950: '#00261e',
  },
  sand: {
    50: '#faf9f5', 100: '#f5f4f0', 200: '#e3e2df',
    300: '#d8d6d1', 400: '#b6b4ae', 500: '#8a8883',
  },
} as const;

export const lightTheme = {
  page: palette.sand[50],
  surface: '#ffffff',
  card: '#ffffff',
  cardBorder: 'rgba(227,226,223,0.8)',   // sand-200/80
  border: palette.sand[200],
  text: palette.brand[950],
  textMuted: palette.brand[600],
  textMeta: palette.sand[500],
  input: '#ffffff',
  inputBorder: palette.sand[200],
  focus: palette.brand[600],
  bubbleAssistant: '#e8f2ec',
  bubbleAssistantText: '#1b2a24',
  bubbleUser: palette.brand[900],
  bubbleUserText: '#ffffff',
  bubbleError: '#fef2f2',
  bubbleErrorText: '#b91c1c',
  dangerBorder: '#fecaca',
  warningBg: '#fef9e7',
  warningText: '#8a6d1a',
  warningBorder: '#f3e3b3',
  navChip: palette.sand[100],
  navChipActive: palette.sand[200],
} as const;

export const darkTheme = {
  page: '#0b1512',
  surface: '#141d1b',
  card: '#1a2320',
  cardBorder: 'rgba(255,255,255,0.05)',
  border: 'rgba(255,255,255,0.1)',
  text: palette.sand[100],
  textMuted: palette.sand[400],
  textMeta: '#8a938e',
  input: '#16211d',
  inputBorder: '#2f3835',
  focus: palette.brand[300],
  bubbleAssistant: '#1a2320',
  bubbleAssistantText: '#eef2ef',
  bubbleUser: '#b9ede0',
  bubbleUserText: '#00261e',
  bubbleError: '#2a1414',
  bubbleErrorText: '#fca5a5',
  dangerBorder: '#7f1d1d',
  warningBg: '#2b2412',
  warningText: '#f0d98a',
  warningBorder: '#5a4a1d',
  navChip: 'rgba(255,255,255,0.05)',
  navChipActive: 'rgba(255,255,255,0.1)',
} as const;

export const radius = { input: 12, bubble: 16, callBubble: 14, card: 16, authCard: 28 } as const;
export const fonts = {
  body: 'Inter',
  heading: 'PlusJakartaSans',
  arabic: 'NotoSansArabic',
} as const;
```

---

## 3. Layout system

### 3.1 App shell (chat, call, and all content pages)

- Centered column, `max-width: 880px`, full height, white (light) / `#141d1b` (dark),
  1px `sand-200/80` side borders, soft outer shadow.
- Height is the **visible** viewport: the web uses `100dvh` (falling back to `100vh`)
  so the composer is never hidden behind mobile browser chrome.
- Three vertical regions: **sticky header**, **scrolling main**, **sticky footer
  (composer only on `/chat` and `/call`)**.

### 3.2 Top bar

Desktop / `lg` and wider — single 56px row:

```
[ spa SAGE  (Personal memory) ]        [ greeting  log out | Trusted Schedule Memory Call | EN theme avatar ]
```

Mobile and tablet (`< 1024px`) — the nav links move to a **second row** inside the
same header, because the bar cannot fit them below ~820px:

```
row 1:  [ spa SAGE ]                              [ log out  EN  theme  avatar ]
row 2:  [ Trusted ] [ Schedule ] [ Memory ] [ Call ]     ← 4 equal columns
```

| Region | Behaviour |
| --- | --- |
| Wordmark | `spa` icon in a 32px `brand-900` rounded-xl tile + `SAGE`, tracking 0.14em |
| Context badge | `Personal memory` pill (chat) / `Voice call` pill (call), `sm` and up only |
| Greeting | Server-rendered name, `hidden` below `sm`; truncates to 160px |
| Log out | Text link at `sm`+; 36px circular icon button below `sm` |
| Nav pills | `Trusted`, `Schedule`, `Memory`, `Call` — inline pills at `lg`+, tab row below |
| Language | `language` icon + `EN`/`AR` code; code hidden below `sm` on `/call` |
| Theme | 32px circular icon button; sun/moon swapped by the theme controller |
| Avatar | 32px circle with the first initial, links to `/settings` |

Nav tab row (below `lg`): 4 equal columns, 48px tall on phones (icon over 10px label,
`rounded-xl`), switching to inline pills (36px, icon + 12px label, `rounded-full`,
1px border) from `sm`. Tap targets stay ≥ 44px on phones.

### 3.3 Content pages (memory, schedule, trusted, settings)

Same shell, no composer. Main is `px-4 py-6` (24px at `sm`) with 20px vertical gaps
between **section cards** (`rounded-2xl`, 1px border, 20/24px padding, title 18px +
14px hint).

Header for these pages: back-arrow link → 32px `spa` tile → `SAGE`, and on the right
the theme toggle (memory/schedule/trusted) or language + theme (settings).

### 3.4 Auth pages (`/login`, `/register`)

- `min-h-screen`, centered, 16/32px page padding.
- One card (`max-w-1080px` / `1120px`), 28px radius, split in two at `md`:
  a brand panel (dark `brand-900`, blurred `brand-400/20` and `brand-200/10` blobs,
  hero headline, 3 feature bullets, "Your data stays in your account.") and a form panel.
- Below `md` the brand panel is hidden and a compact logo row appears above the title,
  so the form is the whole screen.
- A fixed 36px theme toggle sits at the top-right of the viewport on both pages.

### 3.5 Mobile-specific rules (already applied on web, keep in native)

- Composer keeps clear of the home indicator: bottom padding
  `0.75rem + env(safe-area-inset-bottom)`.
- Inputs use **16px** text on phones so mobile browsers stop zooming on focus.
- The thread and the call transcript are the only scroll regions; the page itself
  never scrolls (no accidental body scroll behind a fixed composer).
- No horizontal scrolling anywhere: verified at 320, 360, 375, 390, 414, 640, 768,
  820, 1024, 1280 and 1440px, in both languages.

---

## 4. Components

### 4.1 Buttons

| Variant | Spec | Use |
| --- | --- | --- |
| `btn-primary` | full width, 12px radius, 0.7/1.25rem padding, `brand-900` bg, white 14px/600 text, success/`brand-800` hover, `brand-950` active with 0.5px press | form submits, `Start call`, `Add appointment` (auto width from `sm`) |
| `btn-secondary` | inline-flex, min-height 40px, 12px radius, white bg, `sand-200` border, `brand-700` text, 13px/600 | `Export` |
| `btn-danger` | same shape, `#fef2f2` bg, `#fecaca` border, `#b91c1c` text | `Forget everything` |
| Icon button | 32px circle, `sand-100` bg, 1px border, `brand-600` icon | theme, language, avatar, back |
| Row action | text-only, 24px tall on desktop, **32px on phones**, `rounded-lg` | `Remove`, `Mark done`, `On`/`Off` |
| Hang up | filled `#b91c1c`, white text, 12px radius | call |
| Mic | white/`sand-100`; `aria-pressed=true` inverts to `brand-900` + white | call |

Disabled state: 40% opacity + not-allowed cursor (primary), 60% opacity (secondary).
All buttons have a visible focus ring: `0 0 0 3px rgba(59,102,91,0.30)`.

### 4.2 Fields

```
LABEL            ← 12px, 600, uppercase, 0.04em, #57635e (light) / #a7b1ac (dark)
[ icon  input ]  ← 12px radius, 1px border, 0.625/0.875rem padding, 14px text (16px on phones)
hint text        ← 12px, brand-500 / sand-400
```

- With-icon variant adds 2.5rem start padding for a material icon (mail, lock, lock_reset).
- Focus: border `brand-600` (dark: `brand-300`) + 3px `brand-600/15` ring
  (dark: `brand-300/18`).
- Invalid input shows a server message in the form-level alert, not inline.
- `datetime-local` (`When`) has **no timezone**: the client converts it with
  `new Date(value).toISOString()` before POSTing.

### 4.3 Alerts

| Purpose | Style | ARIA |
| --- | --- | --- |
| Form / section error | red-50 bg, red-200 border, red-700 text, 12px radius | `role="alert"` |
| Success | `brand-50` bg, `brand-200` border, `brand-800` text | `role="status"` |
| Voice unavailable banner | warning yellow above | `role="status" aria-live="polite"` |
| Place context / reminder | full-width band under the header | `role="status"` |

### 4.4 List row (memory, schedule, trusted)

```
┌─────────────────────────────────────────────────────┐
│ Rounded-xl border, 14px 10px padding                │
│ Title (14px, wraps)                  [ Remove ]      │
│ Sub-meta (12px, sand-500)                            │
└─────────────────────────────────────────────────────┘
```

- Text **wraps** (`break-words`), never truncates: long fact values, place names and
  emails must stay readable on a phone.
- Actions are right-aligned and `shrink-0`.
- Deleting always asks a confirm first and shows a success alert on completion.

### 4.5 Consent toggle (memory page)

A row: label on the left, a pill button on the right that reads **On** (granted,
`brand-100`/`brand-800`) or **Off** (not granted, `sand-100`/`brand-600`). Tapping
`PUT`s the new value and re-renders from the response. Explanatory hint above the
list: *"Turn these off at any time. Nothing is shared without your permission."*

Kinds shown: **Location** (`location`), **Voice replies** (`voice`),
**Share memory with trusted people** (`memory_sharing`), **Familiar voice**
(`familiar_voice`), **Share my activity with trusted people** (`trusted_sharing`).

> Only `location` and `trusted_sharing` are enforced by the server today; the other
> three are stored switches and are not yet gating anything.

### 4.6 Badges

- Status pills for trusted contacts: **pending**, **accepted**, **revoked**.
- Activity badges from `GET /api/trusted/people/<id>`: **active**, **stale**, **unknown**.
- Header pill on chat (`Personal memory`) and call (`Voice call`).
- Sharing state on the trusted page: **sharing on** / **sharing off**, and per-contact
  **sharing paused** when the owner's `trusted_sharing` consent is off.

### 4.7 Chat thread

| Piece | Spec |
| --- | --- |
| Row | `max-width: 85%`, 12px gap; user rows right-aligned, assistant left-aligned |
| Avatar | 32px gradient circle, `spa` icon at 16px, assistant rows only; 2px top offset |
| Bubble | 12/16px padding, **18px radius**, 15px/1.65 text, `white-space: pre-wrap`, `overflow-wrap: anywhere`, 4px squared corner on the sender's side; assistant bubbles carry a 1px hairline border, user bubbles a 160° gradient (`#145243 → #0f3d33`, pale mint in dark mode) |
| Meta | 12px timestamp + hover/tap delete button; user meta right-aligned |
| Delete button | trash icon; **always visible on touch devices** (hover media query) |
| Typing indicator | three 6px dots bouncing, 1.2s loop (disabled under reduced motion) |
| Streaming caret | inline `▍` appended via `.is-streaming`, blinking 1s |
| Tool activity | one transient line under the bubble: *Saving memory…*, *Searching memory…*, *Memory saved*, *Memory searched*, *Memory tool failed*, *Using tool…*, *Checking your account…*, *Account checked*. Tool **names/status only** — never arguments or results. Clears when answer text starts, or expires 1.4s after a tool ends. |
| Welcome bubble | assistant bubble with a personalized greeting; hidden once the thread is non-empty |
| Starter chips | three pill buttons under the greeting (*What's on today?*, *Remember that...*, *What do you remember about me?*). Tapping **fills the composer** — it never auto-sends — and they hide once the thread has a message |
| Jump-to-latest | 30px pill above the composer, visible only while the reader is scrolled up. Tap scrolls smoothly to the newest message |
| Error bubble | red bubble replacing an answer that failed |

Empty thread = the welcome bubble plus the starter chips. Long threads scroll inside
the thread region.

**Scroll policy:** streaming follows the reply only while the reader is within ~120px of
the end; otherwise the view stays put and the jump pill appears. Sending a message
always jumps to the newest bubble. Scrolls are instant for streamed deltas and smooth
only for the explicit jump — never animate every token.

### 4.8 Composer (chat and call)

```
┌───────────────────────────────────────────────────────────┐
│ [ 🧽 Clear ]  [ textarea … ]              [ ⏹ ] [ ↑ ]     │
└───────────────────────────────────────────────────────────┘
   status dot + status text (11px, brand-400)
```

- Container: 16px radius, `sand-50/60` bg, 1px border, focus-within border `brand-500`
  + 2px `brand-600/15` ring, 10px padding.
- Textarea autosizes from 1 row up to **144px**, no visible scrollbar; Enter sends,
  Shift+Enter inserts a newline; call-screen placeholder is *"Type instead of speaking…"*.
- `Clear` (chat only) = clear the whole conversation; label hidden below `sm`. It asks
  for confirmation in the shared dialog, whose copy notes that saved memories stay.
- **One action slot:** while a reply streams, `Send` hides and `Stop` takes its place;
  when the turn ends the swap reverses. Two competing buttons never show at once.
- Send button is a 36px filled circle with a 150° brand gradient; disabled at 40% opacity;
  hovering lifts it 1px with a soft glow.
- Status line shows *Thinking…* while busy and clears afterwards.

### 4.9 Call screen

```
        [ gradient orb 104px ]      ← pulses while listening / speaking
        Voice call with SAGE
        Speak naturally, or type if you prefer.
        [ yellow banner when voice is unavailable ]
        [ Start call ] [ Hang up ] [ Mute ]
        Ready when you are.
        ── transcript ──
        YOU      → right-aligned bubble
        SAGE     → left-aligned bubble
```

- Orb: 104px gradient circle with `spa` at 36px; `listening` pulse 1.6s, `speaking`
  pulse 1.1s (ring expanding 0 → 18px, fading). Disabled under reduced motion.
- Buttons appear progressively: `Start call` → `Hang up` + `Mute` once the call is live.
- Transcript bubbles mirror the chat bubbles (assistant tinted, user filled) with a
  small uppercase speaker label; a pending (not yet spoken) bubble is 65% opacity.
- The voice banner explains *why* voice is off: not configured, token expired, mic
  denied, no mic, unsupported language, network loss, or a browser that blocks the
  speech service. Callers can always type.

### 4.10 Motion

One timing scale for the whole app (CSS custom properties in `static/style.css`):

| Token | Value | Use |
| --- | --- | --- |
| `--dur-fast` | 150ms | hover, press, alert dismissal |
| `--dur-base` | 240ms | rows, banners, dialogs |
| `--dur-slow` | 420ms | page entrance |
| `--ease-standard` | `cubic-bezier(.2, .8, .2, 1)` | everything default |
| `--ease-pop` | `cubic-bezier(.2, .9, .25, 1)` | dialogs, auth card |

Named effects:

| Class | Effect | Where |
| --- | --- | --- |
| `.anim-rise` | fade + 8px lift | list rows, empty states |
| `.anim-slide-down` | fade + 6px drop | alerts, banners |
| `.anim-pop` | scale-in | dialog card, auth card |
| `.page-enter` | 420ms fade | `<main>` of content pages |
| `.tap-style` | `active:scale(.97)` | chips, tabs |

Lists with `.stagger-list` animate their rows 30ms apart, capped at 6 rows so long lists
stay snappy. Only `transform` and `opacity` are animated.

**Reduced motion is a hard requirement:** one `prefers-reduced-motion` block disables
every entrance animation, the stagger delays, the skeleton shimmer, the typing dots,
the stream caret, the call-orb pulse, the jump-pill transition and smooth scrolling. Any
new animation must be added to that block.

### 4.11 Confirmation dialog

Replaces `window.confirm` everywhere (clear conversation, delete memory item, forget
everything, remove appointment/routine/place, revoke a trusted contact).

- Centered card, max 22rem, 20px radius, `pop-in` entrance; backdrop `rgba(2,15,12,.45)`
  with a 2px blur and a 120ms fade.
- Title 16px/700; body 14px/1.6 (omitted when the copy is the question itself).
- Actions: `Cancel` (`btn-secondary`) then the destructive action (`btn-danger`).
- **Focus starts on Cancel for destructive actions** and on Confirm otherwise; Escape and
  the backdrop cancel; Tab is trapped inside; focus returns to the trigger.
- Implemented once in `static/ui.js` as `THUI.confirm({title, message, confirmLabel,
  cancelLabel})` → `Promise<boolean>`. The copy comes from each page's `STRINGS` map.

### 4.12 Loading and feedback

- **Skeletons:** content pages show 2 shimmering placeholder rows per list while the
  first fetch is in flight (`.skeleton-row` + `.skeleton-line`, 1.4s shimmer), so the
  page never flashes "Nothing saved yet" before data arrives. Removed on error too.
- **Success toasts:** success alerts slide in, then fade out on their own after **4.5s**
  (`THUI.showSuccess`), instead of sitting there until the next action. Errors stay until
  dismissed by the next action.
- **Press feedback:** every tappable row/chip/tab uses `.tap-style` so touch users see a
  state change, not just hover users.

---

## 5. Screens

Legend: **auth** = requires a bearer token for its API calls (the HTML shell itself is
public and data-free). All screens are full-height; "sections" are cards.

### 5.1 `/` — redirect

No UI. The web redirects to `/login`; `/login` forwards to `/chat` when a valid token
exists. In native: decide at boot — valid stored token → Chat, otherwise → Login.

### 5.2 `/login` — **auth page**

- Brand panel (`md`+): `SAGE`, *Smart Assistance, Guidance & Ease.*, description, three
  bullets (Remembers what matters / Speaks English & Arabic / Private by design),
  footer *"Your data stays in your account."*
- Form: **Email** (mail icon), **Password** (lock icon), `Log in` with an arrow, footer
  link *New to SAGE? Create an account*, and *SAGE • AI Assistant*.
- Behaviour: client validates ≥ 8 chars and matching passwords, disables the button
  (*Please wait…*), posts, stores the session, then replaces the screen with Chat.
  Server errors appear in the alert above the form.
- Endpoint: `POST /api/auth/login`.

### 5.3 `/register` — **auth page**

- Brand panel: *A calmer place for what matters.* + "You choose what to save /
  Delete anything, anytime / Arabic & English, side by side".
- Form: **First name** + **Last name** (2 columns at `sm`+), **Email**, **Password**
  (`At least 8 characters`), **Confirm password**, `Create account`.
- Limits: names ≤ 60 chars, email ≤ 254, password 8–128.
- Endpoint: `POST /api/auth/register`. Success signs the user in immediately.

### 5.4 `/chat` — **the home screen**

Order of layers: app bar → section tabs (`<lg`) → today's-appointment banner →
familiar-place banner → thread → composer → status line.

| Behaviour | Detail |
| --- | --- |
| Load | `GET /api/conversations/current` renders the thread from the database |
| Send | Optimistically render the user bubble, stub the assistant bubble with the typing dots, then `POST /api/chat/stream` with `{conversation_id, message, language}` |
| Streaming | Append `delta` text; render `reasoning` separately if you show it; show tool activity; on `done` stop the caret |
| Ids | Adopt `saved.{user_turn_id, assistant_turn_id}` so delete works **without refetching**; fall back to a reload only if `saved` is missing |
| Stop | Send `POST /api/chat/stop {id: X-Stream-ID}`, then abort the local request; roll back **both** bubbles and restore the text to the composer. A stopped turn is deleted server-side |
| Disconnect | Never a rollback: the server keeps the user turn and the partial reply |
| Delete a message | `DELETE /api/conversations/<conversation_id>/turns/<turn_id>`; deleting a user turn also removes the assistant reply that followed it |
| Clear | `DELETE /api/conversations` (clears **all** threads) — always through the confirmation dialog |
| Banners | Appointment banner from `GET /api/appointments/today?tz_offset=`; place banner from the location ping (below) |
| Greeting | `Hi, <first name>` in the header; the welcome bubble uses *Welcome, <name>! Ask me anything to get started.* |
| Empty state | Welcome bubble + three starter chips that fill the composer; both disappear once a message exists |
| Reading while streaming | The reply must not pull the view down. Follow only within ~120px of the end; otherwise show the jump pill |
| Composer state | Send ↔ Stop swap in one slot; typing stays enabled while a reply streams |

Location ping (chat and call only): every **5s**, `POST /api/location` with the current
coordinates, skipping a tick while a request is in flight, pausing when the app is
backgrounded, and stopping permanently on permission denial. `204` = stored, no familiar
place; `200` = also render `context.place.name` (plus the next appointment time).
`403 consent_required` = the user turned location off — hide the banner and stop pinging.

Also on this screen: a call entry point (`/call`) and the four section tabs. The web
also links `Chat` → `/call`; native should keep a visible "voice call" affordance in
the chat header.

### 5.5 `/call` — voice call mode

- Same header, plus a back arrow on phones; badge reads **Voice call**.
- On open: `GET /api/voice/status` → if not `available`, show the banner and keep typing.
- `Start call` unlocks playback, starts recognition (`ar-EG` for Arabic, `en-US`
  otherwise), and opens a turn with `{mode: "call", greeting: true}`.
- Assistant text is split into segments around tool calls and each segment is sent to
  `POST /api/voice/speak` (MP3) while more text streams; recognition pauses during
  playback so the microphone doesn't hear SAGE's own voice.
- `Mute` toggles recognition; `Hang up` aborts the stream, stops recognition and audio,
  and calls `/api/chat/stop`.
- Transcript shows `You` / `SAGE` turns; the status line reports *Listening…*,
  *Thinking…*, *Speaking…*, *Microphone muted.*, *Call ended.*
- Speech-recognition errors are explained, not swallowed: denied permission, no device,
  unsupported language, and network loss each get their own message. Fatal errors stop
  retrying and explain that typing still works.

### 5.6 `/memory` — what SAGE remembers

Sections:

1. **What SAGE remembers** — *Everything saved about you. You can remove any item, or
   export a copy.* Actions: `Export` (downloads JSON), `Forget everything` (`DELETE
   /api/memory` with `{"confirm": true}`; returns counts).
   Three lists with no data = *Nothing saved yet.*
   - **People & things** — entities (`name`, `entity_type`)
   - **Facts** — `predicate: value` + entity name, `· expired` marker for superseded facts
   - **Relationships** — `source → target (type)`
   Every row has `Remove`; the entity remove cascades to its facts and relationships.
2. **Permissions** — the consent toggle list described in §4.5.

Privacy copy that matters: deleting is a **hard delete**, and the page promises exactly
that. Superseded facts are shown (greyed via the `expired` marker) rather than hidden.

### 5.7 `/schedule` — appointments, routines, places

| Section | Content | Actions |
| --- | --- | --- |
| **Today** | *Appointments SAGE knows about for your local day.* | list from `GET /api/appointments/today?tz_offset=` |
| **Upcoming** | Soonest first, done/cancelled hidden | `Mark done`, `Remove`; plus the add form: **Title**, **When** (`datetime-local`), **Place (optional)** |
| **Routines** | *Repeating parts of your day.* | list with `Remove` |
| **Familiar places** | *Save where you are now, and SAGE can show what you noted about it.* | list with `Remove`; form: **Place name**, **Radius (metres, default 150, 10–50000)** |

Filled states use a green success alert (*Appointment added.* / *Place saved.*); empty
states use the small centered muted line for each list. All timestamps are UTC in
storage and rendered in local time (`toLocaleString`).

### 5.8 `/trusted` — people who can see you, and people you can see

1. **Share my activity** — a header row with a switch (`sharing on` / `sharing off`) and
   the explanation that while it's off, people you invited can see the invitation but
   not your last seen or location.
2. **People I trust** — invites you sent (any status) with `Remove` (revoke). Invite form:
   **Their email**, checkboxes **Last seen** and **Last location** (both on by default),
   `Send invitation`. Adding an address with no account still succeeds (by design — the
   response must not reveal whether the address exists).
3. **Invitations for me** — pending invites addressed to your email with `Accept` /
   `Decline`.
4. **People who trust me** — owners who accepted you. Render every accepted contact,
   including ones whose owner has sharing off, as **sharing paused** — never hide them.
   Expanding one (`View activity`) calls `GET /api/trusted/people/<id>` and shows last
   seen, a location line when permitted, and a warning line when the contact has been
   inactive for 24h. A `403` means the gates closed: show an error, not an empty card.

Status copy: **unknown** when the person has never been active (never "stale"),
**stale** past 24h, **active** otherwise. The warning is a prompt to check in, never a
claim that something is wrong.

### 5.9 `/settings` — account

1. **Profile** — *The name SAGE uses with you, and the email you sign in with.*
   First name, Last name, Email, plus **Current password** (revealed only when the email
   is edited). `Save profile` → `PATCH /api/auth/profile`, which returns a **new token**
   that must replace the stored one.
2. **Password** — *Use at least 8 characters. Other signed-in devices stay signed in.*
   Current password, New password, Confirm new password, `Update password`.

Header also carries the language toggle here. Success alerts per section
(*Profile saved.* / *Password updated.*); password mismatch is caught client-side.

---

## 6. Theme and language

### 6.1 Theme

- Storage key `th_theme` with values `dark` | `light`. No value = follow the OS
  (`prefers-color-scheme`) and keep following it when the OS changes.
- The web applies the theme **before first paint** (a `<head>` script) to avoid a flash;
  in native, resolve the theme before rendering the first screen.
- Toggling sets the class on the root element *and* `colorScheme` (native form
  controls/scrollbars) plus a background fill of `#f8fafc` (light) / `#020617` (dark).
- Every toggle button declares its action: *Switch to light mode* / *Switch to dark mode*.

### 6.2 Language

- Storage key `th_language` with values `en` | `ar`. Accepted aliases on the wire:
  `en|eng|english`, `ar|ara|arabic` (anything else falls back to `en`).
- Arabic sets `dir="rtl"` on the document, swaps the font stack (§2.2), and raises the
  line-height to 1.75. Bubbles carry `dir="auto"` so mixed content still reads correctly.
- The server forces the reply language per request (`language` field), so the chat
  cannot answer in the wrong language even if the user types in the other one.
- **Call mode Arabic is Egyptian colloquial** (`ar-EG`), not MSA or Gulf; the speech
  recognizer must use `ar-EG` as well.
- Strings live in each JS module's `STRINGS` map; the key inventory per screen is in
  §10. Time formatting always uses the device locale (`toLocaleTimeString`).

---

## 7. Accessibility

- Errors use `role="alert"`; successes and banners use `role="status"`.
- The chat thread is `aria-live="polite"`; the call transcript has its own live region
  labelled *Call transcript*.
- Icon-only controls always carry `aria-label` + `title` (theme, language, avatar,
  back, send, stop, delete, mic).
- The mic button uses `aria-pressed`; consent and sharing toggles read their state in
  text (*On* / *Off*, *sharing on* / *sharing off*).
- Focus rings are always visible (3px at 25–30% alpha).
- Touch targets: 44–48px for primary actions and the mobile tab row; list-row actions
  grow to 32px on phones.
- Motion respects `prefers-reduced-motion`: entrance animations, typing dots, the
  streaming caret, and the call-orb pulse all switch off.
- Contrast: body text is `brand-950`/`sand-100`; muted text never goes lighter than
  `sand-500` for content that must be read.

---

## 8. Native porting notes

### 8.1 What maps cleanly

| Web | React Native |
| --- | --- |
| `app-shell` column + sticky regions | `SafeAreaView` + `View` header / `FlatList` or `ScrollView` body / `KeyboardAvoidingView` footer |
| Tailwind classes | the theme object in §2.4 + `StyleSheet` (or NativeWind if you prefer utility classes) |
| `100dvh` | `flex: 1` inside `SafeAreaView` (no viewport-unit problem on native) |
| `env(safe-area-inset-bottom)` | `useSafeAreaInsets()` padding on the composer |
| `window.confirm` | `Alert.alert` with destructive styling — copy is already written per action |
| hover-revealed delete button | always visible on touch (the web already does this under `@media (hover: none)`) |
| Material Symbols font | any icon set (`@expo/vector-icons`) — keep the same glyph meanings |

### 8.2 What must change

| Web behaviour | Native replacement |
| --- | --- |
| `localStorage` for `th_token` / `th_expires_at` / `th_user` | `expo-secure-store` for the token and expiry; AsyncStorage is acceptable for `th_user` |
| `localStorage` for `th_theme` / `th_language` | AsyncStorage (non-secret preferences) |
| `fetch` + `response.body.getReader()` for SSE | `react-native-sse`, or XHR `onprogress`, or a small native SSE module. The frame format is plain `data: {json}\n\n` lines and a literal `data: [DONE]` terminator |
| `X-Stream-ID` response header | must be captured from the streaming response — it is the **only** way to stop a generation |
| `AbortController` on the stream | abort the request *after* the stop POST returns; a stopped turn is rolled back and the server deletes the row pair |
| `navigator.geolocation.getCurrentPosition` every 5s | `expo-location` `getCurrentPositionAsync` on a 5s interval, paused in `AppState` background, permanently stopped on `denied` (matches the web) |
| `SpeechRecognition` (Chrome) | a native STT module (`expo-speech-recognition` / platform APIs) with `ar-EG` for Arabic, or server-side transcription. Recognition must pause while audio plays |
| `<audio>` playback + silent-clip unlock | `expo-av` / `expo-audio`; `POST /api/voice/speak` returns `audio/mpeg` bytes to write to a file and play |
| `datetime-local` + `new Date(value).toISOString()` | `@react-native-community/datetimepicker`, then send ISO-8601 UTC |
| `-new Date().getTimezoneOffset()` | same computation for `tz_offset` (minutes **east** of UTC) |
| Pre-paint theme script | read the stored theme (or `useColorScheme()`) before the first render |
| `THUI.confirm` (styled dialog) | `Alert.alert` with a destructive button, or a `Modal` that mirrors §4.11 — keep focus on Cancel for destructive actions |
| `THUI.showSuccess` + CSS transitions | a toast/snackbar with a 4.5s timer, animated with `Animated`/Reanimated |
| `.stagger-list` + `anim-*` classes | `Animated`/Reanimated entering animations with the same durations and easings (§4.10) |
| `prefers-reduced-motion` media query | `AccessibilityInfo.isReduceMotionEnabled()` — gate every entering animation and the smooth scroll |

### 8.3 Suggested navigation

```
Root
├── (auth)  Login, Register           — no token
└── (app)   Chat  ← home              — token required
            Call (modal / push)       — voice mode, needs mic permission
            Memory, Schedule, Trusted — reachable from the chat tab row
            Settings                  — from the avatar
```

Keep the chat screen as the app's home so the four section tabs mirror the web tab row.
Chat and Call are the only screens that ping location; both need the mic permission
prompt only in Call mode.

### 8.4 Porting checklist

- [ ] Session: token + `expires_at` stored securely; on `401` clear the session and go to Login.
- [ ] Boot: valid token → Chat, else Login (`/` behaviour).
- [ ] Chat sends **only** `{conversation_id, message, language}` — never a client-side history.
- [ ] Adopt `saved` ids instead of refetching after every reply.
- [ ] Stop = POST `/api/chat/stop` with the `X-Stream-ID`, then abort; roll back both bubbles.
- [ ] Delete a user message deletes the assistant reply that followed it.
- [ ] `Clear` uses `DELETE /api/conversations` (all threads).
- [ ] Location ping: 5s, skip while in flight, pause in background, stop on denial,
      `403` = consent off (hide the banner, stop pinging).
- [ ] `GET /api/location/context` never returns coordinates — don't expect them.
- [ ] `POST /api/memory`-style destructive calls need `{"confirm": true}` for `/api/memory`.
- [ ] Profile email change requires `current_password` and returns a **new token** — persist it.
- [ ] Arabic: RTL layout, Arabic font, `ar-EG` for calls, and Egyptian-colloquial copy.
- [ ] Trusted contacts: render accepted contacts with sharing off as "sharing paused";
      a never-active contact is **unknown**, not stale.
- [ ] Voice: check `/api/voice/status` before enabling call mode; keep typing available.
- [ ] Never log or persist coordinates, tokens, voice text, or chat text on the device.

---

## 9. Web-only details (do not port)

- Tailwind CDN, the pre-paint theme script, and the Material Symbols webfont.
- `static/welcome.js` is unreferenced leftover code.
- The HTML page shells are public and data-free: all data arrives through the API with
  the bearer token. Replicate that boundary — the native app should never render
  anything from a cached response as if it were fresh.

---

## 10. String inventory

All user-facing copy lives in `STRINGS` maps inside these modules (each has `en` and
`ar`). Use this as the checklist when lifting copy into your i18n layer.

| Module | Screen | Keys |
| --- | --- | --- |
| `static/chat.js` | Chat | `code, greeting, welcome, logout, clear, placeholder, placeholderShort, thinking, noResponse, stop, toggleLang, deleteReply, deleteOne, toolSaving, toolSearching, toolSaved, toolFound, toolFailed, toolGeneric, toolAccount, toolAccountDone, loadFailed, deleteFailed, clearFailed, jumpLatest, clearTitle, clearBody, clearConfirm, cancel, suggest1, suggest2, suggest3` |
| `static/call.js` | Call | `code, badge, title, subtitle, start, hangup, mute, unmute, ready, connecting, listening, thinking, speaking, muted, ended, logout, placeholder, you, sage, voiceUnavailable, voiceExpired, micDenied, micUnavailable, micLanguage, micNetwork, micNetworkOffline, micBraveBlocked, noSpeech, audioBlocked, error, requestFailed` |
| `static/memory-page.js` | Memory | `memoryTitle, memoryHint, export, deleteAll, empty, entities, facts, relationships, consentTitle, consentHint, delete, confirmDelete, confirmDeleteAll, cancel, deletedOne, deletedAll, exported, emptyValue, consentLocation, consentVoice, consentMemorySharing, consentFamiliarVoice, consentTrustedSharing, on, off, genericError, loadFailed` |
| `static/schedule-page.js` | Schedule | `todayTitle, todayHint, todayEmpty, upcomingTitle, upcomingEmpty, titleLabel, whenLabel, placeLabel, add, routinesTitle, routinesHint, routinesEmpty, placesTitle, placesHint, placesEmpty, placeNameLabel, radiusLabel, saveHere, placeSaved, confirmRemovePlace, locationOff, locationDenied, done, remove, confirmRemove, confirmRemoveRoutine, cancel, added, genericError, loadFailed, required` |
| `static/trusted-page.js` | Trusted | `trustedTitle, trustedHint, trustedEmpty, emailLabel, allowLastSeen, allowLocation, sendInvite, invitesTitle, invitesEmpty, watchingTitle, watchingEmpty, sharingTitle, sharingHint, sharingOn, sharingOff, notSharing, paused, pending, accepted, revoked, revoke, accept, decline, see, hide, confirmRevoke, cancel, inviteSent, acceptedMsg, declinedMsg, revokedMsg, lastSeen, neverSeen, warning, location, locationHidden, staleBadge, activeBadge, unknownBadge, genericError, loadFailed` |
| `static/settings.js` | Settings | `pageTitle, profileTitle, profileHint, firstName, lastName, email, currentPassword, currentPasswordHint, saveProfile, passwordTitle, passwordHint, newPassword, confirmPassword, savePassword, profileSaved, passwordSaved, passwordMismatch, genericError, busy, toggleLang` |
| `static/location.js` | banners | `at, next, off` |
| `static/schedule-reminder.js` | banner | `next, at, today` |
| `static/auth.js` | Login / Register | inline English only: `Please wait...`, `Password must be at least 8 characters.`, `The two passwords do not match.`, `Request failed (<status>)` |
| `templates/*.html` | static chrome | headings, hints and button labels are in the markup with `data-i18n` keys where they are translated |

> The prompts (`prompts/*.md`) are **not** UI copy — they are the model persona and are
> injected server-side. Keep them out of the i18n layer.
