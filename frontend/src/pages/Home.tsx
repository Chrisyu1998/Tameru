/*
 * Home (dashboard) placeholder. Day 15 implements the real minimal-dashboard
 * (DESIGN.md §6.2) — one headline delta, 4–5 delta tiles, one chat prompt,
 * no scrolling. This placeholder exists only so the route resolves today.
 */
export function Home() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <p className="font-sans text-sm text-secondary">
        home — dashboard lands day 15.
      </p>
    </main>
  );
}
