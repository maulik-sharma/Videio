// Line icons on a 24px grid; stroke colour follows the text colour.
function Icon({ children }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export function MenuIcon() {
  return (
    <Icon>
      <path d="M4 6.5h16M4 12h16M4 17.5h16" />
    </Icon>
  );
}

export function HomeIcon() {
  return (
    <Icon>
      <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1v-8.5Z" />
    </Icon>
  );
}

export function LibraryIcon() {
  return (
    <Icon>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.5" />
      <path d="M10.5 9.5v5l4-2.5-4-2.5Z" />
    </Icon>
  );
}

export function UploadIcon() {
  return (
    <Icon>
      <path d="M12 15.5V4.5m0 0L7.5 9M12 4.5 16.5 9M4.5 14.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
    </Icon>
  );
}

export function PlusIcon() {
  return (
    <Icon>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function SignInIcon() {
  return (
    <Icon>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 19.5c1.2-3.3 3.8-5 7-5s5.8 1.7 7 5" />
    </Icon>
  );
}

export function ShieldIcon() {
  return (
    <Icon>
      <path d="M12 3.5 5 6v5.5c0 4.2 2.9 7.7 7 9 4.1-1.3 7-4.8 7-9V6l-7-2.5Z" />
      <path d="m9 12 2 2 4-4" />
    </Icon>
  );
}
