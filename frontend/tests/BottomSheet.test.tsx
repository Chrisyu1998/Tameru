import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BottomSheet } from '@/components/BottomSheet';

/**
 * Regression: the breakdown / home / cards pages wrap their bodies in a
 * div with `animate-fade-up`, whose keyframes set `transform` with
 * `fill-mode: both`. That makes the wrapper a containing block for
 * `position: fixed` descendants — which used to break the BottomSheet:
 * the modal was clipped to the page wrapper instead of the viewport,
 * with no way to scroll back to fields above the fold (the form was
 * "stuck" — DAY's bug report on Edit Transaction from the category list).
 *
 * The fix is to portal BottomSheet contents to document.body so the
 * fixed positioning is always viewport-anchored. These tests pin that.
 */
describe('BottomSheet', () => {
  it('renders its contents as a child of document.body (portal), not inside the caller subtree', () => {
    const onClose = vi.fn();
    const { container } = render(
      <div data-testid="page-wrapper" style={{ transform: 'translateY(0)' }}>
        <BottomSheet open onClose={onClose} ariaLabel="edit transaction">
          <p>field-merchant</p>
        </BottomSheet>
      </div>,
    );

    // The caller's subtree should NOT contain the sheet content.
    expect(container.querySelector('[aria-label="edit transaction"]')).toBeNull();
    expect(container.textContent ?? '').not.toContain('field-merchant');

    // But the sheet IS in the document — directly under body.
    const dialog = screen.getByRole('dialog', { name: 'edit transaction' });
    expect(dialog.parentElement).toBe(document.body);
  });

  it('renders nothing when open is false (no portal leak)', () => {
    const { container } = render(
      <BottomSheet open={false} onClose={() => undefined} ariaLabel="edit transaction">
        <p>field-merchant</p>
      </BottomSheet>,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('default variant: scrollable inner region has `flex-1 overflow-y-auto` and the parent caps at 85svh', () => {
    render(
      <BottomSheet open onClose={() => undefined} ariaLabel="sheet">
        <p data-testid="child">content</p>
      </BottomSheet>,
    );
    // The child is wrapped in the scroll region — walk up to find it.
    const child = screen.getByTestId('child');
    const scrollRegion = child.parentElement!;
    expect(scrollRegion.className).toMatch(/overflow-y-auto/);
    expect(scrollRegion.className).toMatch(/flex-1/);
    expect(scrollRegion.className).toMatch(/min-h-0/);
    // Parent of the scroll region carries the 85svh cap.
    const sheetBody = scrollRegion.parentElement!;
    expect(sheetBody.className).toMatch(/max-h-\[85svh\]/);
  });

  it('side variant on mobile uses the inner MobileBottomSheet (same scroll contract)', () => {
    render(
      <BottomSheet
        open
        onClose={() => undefined}
        ariaLabel="edit transaction"
        desktopVariant="side"
      >
        <p data-testid="child">content</p>
      </BottomSheet>,
    );
    // Two dialogs exist in the DOM (mobile + desktop), but the mobile
    // one is the one with role=dialog aria-modal=true. Use getAllByRole.
    const dialogs = screen.getAllByRole('dialog', { name: 'edit transaction' });
    expect(dialogs.length).toBeGreaterThan(0);
    // Both scroll regions (mobile + desktop side) must have the
    // overflow/min-h-0 contract so internal scroll works.
    const children = screen.getAllByTestId('child');
    expect(children.length).toBe(2);
    for (const c of children) {
      const scrollRegion = c.parentElement!;
      expect(scrollRegion.className).toMatch(/overflow-y-auto/);
      expect(scrollRegion.className).toMatch(/min-h-0/);
    }
  });
});
