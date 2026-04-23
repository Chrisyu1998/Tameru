/*
 * Splash screen. Day 21 replaces this with the real first-launch philosophy
 * screen (DESIGN.md §5.4.1). Today it exists to verify the Tailwind theme
 * and font loading on the root route.
 */
export function Splash() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <div className="flex flex-col items-center gap-3">
        <h1 className="font-display text-5xl text-primary">tameru</h1>
        <p className="font-sans text-sm text-tertiary">
          spending intelligence, quietly.
        </p>
      </div>
    </main>
  );
}
