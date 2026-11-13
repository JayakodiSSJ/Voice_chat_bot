import React from "react";

class VoiceChatErrorBoundary extends React.Component {
  state = { hasError: false, errorMessage: "" };

  static getDerivedStateFromError(error) {
    return { hasError: true, errorMessage: error?.message || "Unknown display error" };
  }

  componentDidCatch(error) {
    console.error("Voice chat rendering failed:", error);
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <main
        role="alert"
        style={{
          minHeight: "70vh",
          display: "grid",
          placeContent: "center",
          gap: 12,
          padding: 24,
          color: "#fff",
          textAlign: "center",
          background: "#080d24",
        }}
      >
        <h2>Voice chat could not open</h2>
        <p>The voice screen encountered a display error. Reload it or return to the dashboard.</p>
        <p style={{ maxWidth: 720, color: "#ffb4bd", overflowWrap: "anywhere" }}>Error: {this.state.errorMessage}</p>
        <div>
          <button type="button" onClick={() => window.location.reload()}>
            Reload voice chat
          </button>
          {" "}
          <a href="/dashboard" style={{ color: "#9fc7ff" }}>
            Return to dashboard
          </a>
        </div>
      </main>
    );
  }
}

export default VoiceChatErrorBoundary;