# One Complaint, Six Ways

A single-page, scripted demo that shows what each layer of an AI setup adds, using one customer complaint. The same complaint is run six times and one layer is switched on each time:

| Step | Layer switched on | What the audience sees |
|---|---|---|
| Assistant | Engage | Fluent answer, but guessed numbers and a promised discount |
| Build it | Build (no-code) | A team lead describes a job in plain words and gets an agent |
| + Agent | Build | The agent acts in the systems: wrong credit, discount given away |
| + Team | Build (orchestration) | A coordinator and three agents close the case fast, but on guesses |
| + Company data | Contextualize | Right numbers with sources, still no limits |
| + Agent IDs | Govern, part 1 | Each agent has its own ID and rights; the discount is denied |
| + Live checks | Govern, part 2 | €1,500 credit waits for a person's approval; full log |
| Side by side | — | Comparison of the six runs |

The main screen uses neutral words. **Under the hood** has three views: *Off*, *Tech* (generic technical terms) and *SAP* (the matching SAP product for each layer and step). Use *SAP* only after the SAP concept has been revealed.

It is a simulation: no AI model, no network calls, no real systems. Every answer, amount and timing (for example "41 seconds", "≈ 45 min") is scripted and illustrative.

## Run it

Open `index.html` in a recent browser (Chrome, Edge, Firefox, Safari). No server or build step. It works offline; without internet the page falls back from Google Fonts to system fonts.

At 1280 px or wider everything sits side by side. Below about 980 px the layer stack moves above the case; below about 720 px the conversation and the work log stack too.

The file is an HTML fragment without a doctype, because the Artifact publish wraps it in its own document. Opened directly from disk it renders in quirks mode; the tests pass in both modes.

## Present it

- **Story** mode: `Next →` / `← Back`, or the arrow keys. Each step runs automatically; `▶ Run` replays it and `Show all at once` skips the animation.
- **Build it**: click `▶ Describe the job`, then `Create agent`, then `Watch it work →`.
- **+ Live checks**: click `Approve €1,500` (or `Reject`) while the agents wait.
- **Free play**: switch any layer on or off in the left column, or use the preset mixes. "Safe but stuck" (rules without data) and "Checks, no IDs" are good for questions.
- Reload the page to reset everything.

## How it is built

One self-contained file, `index.html`: inline CSS and JavaScript, no libraries.

- `SCENES` holds the story steps and their layer settings: `agents` (0, 1 or 2), `data`, `ids`, `checks`.
- `steps(config)` returns the conversation and work-log entries for any mix of layers (24 combinations).
- `score(config, approved)` computes the four outcome tiles; the recap table reuses it.
- `run()` plays the steps with delays and pauses at the approval step. Each new run cancels the previous one.
- `sapTag()` derives the SAP view labels from each entry's actor.

The case data (invoice, contract, delivery note, rules) is in the case file panel and in `steps()`; change both together.

## Test it

`tests/e2e.mjs` drives the page in Chromium with Playwright. It covers every story step and the recap values, all 24 layer mixes in free play (expected outcome tiles and invariants such as "discount is denied exactly when agent IDs are on"), approve and reject (work log, email, chat, tile, recap), the three hood views, keyboard navigation, fast clicking, layout at 400 / 768 / 1280 px and on a phone, dark theme and, optionally, an axe accessibility audit.

```sh
PLAYWRIGHT_MODULE=$(npm root -g)/playwright/index.mjs node ai-layers-demo/tests/e2e.mjs
# optional: CHROMIUM_PATH=/path/to/chromium  AXE_PATH=/path/to/axe-core/axe.min.js
```

Playwright and axe-core are not repository dependencies; install them globally or point the variables at an existing copy.

## Limitations

- Scripted only: it shows a concept and is not connected to, and does not represent the performance of, any product.
- The SAP view names are a simplified mapping made for a presentation; check them against current SAP material before using them externally.
- Company names, people and e-mail addresses are fictional (`.example` domains).
- The only network request is Google Fonts, which tells Google the viewer's IP address. Remove the `<link>` tags in the head to avoid it.
- Run updates are not announced to screen readers (no live region). Screen-reader use has not been tested.
- Only Chromium is covered by the tests.
