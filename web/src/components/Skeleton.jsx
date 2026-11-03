export default function CardSkeletons({ count = 8 }) {
  return (
    <div className="video-grid" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div className="card-skeleton" key={index}>
          <div className="skeleton skeleton-thumb" />
          <div className="skeleton skeleton-line" />
          <div className="skeleton skeleton-line skeleton-short" />
        </div>
      ))}
    </div>
  );
}
