import { describe, expect, it } from 'vitest';
import { formatBytes, formatDuration, formatRelative, formatViews } from './format.js';

describe('formatDuration', () => {
  it('formats minutes and hours', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(75.4)).toBe('1:15');
    expect(formatDuration(599.6)).toBe('10:00');
    expect(formatDuration(3725)).toBe('1:02:05');
  });

  it('returns null when the length is unknown', () => {
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
  });
});

describe('formatViews', () => {
  it('uses the singular for one view and compacts large counts', () => {
    expect(formatViews(0)).toBe('0 views');
    expect(formatViews(1)).toBe('1 view');
    expect(formatViews(999)).toBe('999 views');
    expect(formatViews(1234)).toBe('1.2K views');
    expect(formatViews(2_500_000)).toBe('2.5M views');
  });
});

describe('formatRelative', () => {
  const now = Date.parse('2026-11-03T12:00:00Z');
  const ago = (seconds) => new Date(now - seconds * 1000).toISOString();

  it('describes how long ago something happened', () => {
    expect(formatRelative(ago(10), now)).toBe('just now');
    expect(formatRelative(ago(5 * 60), now)).toBe('5 minutes ago');
    expect(formatRelative(ago(3 * 3600), now)).toBe('3 hours ago');
    expect(formatRelative(ago(24 * 3600), now)).toBe('yesterday');
    expect(formatRelative(ago(15 * 24 * 3600), now)).toBe('2 weeks ago');
    expect(formatRelative(ago(400 * 24 * 3600), now)).toBe('last year');
  });
});

describe('formatBytes', () => {
  it('picks a readable unit', () => {
    expect(formatBytes(200)).toBe('1 KB');
    expect(formatBytes(45_000)).toBe('45 KB');
    expect(formatBytes(12_345_678)).toBe('12.3 MB');
    expect(formatBytes(1_500_000_000)).toBe('1.50 GB');
  });
});
