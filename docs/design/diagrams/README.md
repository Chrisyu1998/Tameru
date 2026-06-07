# Hero diagrams

Presentation-grade [Excalidraw](https://excalidraw.com) source files for the design doc. These complement
the inline Mermaid diagrams in the main docs — Mermaid is for in-flow readability and renders natively on
GitHub; these `.excalidraw` files are the editable "hero" visuals, each making a single argument.

**To view or export:** open a `.excalidraw` file at [excalidraw.com](https://excalidraw.com)
(File → Open) or with the VS Code Excalidraw extension, then export to PNG/SVG. (They're shipped as
editable sources rather than baked PNGs so they stay version-controlled and tweakable.)

| Diagram | Argues | Pairs with |
|---|---|---|
| [`rls-isolation`](./rls-isolation.excalidraw) | Tenant isolation is a *database* property — every user path carries the user's JWT (RLS enforced); only no-JWT system callers use the service role | [trade-off #1](../04-tradeoffs.md#1-rls-via-the-users-jwt-not-the-service-role) |
| [`agent-loop`](./agent-loop.excalidraw) | The agent *proposes*; it never *commits* — a ledger write is always a separate, explicit HTTP call after a user tap | [trade-off #4](../04-tradeoffs.md#4-propose-then-confirm-the-agent-never-commits-a-ledger-row) |

The broad system map lives as Mermaid in the [top-level README](../README.md#the-system-in-one-diagram).
