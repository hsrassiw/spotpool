export default function Logo() {
  return (
    <span className="brand">
      <svg className="brand-mark" viewBox="0 0 40 44" aria-hidden="true" focusable="false">
        <path d="m20 2 17 7-17 7L3 9Z" fill="currentColor" />
        <path d="m3 12 15 6v10L3 22Zm19 6 15-6v10l-15 6ZM3 25l15 6v11L3 36Zm19 6 15-6v11l-15 6Z" fill="currentColor" />
        <path d="m8 18 4 2m-4 11 4 2m15-13 4-2m-4 15 4-2" stroke="white" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <span>SpotPool</span>
    </span>
  );
}
