export default function App() {
  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="LedgerPilot home">
          <span className="brand-mark" aria-hidden="true">L</span>
          <span>LedgerPilot</span>
        </a>
        <span className="environment">Local development</span>
      </header>
      <section className="welcome" aria-labelledby="welcome-title">
        <p className="eyebrow">Finance office</p>
        <h1 id="welcome-title">Your books, ready for review.</h1>
        <p className="description">
          The web workspace is initialized. LedgerPilot keeps financial records clear,
          reviewable, and grounded in your accounting rules.
        </p>
        <div className="status" role="status">
          <span className="status-dot" />
          Development shell is running
        </div>
      </section>
      <footer>LedgerPilot · Responsive web application</footer>
    </main>
  );
}
