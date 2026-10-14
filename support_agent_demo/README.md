# Support Agent Team demo

A simple agentic AI example for non-technical audiences: a customer complaint handled by a small agent team,
with a loop picker for 4 agent loops (ask and retry, self-check, human feedback, tool retry) and a max-3-tries stop rule.

- `index.html`: the interactive demo. Published as a claude.ai artifact; it uses live Claude when opened
  there and falls back to an offline script anywhere else (open the file in any browser).
- `slides/`: the 3-slide explainer deck source (Slides artifact format).

Demo controls: send a complaint or click an example, click **Next** (or press →) at each hand-off,
approve or reject as the manager when asked.
