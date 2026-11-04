import { describe, expect, it } from 'vitest';
import { toApiError } from './client.js';

describe('toApiError', () => {
  it('reads the API error shape', () => {
    const error = toApiError(409, {
      error: { code: 'EMAIL_TAKEN', message: 'An account with this email already exists.' },
    });
    expect(error.status).toBe(409);
    expect(error.code).toBe('EMAIL_TAKEN');
    expect(error.message).toBe('An account with this email already exists.');
    expect(error.fields).toEqual({});
  });

  it('keeps field errors for forms', () => {
    const error = toApiError(422, {
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Some fields are invalid.',
        fields: { email: 'bad' },
      },
    });
    expect(error.fields).toEqual({ email: 'bad' });
  });

  it('prefers the Retry-After header for rate limits', () => {
    const headers = new Headers({ 'Retry-After': '42' });
    const error = toApiError(
      429,
      { error: { code: 'RATE_LIMITED', message: 'Slow down', retry_after: 7 } },
      headers,
    );
    expect(error.retryAfter).toBe(42);
  });

  it('falls back to the body when Retry-After is missing', () => {
    const error = toApiError(429, {
      error: { code: 'RATE_LIMITED', message: 'x', retry_after: 7 },
    });
    expect(error.retryAfter).toBe(7);
  });

  it('handles bodies that are not ours, such as a proxy error page', () => {
    const error = toApiError(502, null);
    expect(error.code).toBe('HTTP_502');
    expect(error.message).toMatch(/our side/);
  });

  it('keeps the takedown reason for removed videos', () => {
    const error = toApiError(410, {
      error: { code: 'VIDEO_REMOVED', message: 'Removed.', reason: 'Copyright claim' },
    });
    expect(error.code).toBe('VIDEO_REMOVED');
    expect(error.reason).toBe('Copyright claim');
  });

  it('describes network failures', () => {
    expect(toApiError(0, null).message).toMatch(/reach the server/);
  });
});
