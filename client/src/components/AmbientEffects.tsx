export function AmbientEffects() {
  return (
    <>
      <div className="noise-overlay" />
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
        {[10, 25, 45, 65, 80, 92].map((left, i) => (
          <span
            key={i}
            className="particle"
            style={{
              left: `${left}%`,
              animation: `particleFloat ${16 + i * 3}s linear infinite ${i * 2}s`,
            }}
          />
        ))}
      </div>
    </>
  );
}
