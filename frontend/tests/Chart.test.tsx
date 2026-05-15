/**
 * Chart rendering test — Day 10b §8.
 *
 * Verifies that each ChartSpec variant the agent can emit renders without
 * throwing and produces the right primitive (line / bar / stacked_bar /
 * donut). Recharts' ResponsiveContainer reads the parent's measured
 * width/height; jsdom reports 0/0 by default, which produces an empty
 * SVG and trips noisy console warnings. We monkey-patch the relevant
 * size APIs to a 375px (iPhone SE) width before each render.
 */

import { describe, expect, test, vi, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Chart } from '@/components/chat/Chart';
import type { ChartSpec } from '@/lib/chat';

beforeAll(() => {
  // Recharts uses ResizeObserver + getBoundingClientRect; both need stubs.
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  // @ts-expect-error — jsdom doesn't ship ResizeObserver
  globalThis.ResizeObserver = ResizeObserverStub;

  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: function () {
      return {
        width: 375,
        height: 220,
        top: 0,
        left: 0,
        right: 375,
        bottom: 220,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      };
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get() {
      return 375;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get() {
      return 220;
    },
  });

  // Silence recharts' "width(0) and height(0) of chart should be greater
  // than 0" warning if jsdom slips past the stubs above.
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

const lineSpec: ChartSpec = {
  type: 'line',
  x: ['Mar W1', 'Mar W2', 'Mar W3', 'Mar W4'],
  series: [{ name: 'Dining', data: [142.0, 211.5, 180.0, 175.0] }],
  y_label: 'USD',
  title: 'dining by week, march',
};

const barSpec: ChartSpec = {
  type: 'bar',
  x: ['Groceries', 'Dining', 'Coffee Shops'],
  series: [{ name: 'March', data: [320.0, 410.0, 78.0] }],
  y_label: 'USD',
  title: 'march totals',
};

const stackedSpec: ChartSpec = {
  type: 'stacked_bar',
  x: ['Jan', 'Feb', 'Mar'],
  series: [
    { name: 'Groceries', data: [240, 260, 320] },
    { name: 'Dining', data: [180, 220, 410] },
  ],
  y_label: 'USD',
  title: 'groceries + dining by month',
};

const donutSpec: ChartSpec = {
  type: 'donut',
  x: ['Groceries', 'Dining', 'Coffee Shops'],
  series: [{ name: 'March', data: [320, 410, 78] }],
  title: 'march share of spend',
};

describe('Chart', () => {
  test('renders a line chart for line specs', () => {
    const { container } = render(<Chart spec={lineSpec} />);
    expect(screen.getByText('dining by week, march')).toBeInTheDocument();
    expect(container.querySelector('[data-chart-type="line"]')).not.toBeNull();
    // Recharts emits a <path class="recharts-curve"> for each line series.
    expect(container.querySelector('.recharts-line')).not.toBeNull();
  });

  test('renders a bar chart for bar specs', () => {
    const { container } = render(<Chart spec={barSpec} />);
    expect(screen.getByText('march totals')).toBeInTheDocument();
    expect(container.querySelector('[data-chart-type="bar"]')).not.toBeNull();
    expect(container.querySelector('.recharts-bar')).not.toBeNull();
  });

  test('renders a stacked bar chart for stacked_bar specs', () => {
    const { container } = render(<Chart spec={stackedSpec} />);
    expect(
      screen.getByText('groceries + dining by month'),
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-chart-type="stacked_bar"]'),
    ).not.toBeNull();
    // Two stacked series → two <Bar> primitives.
    expect(container.querySelectorAll('.recharts-bar').length).toBe(2);
  });

  test('renders a donut chart for donut specs', () => {
    const { container } = render(<Chart spec={donutSpec} />);
    expect(screen.getByText('march share of spend')).toBeInTheDocument();
    expect(container.querySelector('[data-chart-type="donut"]')).not.toBeNull();
    expect(container.querySelector('.recharts-pie')).not.toBeNull();
  });
});
