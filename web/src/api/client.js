// Thin fetch wrapper for the Videio API.
// - Sends the CSRF header the API requires on every mutating request.
// - Cookies carry the session (same origin), so there is no token handling here.
// - On a 401, refreshes the session once and retries, sharing one refresh
//   between concurrent requests so the refresh token is only rotated once.

export class ApiError extends Error {
  constructor({ status, code, message, fields = {}, retryAfter = null }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.retryAfter = retryAfter;
  }
}

// Turn any failed response into an ApiError, whether or not the body has our shape.
export function toApiError(status, body, headers = new Headers()) {
  const error = body && typeof body === 'object' ? body.error : null;
  const retryHeader = Number.parseInt(headers.get('Retry-After') ?? '', 10);
  return new ApiError({
    status,
    code: error?.code ?? 'HTTP_' + status,
    message: error?.message ?? defaultMessage(status),
    fields: error?.fields ?? {},
    retryAfter: Number.isFinite(retryHeader) ? retryHeader : (error?.retry_after ?? null),
  });
}

function defaultMessage(status) {
  if (status === 0) return 'Could not reach the server. Check your connection and try again.';
  if (status >= 500) return 'Something went wrong on our side. Try again in a moment.';
  return 'Request failed (HTTP ' + status + ').';
}

let refreshing = null;

function refreshSession() {
  refreshing ??= request('/api/auth/refresh', { method: 'POST' })
    .then(() => true)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

async function request(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') headers['X-Requested-With'] = 'videio';
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
  } catch {
    throw toApiError(0, null);
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw toApiError(response.status, data, response.headers);
  return data;
}

export async function api(path, options) {
  try {
    return await request(path, options);
  } catch (error) {
    const canRefresh = error.status === 401 && !path.startsWith('/api/auth/');
    if (!canRefresh || !(await refreshSession())) throw error;
    return request(path, options);
  }
}
