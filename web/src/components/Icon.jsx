const shapes = {
  home: <><path d="m3 10 9-7 9 7" /><path d="M5 9v12h5v-7h4v7h5V9" /></>,
  jobs: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M10 8h7M10 12h7M10 16h5" /><path d="M7 8h.01M7 12h.01M7 16h.01" /></>,
  plus: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></>,
  receipt: <><path d="M6 3h9l4 4v14l-3-2-4 2-3-2-3 2V3Z" /><path d="M14 3v5h5M9 11h6M9 15h6" /></>,
  user: <><circle cx="12" cy="7" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2Z" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  coins: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  play: <path d="m8 4 12 8-12 8Z" />,
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  search: <><circle cx="10.5" cy="10.5" r="7" /><path d="m16 16 5 5" /></>,
  arrow: <><path d="M12 3v17m-7-7 7 7 7-7" /></>,
  'arrow-left': <><path d="M20 12H4m6-6-6 6 6 6" /></>,
  refresh: <><path d="M20 7v5h-5" /><path d="M20 12a8 8 0 1 0-2 5M20 7l-3-2" /></>,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6m0 4h.01" /></>,
  file: <><path d="M6 3h8l5 5v13H6Z" /><path d="M14 3v6h5M9 13h7M9 17h5" /></>,
  video: <><rect x="3" y="5" width="12" height="14" rx="2" /><path d="m15 9 6-3v12l-6-3" /></>,
  code: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="m9 8-4 4 4 4m6-8 4 4-4 4m-2-9-2 10" /></>,
  server: <><rect x="3" y="3" width="18" height="7" rx="2" /><rect x="3" y="14" width="18" height="7" rx="2" /><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
};

export default function Icon({ name, size = 20, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}
      aria-hidden="true" focusable="false">
      {shapes[name] ?? shapes.file}
    </svg>
  );
}
