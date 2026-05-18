/**
 * ToolAttribution chip — verifies the `via` label map.
 *
 * Regression guard for the "every chip says `calculate_total`" bug:
 * known backend tool names must render their friendly label in plain
 * text (not monospace), and unknown names fall through to the raw
 * snake_case identifier in monospace so a future backend tool still
 * surfaces something useful before the frontend ships an updated map.
 */

import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolAttribution } from '@/components/chat/MessageBubble';

describe('ToolAttribution', () => {
  test('renders the friendly label for get_spending_summary', () => {
    render(<ToolAttribution name="get_spending_summary" />);
    expect(screen.getByText('via', { exact: false }).textContent).toContain(
      'spending summary',
    );
    // The label is NOT wrapped in font-mono — only unknown tool names are.
    expect(screen.queryByText('get_spending_summary')).toBeNull();
  });

  test('renders the friendly label for render_chart', () => {
    render(<ToolAttribution name="render_chart" />);
    expect(screen.getByText('via', { exact: false }).textContent).toContain(
      'chart',
    );
    expect(screen.queryByText('render_chart')).toBeNull();
  });

  test('renders the friendly label for calculate_total', () => {
    render(<ToolAttribution name="calculate_total" />);
    expect(screen.getByText('via', { exact: false }).textContent).toContain(
      'total',
    );
  });

  test('falls back to the raw snake_case for an unknown tool', () => {
    render(<ToolAttribution name="future_unknown_tool" />);
    const span = screen.getByText('future_unknown_tool');
    expect(span).toBeInTheDocument();
    expect(span.className).toContain('font-mono');
  });
});
