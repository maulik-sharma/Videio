import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';
import { useLogin, useMe, useRegister } from '../auth/session.js';

// Friendlier wording for the API's field validation messages.
const FIELD_HINTS = {
  email: 'Enter a valid email address.',
  password: 'Use 8 to 128 characters.',
  display_name: 'Enter a display name, up to 50 characters.',
};

// Only follow same-site paths after login, never `//evil.example` or full URLs.
function safeNext(value) {
  return value && value.startsWith('/') && !value.startsWith('//') ? value : '/';
}

function formError(error) {
  if (!error) return null;
  if (error.code === 'RATE_LIMITED' && error.retryAfter) {
    return `Too many attempts. Try again in ${error.retryAfter} seconds.`;
  }
  if (error.code === 'VALIDATION_ERROR') return null; // shown next to each field
  return error.message;
}

function Field({ id, label, error, hint, ...input }) {
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...input}
      />
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="field-hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export default function AuthPage({ mode }) {
  const isRegister = mode === 'register';
  const [params] = useSearchParams();
  const next = safeNext(params.get('next'));
  const navigate = useNavigate();
  const { data: me } = useMe();
  const login = useLogin();
  const register = useRegister();
  const mutation = isRegister ? register : login;

  const [form, setForm] = useState({ email: '', password: '', displayName: '' });
  const update = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  if (me) return <Navigate to={next} replace />;

  const fields = mutation.error?.fields ?? {};
  const fieldError = (name) => (fields[name] ? FIELD_HINTS[name] : null);
  const message = formError(mutation.error);

  const submit = (event) => {
    event.preventDefault();
    mutation.mutate(form, { onSuccess: () => navigate(next, { replace: true }) });
  };

  return (
    <main className="page page-narrow">
      <h1>{isRegister ? 'Create an account' : 'Log in'}</h1>
      <p className="muted">
        {isRegister ? 'Already have an account? ' : 'No account yet? '}
        <Link to={{ pathname: isRegister ? '/login' : '/register', search: params.toString() }}>
          {isRegister ? 'Log in' : 'Create one'}
        </Link>
      </p>

      <form className="form" onSubmit={submit} noValidate>
        {message ? (
          <p className="form-error" role="alert">
            {message}
          </p>
        ) : null}
        {isRegister ? (
          <Field
            id="display_name"
            label="Display name"
            autoComplete="nickname"
            value={form.displayName}
            onChange={update('displayName')}
            error={fieldError('display_name')}
            hint="Shown next to your videos."
          />
        ) : null}
        <Field
          id="email"
          label="Email"
          type="email"
          autoComplete="email"
          value={form.email}
          onChange={update('email')}
          error={fieldError('email')}
        />
        <Field
          id="password"
          label="Password"
          type="password"
          autoComplete={isRegister ? 'new-password' : 'current-password'}
          value={form.password}
          onChange={update('password')}
          error={fieldError('password')}
          hint={isRegister ? 'At least 8 characters.' : undefined}
        />
        <button type="submit" className="button button-primary" disabled={mutation.isPending}>
          {mutation.isPending
            ? isRegister
              ? 'Creating account…'
              : 'Logging in…'
            : isRegister
              ? 'Create account'
              : 'Log in'}
        </button>
      </form>
    </main>
  );
}
