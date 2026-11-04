const VISIBILITY_OPTIONS = [
  ['public', 'Public', 'Shown in the feed.'],
  ['unlisted', 'Unlisted', 'Anyone with the link can watch.'],
  ['private', 'Private', 'Only you can watch.'],
];

export default function VisibilityChoice({ value, onChange, disabled = false, name }) {
  return (
    <fieldset className="field">
      <legend>Visibility</legend>
      <div className="choice-group">
        {VISIBILITY_OPTIONS.map(([option, label, hint]) => (
          <label className="choice" key={option}>
            <input
              type="radio"
              name={name}
              value={option}
              checked={value === option}
              onChange={() => onChange(option)}
              disabled={disabled}
            />
            <span>
              <strong>{label}</strong>
              <span>{hint}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
